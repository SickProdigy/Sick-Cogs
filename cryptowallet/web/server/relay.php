<?php
declare(strict_types=1);

const SICKWALLET_RELAY_PROTOCOL = 'relay-v1';
const SICKWALLET_RELAY_AUTH_WINDOW = 300;
const SICKWALLET_RELAY_MAX_BODY = 65536;
const SICKWALLET_RELAY_LEASE_SECONDS = 30;

function sickwallet_relay_config(): array
{
    $path = (string) getenv('SICKWALLET_RELAY_CONFIG');
    if ($path === '' || !str_starts_with($path, '/') || !is_file($path)) {
        throw new RuntimeException('SICKWALLET_RELAY_CONFIG is unavailable.');
    }
    $config = require $path;
    if (!is_array($config)) {
        throw new RuntimeException('Relay configuration is invalid.');
    }
    foreach (['dsn', 'username', 'password'] as $key) {
        if (!array_key_exists($key, $config) || !is_string($config[$key])) {
            throw new RuntimeException("Relay configuration omitted {$key}.");
        }
    }
    return $config;
}

function sickwallet_relay_db(): PDO
{
    static $database = null;
    if ($database instanceof PDO) {
        return $database;
    }
    $config = sickwallet_relay_config();
    $database = new PDO($config['dsn'], $config['username'], $config['password'], [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_EMULATE_PREPARES => false,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
    return $database;
}

function sickwallet_relay_json_input(): array
{
    $raw = file_get_contents('php://input', false, null, 0, SICKWALLET_RELAY_MAX_BODY + 1);
    if ($raw === false || strlen($raw) > SICKWALLET_RELAY_MAX_BODY) {
        throw new InvalidArgumentException('Request body is unavailable or too large.');
    }
    $value = json_decode($raw, true, 32, JSON_THROW_ON_ERROR);
    if (!is_array($value)) {
        throw new InvalidArgumentException('A JSON object is required.');
    }
    return [$value, $raw];
}

function sickwallet_relay_response(array $value, int $status = 200): never
{
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    header('X-Content-Type-Options: nosniff');
    echo json_encode($value, JSON_THROW_ON_ERROR);
    exit;
}

function sickwallet_relay_error(string $code, string $message, int $status): never
{
    sickwallet_relay_response(['error' => ['code' => $code, 'message' => $message]], $status);
}

function sickwallet_relay_request_path(): string
{
    $path = parse_url((string) ($_SERVER['REQUEST_URI'] ?? ''), PHP_URL_PATH);
    if (!is_string($path) || $path === '' || str_contains((string) ($_SERVER['REQUEST_URI'] ?? ''), '?')) {
        throw new RuntimeException('Unsigned or invalid request path.');
    }
    return $path;
}

function sickwallet_relay_authenticate(string $rawBody): array
{
    $installationId = (string) ($_SERVER['HTTP_X_SICKWALLET_INSTALLATION'] ?? '');
    $timestamp = (string) ($_SERVER['HTTP_X_SICKWALLET_TIMESTAMP'] ?? '');
    $nonce = (string) ($_SERVER['HTTP_X_SICKWALLET_NONCE'] ?? '');
    $signature = strtolower((string) ($_SERVER['HTTP_X_SICKWALLET_SIGNATURE'] ?? ''));
    if (
        $installationId === '' ||
        !ctype_digit($timestamp) ||
        strlen($nonce) < 16 ||
        strlen($nonce) > 128 ||
        strlen($signature) !== 64
    ) {
        throw new RuntimeException('invalid_authentication');
    }
    $now = time();
    if (abs($now - (int) $timestamp) > SICKWALLET_RELAY_AUTH_WINDOW) {
        throw new RuntimeException('request_expired');
    }
    $database = sickwallet_relay_db();
    $database->beginTransaction();
    try {
        $query = $database->prepare(
            'SELECT credential, deployment_id, discord_application_id
             FROM sickwallet_installations
             WHERE installation_id = ? AND revoked_at IS NULL
             FOR UPDATE'
        );
        $query->execute([$installationId]);
        $installation = $query->fetch();
        if (!is_array($installation)) {
            throw new RuntimeException('invalid_authentication');
        }
        $canonical = implode("\n", [
            SICKWALLET_RELAY_PROTOCOL,
            $timestamp,
            $nonce,
            strtoupper((string) ($_SERVER['REQUEST_METHOD'] ?? '')),
            sickwallet_relay_request_path(),
            hash('sha256', $rawBody),
        ]);
        $expected = hash_hmac('sha256', $canonical, (string) $installation['credential']);
        if (!hash_equals($expected, $signature)) {
            throw new RuntimeException('invalid_authentication');
        }
        $database->prepare('DELETE FROM sickwallet_relay_nonces WHERE seen_at < ?')
            ->execute([$now - SICKWALLET_RELAY_AUTH_WINDOW]);
        try {
            $database->prepare(
                'INSERT INTO sickwallet_relay_nonces (installation_id, nonce, seen_at)
                 VALUES (?, ?, ?)'
            )->execute([$installationId, $nonce, $now]);
        } catch (PDOException $error) {
            if ((string) $error->getCode() === '23000') {
                throw new RuntimeException('request_replayed');
            }
            throw $error;
        }
        $database->commit();
        return ['installation_id' => $installationId] + $installation;
    } catch (Throwable $error) {
        if ($database->inTransaction()) {
            $database->rollBack();
        }
        throw $error;
    }
}

function sickwallet_relay_pair(array $input): array
{
    foreach (['code', 'deployment_id', 'discord_application_id'] as $key) {
        if (!isset($input[$key]) || !is_string($input[$key]) || trim($input[$key]) === '') {
            throw new InvalidArgumentException('Pairing data is incomplete.');
        }
    }
    if (
        strlen(trim($input['code'])) > 128 ||
        strlen(trim($input['deployment_id'])) > 128 ||
        strlen(trim($input['discord_application_id'])) > 32 ||
        !ctype_digit(trim($input['discord_application_id']))
    ) {
        throw new InvalidArgumentException('Pairing data is invalid.');
    }
    $now = time();
    $digest = hash('sha256', trim($input['code']));
    $installationId = rtrim(strtr(base64_encode(random_bytes(18)), '+/', '-_'), '=');
    $credential = rtrim(strtr(base64_encode(random_bytes(32)), '+/', '-_'), '=');
    $database = sickwallet_relay_db();
    $database->beginTransaction();
    try {
        $query = $database->prepare(
            'SELECT expires_at, consumed_at FROM sickwallet_pairing_codes
             WHERE code_digest = ? FOR UPDATE'
        );
        $query->execute([$digest]);
        $pairing = $query->fetch();
        if (
            !is_array($pairing) ||
            $pairing['consumed_at'] !== null ||
            (int) $pairing['expires_at'] <= $now
        ) {
            throw new RuntimeException('pairing_rejected');
        }
        $database->prepare(
            'UPDATE sickwallet_pairing_codes SET consumed_at = ? WHERE code_digest = ?'
        )->execute([$now, $digest]);
        $database->prepare(
            'UPDATE sickwallet_installations SET revoked_at = ?
             WHERE deployment_id = ? AND discord_application_id = ? AND revoked_at IS NULL'
        )->execute([
            $now, trim($input['deployment_id']), trim($input['discord_application_id'])
        ]);
        $database->prepare(
            'INSERT INTO sickwallet_installations
             (installation_id, credential, deployment_id, discord_application_id, created_at)
             VALUES (?, ?, ?, ?, ?)'
        )->execute([
            $installationId,
            $credential,
            trim($input['deployment_id']),
            trim($input['discord_application_id']),
            $now,
        ]);
        $database->commit();
    } catch (Throwable $error) {
        if ($database->inTransaction()) {
            $database->rollBack();
        }
        throw $error;
    }
    return [
        'version' => 1,
        'installation_id' => $installationId,
        'credential' => $credential,
        'deployment_id' => trim($input['deployment_id']),
        'discord_application_id' => trim($input['discord_application_id']),
    ];
}

function sickwallet_relay_claim(string $installationId): ?array
{
    $now = time();
    $database = sickwallet_relay_db();
    $database->beginTransaction();
    try {
        $query = $database->prepare(
            'SELECT request_id, operation, request_json, expires_at
             FROM sickwallet_relay_messages
             WHERE installation_id = ?
               AND completed_at IS NULL
               AND available_at <= ?
               AND expires_at > ?
               AND (leased_until IS NULL OR leased_until <= ?)
             ORDER BY created_at ASC
             LIMIT 1
             FOR UPDATE'
        );
        $query->execute([$installationId, $now, $now, $now]);
        $message = $query->fetch();
        if (!is_array($message)) {
            $database->commit();
            return null;
        }
        $lease = rtrim(strtr(base64_encode(random_bytes(24)), '+/', '-_'), '=');
        $database->prepare(
            'UPDATE sickwallet_relay_messages
             SET lease_token = ?, leased_until = ?, attempts = attempts + 1
             WHERE request_id = ?'
        )->execute([$lease, $now + SICKWALLET_RELAY_LEASE_SECONDS, $message['request_id']]);
        $database->commit();
        return [
            'request_id' => $message['request_id'],
            'operation' => $message['operation'],
            'payload' => json_decode($message['request_json'], true, 32, JSON_THROW_ON_ERROR),
            'expires_at' => (int) $message['expires_at'],
            'lease_token' => $lease,
        ];
    } catch (Throwable $error) {
        if ($database->inTransaction()) {
            $database->rollBack();
        }
        throw $error;
    }
}

function sickwallet_relay_complete(string $installationId, array $input): bool
{
    foreach (['request_id', 'lease_token', 'result'] as $key) {
        if (!array_key_exists($key, $input)) {
            throw new InvalidArgumentException('Completion data is incomplete.');
        }
    }
    if (!is_string($input['request_id']) || !is_string($input['lease_token']) || !is_array($input['result'])) {
        throw new InvalidArgumentException('Completion data is invalid.');
    }
    $now = time();
    $result = json_encode($input['result'], JSON_THROW_ON_ERROR);
    if (strlen($result) > SICKWALLET_RELAY_MAX_BODY) {
        throw new InvalidArgumentException('Completion result is too large.');
    }
    $query = sickwallet_relay_db()->prepare(
        'UPDATE sickwallet_relay_messages
         SET result_json = ?, completed_at = ?, lease_token = NULL, leased_until = NULL
         WHERE request_id = ? AND installation_id = ? AND lease_token = ?
           AND completed_at IS NULL AND expires_at > ?'
    );
    $query->execute([
        $result,
        $now,
        $input['request_id'],
        $installationId,
        $input['lease_token'],
        $now,
    ]);
    return $query->rowCount() === 1;
}
