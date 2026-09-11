<?php
declare(strict_types=1);

require_once dirname(__DIR__) . '/server/recovery-config.php';

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('Referrer-Policy: no-referrer');
header('X-Content-Type-Options: nosniff');

function result_error(string $code, string $message, int $status): void
{
    http_response_code($status);
    echo json_encode(['error' => ['code' => $code, 'message' => $message]]);
    exit;
}

function result_config(): array
{
    $config = sickwallet_recovery_config();
    if (strlen((string) ($config['relay_secret'] ?? '')) < 32) {
        throw new RuntimeException('Relay configuration is unavailable.');
    }
    return $config;
}

function result_database(array $config): PDO
{
    $database = new PDO(
        (string) $config['database_dsn'],
        (string) $config['database_user'],
        (string) $config['database_password'],
        [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
         PDO::ATTR_EMULATE_PREPARES => false,
         PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC]
    );
    $database->exec(
        'CREATE TABLE IF NOT EXISTS sickwallet_tokenfactory_results ('
        . 'handoff_digest CHAR(64) CHARACTER SET ascii COLLATE ascii_bin PRIMARY KEY,'
        . 'transaction_hash CHAR(66) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,'
        . 'recipient CHAR(42) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,'
        . 'created_at BIGINT UNSIGNED NOT NULL,'
        . 'INDEX sickwallet_tokenfactory_result_created (created_at)'
        . ') ENGINE=InnoDB'
    );
    return $database;
}

function result_body(): array
{
    $raw = (string) file_get_contents('php://input');
    if ($raw === '' || strlen($raw) > 4096) {
        result_error('invalid_request', 'The TokenFactory result is invalid.', 400);
    }
    try {
        $body = json_decode($raw, true, 8, JSON_THROW_ON_ERROR);
    } catch (Throwable) {
        result_error('invalid_request', 'The TokenFactory result is invalid.', 400);
    }
    return [$body, $raw];
}

function result_verify_bot(PDO $database, string $raw, string $secret): void
{
    $timestamp = (string) ($_SERVER['HTTP_X_SICKWALLET_TIMESTAMP'] ?? '');
    $nonce = (string) ($_SERVER['HTTP_X_SICKWALLET_NONCE'] ?? '');
    $signature = (string) ($_SERVER['HTTP_X_SICKWALLET_SIGNATURE'] ?? '');
    if (!ctype_digit($timestamp) || abs(time() - (int) $timestamp) > 300
        || !preg_match('/^[A-Za-z0-9_-]{24,128}$/D', $nonce)
        || !preg_match('/^[a-f0-9]{64}$/D', $signature)) {
        result_error('authentication_failed', 'Relay authentication failed.', 401);
    }
    $canonical = implode("
", [
        'v1', $timestamp, $nonce, 'POST', '/api/tokenfactory-result.php',
        hash('sha256', $raw),
    ]);
    if (!hash_equals(hash_hmac('sha256', $canonical, $secret), $signature)) {
        result_error('authentication_failed', 'Relay authentication failed.', 401);
    }
    try {
        $database->prepare(
            'INSERT INTO sickwallet_relay_nonces (nonce_digest, created_at) VALUES (?, ?)'
        )->execute([hash('sha256', $nonce), time()]);
    } catch (PDOException $error) {
        if ((string) $error->getCode() === '23000') {
            result_error('authentication_failed', 'Relay authentication failed.', 401);
        }
        throw $error;
    }
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    header('Allow: POST');
    result_error('method_not_allowed', 'POST is required.', 405);
}

try {
    [$body, $raw] = result_body();
    $config = result_config();
    $database = result_database($config);
    $operation = (string) ($body['operation'] ?? '');

    if ($operation === 'complete') {
        $handle = (string) ($body['handoff'] ?? '');
        $transaction = strtolower((string) ($body['transaction_hash'] ?? ''));
        $recipient = strtolower((string) ($body['recipient'] ?? ''));
        if (!preg_match('/^[A-Za-z0-9_-]{32,128}$/D', $handle)
            || !preg_match('/^0x[a-f0-9]{64}$/D', $transaction)
            || !preg_match('/^0x[a-f0-9]{40}$/D', $recipient)
            || $recipient === '0x' . str_repeat('0', 40)) {
            result_error('invalid_request', 'The TokenFactory result is invalid.', 400);
        }
        $digest = hash('sha256', $handle);
        $statement = $database->prepare(
            'SELECT expires_at, consumed_at FROM sickwallet_recovery_handoffs '
            . 'WHERE handoff_digest = ?'
        );
        $statement->execute([$digest]);
        $handoff = $statement->fetch();
        if (!$handoff || $handoff['consumed_at'] === null
            || (int) $handoff['expires_at'] + 30 < time()) {
            result_error('handoff_unavailable', 'This deployment handoff is unavailable.', 410);
        }
        $statement = $database->prepare(
            'INSERT INTO sickwallet_tokenfactory_results '
            . '(handoff_digest, transaction_hash, recipient, created_at) VALUES (?, ?, ?, ?)'
            . ' ON DUPLICATE KEY UPDATE transaction_hash = transaction_hash'
        );
        $statement->execute([$digest, $transaction, $recipient, time()]);
        echo json_encode(['status' => 'accepted']);
        exit;
    }

    if ($operation === 'poll') {
        result_verify_bot($database, $raw, (string) $config['relay_secret']);
        $digest = (string) ($body['handoff_digest'] ?? '');
        if (!preg_match('/^[a-f0-9]{64}$/D', $digest)) {
            result_error('invalid_request', 'The TokenFactory poll is invalid.', 400);
        }
        $statement = $database->prepare(
            'SELECT transaction_hash, recipient FROM sickwallet_tokenfactory_results '
            . 'WHERE handoff_digest = ?'
        );
        $statement->execute([$digest]);
        $result = $statement->fetch();
        if (!$result) {
            http_response_code(204);
            exit;
        }
        echo json_encode([
            'status' => 'submitted',
            'transaction_hash' => $result['transaction_hash'],
            'recipient' => $result['recipient'],
        ]);
        exit;
    }
    result_error('invalid_request', 'The TokenFactory operation is invalid.', 400);
} catch (Throwable) {
    result_error('service_unavailable', 'TokenFactory reporting is temporarily unavailable.', 503);
}
