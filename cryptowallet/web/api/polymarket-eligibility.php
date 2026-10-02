<?php
declare(strict_types=1);

require_once dirname(__DIR__) . "/server/recovery-config.php";

const POLYMARKET_RESULT_LIFETIME_SECONDS = 300;
const POLYMARKET_RESULT_AAD = "sickwallet-polymarket-eligibility-v1";

header("Content-Type: application/json; charset=utf-8");
header("Cache-Control: no-store");
header("Referrer-Policy: no-referrer");
header("X-Content-Type-Options: nosniff");

function polymarket_error(string $code, string $message, int $status): void
{
    http_response_code($status);
    echo json_encode(["error" => ["code" => $code, "message" => $message]]);
    exit;
}

function polymarket_body(): array
{
    $raw = (string) file_get_contents("php://input");
    if ($raw === "" || strlen($raw) > 4096) {
        polymarket_error("invalid_request", "The eligibility result is invalid.", 400);
    }
    try {
        $body = json_decode($raw, true, 12, JSON_THROW_ON_ERROR);
    } catch (Throwable) {
        polymarket_error("invalid_request", "The eligibility result is invalid.", 400);
    }
    if (!is_array($body)) {
        polymarket_error("invalid_request", "The eligibility result is invalid.", 400);
    }
    return [$body, $raw];
}

function polymarket_config(): array
{
    $configuration = sickwallet_recovery_config();
    $secret = (string) ($configuration["relay_secret"] ?? "");
    $dsn = (string) ($configuration["database_dsn"] ?? "");
    if (strlen($secret) < 32 || strlen($secret) > 512
        || $dsn === "" || !str_starts_with($dsn, "mysql:")) {
        throw new RuntimeException("Polymarket relay configuration is unavailable.");
    }
    return $configuration;
}

function polymarket_database(array $configuration): PDO
{
    return new PDO(
        (string) $configuration["database_dsn"],
        (string) ($configuration["database_user"] ?? ""),
        (string) ($configuration["database_password"] ?? ""),
        [PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
         PDO::ATTR_EMULATE_PREPARES => false,
         PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC]
    );
}

function polymarket_verify_poll(PDO $database, string $raw, string $secret): void
{
    $timestamp = (string) ($_SERVER["HTTP_X_SICKWALLET_TIMESTAMP"] ?? "");
    $nonce = (string) ($_SERVER["HTTP_X_SICKWALLET_NONCE"] ?? "");
    $signature = (string) ($_SERVER["HTTP_X_SICKWALLET_SIGNATURE"] ?? "");
    if (!ctype_digit($timestamp) || abs(time() - (int) $timestamp) > 300
        || !preg_match("/^[A-Za-z0-9_-]{24,128}$/D", $nonce)
        || !preg_match("/^[a-f0-9]{64}$/D", $signature)) {
        polymarket_error("authentication_failed", "Relay authentication failed.", 401);
    }
    $canonical = implode("\n", [
        "v1", $timestamp, $nonce, "POST", "/api/polymarket-eligibility.php", hash("sha256", $raw),
    ]);
    if (!hash_equals(hash_hmac("sha256", $canonical, $secret), $signature)) {
        polymarket_error("authentication_failed", "Relay authentication failed.", 401);
    }
    try {
        $statement = $database->prepare(
            "INSERT INTO sickwallet_relay_nonces (nonce_digest, created_at) VALUES (?, ?)"
        );
        $statement->execute([hash("sha256", $nonce), time()]);
    } catch (PDOException $error) {
        if ((string) $error->getCode() === "23000") {
            polymarket_error("authentication_failed", "Relay authentication failed.", 401);
        }
        throw $error;
    }
}

function polymarket_client_ip(array $configuration): string
{
    $serverKey = (string) ($configuration["trusted_client_ip_server_key"] ?? "");
    if ($serverKey !== "" && !preg_match("/^[A-Z0-9_]{1,64}$/D", $serverKey)) {
        throw new RuntimeException("Trusted client IP configuration is invalid.");
    }
    $candidate = (string) ($_SERVER[$serverKey !== "" ? $serverKey : "REMOTE_ADDR"] ?? "");
    if (str_contains($candidate, ",") || filter_var($candidate, FILTER_VALIDATE_IP) === false) {
        polymarket_error("eligibility_unavailable", "The protected client address is unavailable.", 400);
    }
    return $candidate;
}

function polymarket_encrypt(array $result, string $secret): array
{
    $plaintext = json_encode($result, JSON_THROW_ON_ERROR);
    $nonce = random_bytes(12);
    $tag = "";
    $key = hash_hmac("sha256", "sickwallet-polymarket-result-encryption-v1", $secret, true);
    $ciphertext = openssl_encrypt(
        $plaintext, "aes-256-gcm", $key, OPENSSL_RAW_DATA, $nonce, $tag,
        POLYMARKET_RESULT_AAD, 16
    );
    if ($ciphertext === false || strlen($tag) !== 16) {
        throw new RuntimeException("Polymarket result encryption failed.");
    }
    return [$ciphertext, $nonce, $tag];
}

function polymarket_decrypt(array $row, string $secret): array
{
    $key = hash_hmac("sha256", "sickwallet-polymarket-result-encryption-v1", $secret, true);
    $plaintext = openssl_decrypt(
        $row["ciphertext"], "aes-256-gcm", $key, OPENSSL_RAW_DATA,
        $row["cipher_nonce"], $row["cipher_tag"], POLYMARKET_RESULT_AAD
    );
    if (!is_string($plaintext) || $plaintext === "") {
        throw new RuntimeException("Polymarket result decryption failed.");
    }
    $result = json_decode($plaintext, true, 8, JSON_THROW_ON_ERROR);
    if (!is_array($result)) {
        throw new RuntimeException("Polymarket result decryption failed.");
    }
    return $result;
}

if ($_SERVER["REQUEST_METHOD"] !== "POST") {
    header("Allow: POST");
    polymarket_error("method_not_allowed", "POST is required.", 405);
}

try {
    [$body, $raw] = polymarket_body();
    $configuration = polymarket_config();
    $database = polymarket_database($configuration);
    $secret = (string) $configuration["relay_secret"];
    $operation = (string) ($body["operation"] ?? "");

    if ($operation === "submit") {
        $expected = ["blocked", "checked_at", "country", "handoff", "ip", "operation", "region"];
        $actual = array_keys($body);
        sort($actual, SORT_STRING);
        $handle = (string) ($body["handoff"] ?? "");
        $blocked = $body["blocked"] ?? null;
        $country = (string) ($body["country"] ?? "");
        $region = (string) ($body["region"] ?? "");
        $checkedAt = $body["checked_at"] ?? null;
        $reportedIp = (string) ($body["ip"] ?? "");
        $now = time();
        if ($actual !== $expected
            || !preg_match("/^[A-Za-z0-9_-]{32,128}$/D", $handle)
            || !is_bool($blocked)
            || !preg_match("/^[A-Z]{2}$/D", $country)
            || !preg_match("/^[A-Z0-9-]{0,16}$/D", $region)
            || !is_int($checkedAt) || abs($now - $checkedAt) > 30
            || filter_var($reportedIp, FILTER_VALIDATE_IP) === false
            || !hash_equals(inet_pton(polymarket_client_ip($configuration)), inet_pton($reportedIp))) {
            polymarket_error("invalid_request", "The eligibility result is invalid.", 400);
        }
        $result = [
            "status" => "submitted", "blocked" => $blocked,
            "country" => $country, "region" => $region, "checked_at" => $checkedAt,
        ];
        [$ciphertext, $cipherNonce, $cipherTag] = polymarket_encrypt($result, $secret);
        try {
            $statement = $database->prepare(
                "INSERT INTO sickwallet_polymarket_eligibility_results "
                . "(handoff_digest, ciphertext, cipher_nonce, cipher_tag, created_at, expires_at) "
                . "VALUES (?, ?, ?, ?, ?, ?)"
            );
            $statement->execute([
                hash("sha256", $handle), $ciphertext, $cipherNonce, $cipherTag,
                $now, $checkedAt + POLYMARKET_RESULT_LIFETIME_SECONDS,
            ]);
        } catch (PDOException $error) {
            if ((string) $error->getCode() === "23000") {
                polymarket_error("result_unavailable", "This result was already submitted.", 409);
            }
            throw $error;
        }
        $database->prepare(
            "DELETE FROM sickwallet_polymarket_eligibility_results WHERE expires_at < ?"
        )->execute([$now - 86400]);
        http_response_code(201);
        echo json_encode(["status" => "submitted"]);
        exit;
    }

    if ($operation === "poll") {
        polymarket_verify_poll($database, $raw, $secret);
        $digest = (string) ($body["handoff_digest"] ?? "");
        if (!preg_match("/^[a-f0-9]{64}$/D", $digest)) {
            polymarket_error("invalid_request", "The eligibility poll is invalid.", 400);
        }
        $database->beginTransaction();
        $statement = $database->prepare(
            "SELECT ciphertext, cipher_nonce, cipher_tag, expires_at "
            . "FROM sickwallet_polymarket_eligibility_results WHERE handoff_digest = ? FOR UPDATE"
        );
        $statement->execute([$digest]);
        $row = $statement->fetch();
        if (!$row || (int) $row["expires_at"] <= time()) {
            $database->rollBack();
            http_response_code(204);
            exit;
        }
        $result = polymarket_decrypt($row, $secret);
        $database->prepare(
            "DELETE FROM sickwallet_polymarket_eligibility_results WHERE handoff_digest = ?"
        )->execute([$digest]);
        $database->commit();
        echo json_encode($result);
        exit;
    }
    polymarket_error("invalid_request", "The eligibility request is invalid.", 400);
} catch (Throwable) {
    if (isset($database) && $database instanceof PDO && $database->inTransaction()) {
        $database->rollBack();
    }
    polymarket_error("service_unavailable", "Polymarket eligibility is temporarily unavailable.", 503);
}
