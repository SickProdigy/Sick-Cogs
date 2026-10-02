<?php
declare(strict_types=1);

require_once dirname(__DIR__) . "/server/recovery-config.php";

const WALLET_TERMS_PRODUCT = "polymarket";
const WALLET_TERMS_VERSION = "2026-10-02.1";
const WALLET_TERMS_LIFETIME_SECONDS = 600;

header("Content-Type: application/json; charset=utf-8");
header("Cache-Control: no-store");
header("Referrer-Policy: no-referrer");
header("X-Content-Type-Options: nosniff");

function terms_error(string $code, string $message, int $status): void
{
    http_response_code($status);
    echo json_encode(["error" => ["code" => $code, "message" => $message]]);
    exit;
}

function terms_body(): array
{
    $raw = (string) file_get_contents("php://input");
    if ($raw === "" || strlen($raw) > 4096) {
        terms_error("invalid_request", "The terms request is invalid.", 400);
    }
    try {
        $body = json_decode($raw, true, 12, JSON_THROW_ON_ERROR);
    } catch (Throwable) {
        terms_error("invalid_request", "The terms request is invalid.", 400);
    }
    if (!is_array($body)) {
        terms_error("invalid_request", "The terms request is invalid.", 400);
    }
    return [$body, $raw];
}

function terms_config(): array
{
    $configuration = sickwallet_recovery_config();
    $secret = (string) ($configuration["relay_secret"] ?? "");
    $dsn = (string) ($configuration["database_dsn"] ?? "");
    if (strlen($secret) < 32 || strlen($secret) > 512
        || $dsn === "" || !str_starts_with($dsn, "mysql:")) {
        throw new RuntimeException("Terms relay configuration is unavailable.");
    }
    return $configuration;
}

function terms_database(array $configuration): PDO
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

function terms_verify_poll(PDO $database, string $raw, string $secret): void
{
    $timestamp = (string) ($_SERVER["HTTP_X_SICKWALLET_TIMESTAMP"] ?? "");
    $nonce = (string) ($_SERVER["HTTP_X_SICKWALLET_NONCE"] ?? "");
    $signature = (string) ($_SERVER["HTTP_X_SICKWALLET_SIGNATURE"] ?? "");
    if (!ctype_digit($timestamp) || abs(time() - (int) $timestamp) > 300
        || !preg_match("/^[A-Za-z0-9_-]{24,128}$/D", $nonce)
        || !preg_match("/^[a-f0-9]{64}$/D", $signature)) {
        terms_error("authentication_failed", "Relay authentication failed.", 401);
    }
    $canonical = implode("\n", [
        "v1", $timestamp, $nonce, "POST", "/api/polymarket-terms.php", hash("sha256", $raw),
    ]);
    if (!hash_equals(hash_hmac("sha256", $canonical, $secret), $signature)) {
        terms_error("authentication_failed", "Relay authentication failed.", 401);
    }
    try {
        $statement = $database->prepare(
            "INSERT INTO sickwallet_relay_nonces (nonce_digest, created_at) VALUES (?, ?)"
        );
        $statement->execute([hash("sha256", $nonce), time()]);
    } catch (PDOException $error) {
        if ((string) $error->getCode() === "23000") {
            terms_error("authentication_failed", "Relay authentication failed.", 401);
        }
        throw $error;
    }
}

if ($_SERVER["REQUEST_METHOD"] !== "POST") {
    header("Allow: POST");
    terms_error("method_not_allowed", "POST is required.", 405);
}

try {
    [$body, $raw] = terms_body();
    $configuration = terms_config();
    $database = terms_database($configuration);
    $operation = (string) ($body["operation"] ?? "");

    if ($operation === "submit") {
        $handle = (string) ($body["handoff"] ?? "");
        $product = (string) ($body["product"] ?? "");
        $version = (string) ($body["version"] ?? "");
        if (!preg_match("/^[A-Za-z0-9_-]{32,128}$/D", $handle)
            || $product !== WALLET_TERMS_PRODUCT || $version !== WALLET_TERMS_VERSION) {
            terms_error("invalid_request", "The terms acceptance is invalid.", 400);
        }
        $acceptanceId = rtrim(strtr(base64_encode(random_bytes(24)), "+/", "-_"), "=");
        try {
            $statement = $database->prepare(
                "INSERT INTO sickwallet_polymarket_terms_acceptances "
                . "(handoff_digest, product, terms_version, acceptance_id, created_at) "
                . "VALUES (?, ?, ?, ?, ?)"
            );
            $statement->execute([
                hash("sha256", $handle), $product, $version, $acceptanceId, time(),
            ]);
        } catch (PDOException $error) {
            if ((string) $error->getCode() === "23000") {
                terms_error("acceptance_unavailable", "This acceptance was already submitted.", 409);
            }
            throw $error;
        }
        $database->prepare(
            "DELETE FROM sickwallet_polymarket_terms_acceptances WHERE created_at < ?"
        )->execute([time() - WALLET_TERMS_LIFETIME_SECONDS]);
        http_response_code(201);
        echo json_encode(["status" => "submitted"]);
        exit;
    }

    if ($operation === "poll") {
        terms_verify_poll($database, $raw, (string) $configuration["relay_secret"]);
        $digest = (string) ($body["handoff_digest"] ?? "");
        if (!preg_match("/^[a-f0-9]{64}$/D", $digest)) {
            terms_error("invalid_request", "The terms poll is invalid.", 400);
        }
        $database->beginTransaction();
        $statement = $database->prepare(
            "SELECT product, terms_version, acceptance_id, created_at "
            . "FROM sickwallet_polymarket_terms_acceptances WHERE handoff_digest = ? FOR UPDATE"
        );
        $statement->execute([$digest]);
        $row = $statement->fetch();
        if (!$row || (int) $row["created_at"] < time() - WALLET_TERMS_LIFETIME_SECONDS) {
            $database->rollBack();
            http_response_code(204);
            exit;
        }
        $database->prepare(
            "DELETE FROM sickwallet_polymarket_terms_acceptances WHERE handoff_digest = ?"
        )->execute([$digest]);
        $database->commit();
        echo json_encode([
            "status" => "submitted", "product" => (string) $row["product"],
            "version" => (string) $row["terms_version"],
            "acceptance_id" => (string) $row["acceptance_id"],
        ]);
        exit;
    }
    terms_error("invalid_request", "The terms request is invalid.", 400);
} catch (Throwable) {
    if (isset($database) && $database instanceof PDO && $database->inTransaction()) {
        $database->rollBack();
    }
    terms_error("service_unavailable", "Polymarket terms acceptance is temporarily unavailable.", 503);
}
