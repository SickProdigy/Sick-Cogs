<?php
declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('Referrer-Policy: no-referrer');
header('X-Content-Type-Options: nosniff');

function recovery_error(string $code, string $message, int $status): void
{
    http_response_code($status);
    echo json_encode(['error' => ['code' => $code, 'message' => $message]]);
    exit;
}

function recovery_secret(): string
{
    $secret = (string) getenv('SICKWALLET_RECOVERY_RELAY_SECRET');
    if (strlen($secret) < 32 || strlen($secret) > 512) {
        throw new RuntimeException('Recovery relay secret is unavailable.');
    }
    return $secret;
}

function recovery_database(): PDO
{
    $dsn = (string) getenv('SICKWALLET_DATABASE_DSN');
    $user = (string) getenv('SICKWALLET_DATABASE_USER');
    $password = (string) getenv('SICKWALLET_DATABASE_PASSWORD');
    if ($dsn === '' || !str_starts_with($dsn, 'mysql:')) {
        throw new RuntimeException('Recovery database is unavailable.');
    }
    return new PDO($dsn, $user, $password, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_EMULATE_PREPARES => false,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
}

function recovery_json_body(): array
{
    $raw = (string) file_get_contents('php://input');
    if ($raw === '' || strlen($raw) > 32768) {
        recovery_error('invalid_request', 'The recovery request is invalid.', 400);
    }
    try {
        $body = json_decode($raw, true, 16, JSON_THROW_ON_ERROR);
    } catch (Throwable) {
        recovery_error('invalid_request', 'The recovery request is invalid.', 400);
    }
    if (!is_array($body)) {
        recovery_error('invalid_request', 'The recovery request is invalid.', 400);
    }
    return [$body, $raw];
}

function recovery_verify_registration(PDO $database, string $raw, string $secret): void
{
    $timestamp = (string) ($_SERVER['HTTP_X_SICKWALLET_TIMESTAMP'] ?? '');
    $nonce = (string) ($_SERVER['HTTP_X_SICKWALLET_NONCE'] ?? '');
    $signature = (string) ($_SERVER['HTTP_X_SICKWALLET_SIGNATURE'] ?? '');
    if (!ctype_digit($timestamp) || abs(time() - (int) $timestamp) > 300
        || !preg_match('/^[A-Za-z0-9_-]{24,128}$/D', $nonce)
        || !preg_match('/^[a-f0-9]{64}$/D', $signature)) {
        recovery_error('authentication_failed', 'Relay authentication failed.', 401);
    }
    $canonical = implode("\n", [
        'v1', $timestamp, $nonce, 'POST', '/api/recovery-handoff.php', hash('sha256', $raw),
    ]);
    $expected = hash_hmac('sha256', $canonical, $secret);
    if (!hash_equals($expected, $signature)) {
        recovery_error('authentication_failed', 'Relay authentication failed.', 401);
    }
    $nonceDigest = hash('sha256', $nonce);
    try {
        $statement = $database->prepare(
            'INSERT INTO sickwallet_relay_nonces (nonce_digest, created_at) VALUES (?, ?)'
        );
        $statement->execute([$nonceDigest, time()]);
    } catch (PDOException $error) {
        if ((string) $error->getCode() === '23000') {
            recovery_error('authentication_failed', 'Relay authentication failed.', 401);
        }
        throw $error;
    }
}

function recovery_encrypt(string $jwt, string $secret): array
{
    $nonce = random_bytes(12);
    $tag = '';
    $ciphertext = openssl_encrypt(
        $jwt, 'aes-256-gcm', recovery_encryption_key($secret),
        OPENSSL_RAW_DATA, $nonce, $tag, 'sickwallet-recovery-v1', 16
    );
    if ($ciphertext === false || strlen($tag) !== 16) {
        throw new RuntimeException('Recovery encryption failed.');
    }
    return [$ciphertext, $nonce, $tag];
}

function recovery_decrypt(array $row, string $secret): string
{
    $jwt = openssl_decrypt(
        $row['ciphertext'], 'aes-256-gcm', recovery_encryption_key($secret),
        OPENSSL_RAW_DATA, $row['cipher_nonce'], $row['cipher_tag'],
        'sickwallet-recovery-v1'
    );
    if (!is_string($jwt) || $jwt === '') {
        throw new RuntimeException('Recovery decryption failed.');
    }
    return $jwt;
}

function recovery_encryption_key(string $secret): string
{
    return hash_hmac('sha256', 'sickwallet-recovery-encryption-v1', $secret, true);
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    header('Allow: POST');
    recovery_error('method_not_allowed', 'POST is required.', 405);
}

try {
    [$body, $raw] = recovery_json_body();
    $database = recovery_database();
    $secret = recovery_secret();
    $operation = (string) ($body['operation'] ?? '');
    if ($operation === 'register') {
        recovery_verify_registration($database, $raw, $secret);
        $digest = (string) ($body['handoff_digest'] ?? '');
        $jwt = (string) ($body['jwt'] ?? '');
        $expiresAt = (int) ($body['expires_at'] ?? 0);
        $now = time();
        if (!preg_match('/^[a-f0-9]{64}$/D', $digest)
            || $jwt === '' || strlen($jwt) > 16384
            || $expiresAt <= $now || $expiresAt > $now + 300) {
            recovery_error('invalid_request', 'The recovery registration is invalid.', 400);
        }
        [$ciphertext, $cipherNonce, $cipherTag] = recovery_encrypt($jwt, $secret);
        $statement = $database->prepare(
            'INSERT INTO sickwallet_recovery_handoffs '
            . '(handoff_digest, ciphertext, cipher_nonce, cipher_tag, expires_at, created_at) '
            . 'VALUES (?, ?, ?, ?, ?, ?)'
        );
        $statement->execute([
            $digest, $ciphertext, $cipherNonce, $cipherTag, $expiresAt, $now,
        ]);
        $database->prepare(
            'DELETE FROM sickwallet_relay_nonces WHERE created_at < ?'
        )->execute([$now - 600]);
        $database->prepare(
            'DELETE FROM sickwallet_recovery_handoffs WHERE expires_at < ?'
        )->execute([$now - 86400]);
        http_response_code(201);
        echo json_encode(['status' => 'registered']);
        exit;
    }
    if ($operation === 'consume') {
        $handle = (string) ($body['handoff'] ?? '');
        if (!preg_match('/^[A-Za-z0-9_-]{32,128}$/D', $handle)) {
            recovery_error('handoff_unavailable', 'This recovery link is invalid, expired, or already used.', 410);
        }
        $digest = hash('sha256', $handle);
        $database->beginTransaction();
        $statement = $database->prepare(
            'SELECT ciphertext, cipher_nonce, cipher_tag, expires_at, consumed_at '
            . 'FROM sickwallet_recovery_handoffs WHERE handoff_digest = ? FOR UPDATE'
        );
        $statement->execute([$digest]);
        $row = $statement->fetch();
        if (!$row || $row['consumed_at'] !== null || (int) $row['expires_at'] <= time()) {
            $database->rollBack();
            recovery_error('handoff_unavailable', 'This recovery link is invalid, expired, or already used.', 410);
        }
        $jwt = recovery_decrypt($row, $secret);
        $database->prepare(
            'UPDATE sickwallet_recovery_handoffs SET consumed_at = ? WHERE handoff_digest = ?'
        )->execute([time(), $digest]);
        $database->commit();
        echo json_encode(['status' => 'consumed', 'jwt' => $jwt]);
        exit;
    }
    recovery_error('invalid_request', 'The recovery request is invalid.', 400);
} catch (Throwable) {
    if (isset($database) && $database instanceof PDO && $database->inTransaction()) {
        $database->rollBack();
    }
    recovery_error('service_unavailable', 'Wallet recovery is temporarily unavailable.', 503);
}
