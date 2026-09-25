<?php
declare(strict_types=1);

require_once dirname(__DIR__) . "/server/recovery-config.php";

header("Content-Type: application/json; charset=utf-8");
header("Cache-Control: no-store");
header("Referrer-Policy: no-referrer");
header("X-Content-Type-Options: nosniff");

function totp_error(string $code, string $message, int $status): void
{
    http_response_code($status);
    echo json_encode(["error" => ["code" => $code, "message" => $message]]);
    exit;
}

function totp_config(): array
{
    $configuration = sickwallet_recovery_config();
    $secret = (string) ($configuration["relay_secret"] ?? "");
    $dsn = (string) ($configuration["database_dsn"] ?? "");
    if (strlen($secret) < 32 || strlen($secret) > 512
        || $dsn === "" || !str_starts_with($dsn, "mysql:")) {
        throw new RuntimeException("TOTP relay configuration is unavailable.");
    }
    return $configuration;
}

function totp_database(array $configuration): PDO
{
    return new PDO(
        (string) $configuration["database_dsn"],
        (string) ($configuration["database_user"] ?? ""),
        (string) ($configuration["database_password"] ?? ""),
        [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_EMULATE_PREPARES => false,
            PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
        ]
    );
}

function totp_body(): array
{
    $raw = (string) file_get_contents("php://input");
    if ($raw === "" || strlen($raw) > 4096) {
        totp_error("invalid_request", "The enrollment request is invalid.", 400);
    }
    try {
        $body = json_decode($raw, true, 12, JSON_THROW_ON_ERROR);
    } catch (Throwable) {
        totp_error("invalid_request", "The enrollment request is invalid.", 400);
    }
    if (!is_array($body)) {
        totp_error("invalid_request", "The enrollment request is invalid.", 400);
    }
    return [$body, $raw];
}

function totp_verify_poll(PDO $database, string $raw, string $secret): void
{
    $timestamp = (string) ($_SERVER["HTTP_X_SICKWALLET_TIMESTAMP"] ?? "");
    $nonce = (string) ($_SERVER["HTTP_X_SICKWALLET_NONCE"] ?? "");
    $signature = (string) ($_SERVER["HTTP_X_SICKWALLET_SIGNATURE"] ?? "");
    if (!ctype_digit($timestamp) || abs(time() - (int) $timestamp) > 300
        || !preg_match("/^[A-Za-z0-9_-]{24,128}$/D", $nonce)
        || !preg_match("/^[a-f0-9]{64}$/D", $signature)) {
        totp_error("authentication_failed", "Relay authentication failed.", 401);
    }
    $canonical = implode("\n", [
        "v1", $timestamp, $nonce, "POST", "/api/totp-enrollment.php",
        hash("sha256", $raw),
    ]);
    if (!hash_equals(hash_hmac("sha256", $canonical, $secret), $signature)) {
        totp_error("authentication_failed", "Relay authentication failed.", 401);
    }
    try {
        $statement = $database->prepare(
            "INSERT INTO sickwallet_relay_nonces (nonce_digest, created_at) VALUES (?, ?)"
        );
        $statement->execute([hash("sha256", $nonce), time()]);
    } catch (PDOException $error) {
        if ((string) $error->getCode() === "23000") {
            totp_error("authentication_failed", "Relay authentication failed.", 401);
        }
        throw $error;
    }
}

if ($_SERVER["REQUEST_METHOD"] !== "POST") {
    header("Allow: POST");
    totp_error("method_not_allowed", "POST is required.", 405);
}

try {
    [$body, $raw] = totp_body();
    $configuration = totp_config();
    $database = totp_database($configuration);
    $secret = (string) $configuration["relay_secret"];
    $operation = (string) ($body["operation"] ?? "");

    if ($operation === "submit") {
        $handle = (string) ($body["handoff"] ?? "");
        $ciphertext = (string) ($body["ciphertext"] ?? "");
        if (!preg_match("/^[A-Za-z0-9_-]{32,128}$/D", $handle)
            || !preg_match("/^[A-Za-z0-9_-]{300,1024}$/D", $ciphertext)) {
            totp_error("invalid_request", "The encrypted enrollment is invalid.", 400);
        }
        try {
            $statement = $database->prepare(
                "INSERT INTO sickwallet_totp_enrollments "
                . "(handoff_digest, ciphertext, created_at) VALUES (?, ?, ?)"
            );
            $statement->execute([hash("sha256", $handle), $ciphertext, time()]);
        } catch (PDOException $error) {
            if ((string) $error->getCode() === "23000") {
                totp_error("enrollment_unavailable", "This enrollment was already submitted.", 409);
            }
            throw $error;
        }
        $database->prepare(
            "DELETE FROM sickwallet_totp_enrollments WHERE created_at < ?"
        )->execute([time() - 300]);
        http_response_code(201);
        echo json_encode(["status" => "submitted"]);
        exit;
    }

    if ($operation === "poll") {
        totp_verify_poll($database, $raw, $secret);
        $digest = (string) ($body["handoff_digest"] ?? "");
        if (!preg_match("/^[a-f0-9]{64}$/D", $digest)) {
            totp_error("invalid_request", "The enrollment poll is invalid.", 400);
        }
        $database->beginTransaction();
        $statement = $database->prepare(
            "SELECT ciphertext, created_at FROM sickwallet_totp_enrollments "
            . "WHERE handoff_digest = ? FOR UPDATE"
        );
        $statement->execute([$digest]);
        $row = $statement->fetch();
        if (!$row || (int) $row["created_at"] < time() - 300) {
            $database->rollBack();
            http_response_code(204);
            exit;
        }
        $database->prepare(
            "DELETE FROM sickwallet_totp_enrollments WHERE handoff_digest = ?"
        )->execute([$digest]);
        $database->commit();
        echo json_encode([
            "status" => "submitted",
            "ciphertext" => (string) $row["ciphertext"],
        ]);
        exit;
    }

    totp_error("invalid_request", "The enrollment request is invalid.", 400);
} catch (Throwable) {
    if (isset($database) && $database instanceof PDO && $database->inTransaction()) {
        $database->rollBack();
    }
    totp_error("service_unavailable", "Authenticator enrollment is temporarily unavailable.", 503);
}
