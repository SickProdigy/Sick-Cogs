<?php
declare(strict_types=1);

if (PHP_SAPI !== "cli") {
    http_response_code(404);
    exit;
}

require_once __DIR__ . "/recovery-config.php";
require_once __DIR__ . "/migration-runner.php";

try {
    $configuration = sickwallet_recovery_config();
    $dsn = (string) ($configuration["database_dsn"] ?? "");
    $user = (string) ($configuration["database_user"] ?? "");
    $password = (string) ($configuration["database_password"] ?? "");
    if ($dsn === "" || !str_starts_with($dsn, "mysql:") || $user === "") {
        throw new RuntimeException("CryptoWallet database configuration is unavailable.");
    }
    $database = new PDO($dsn, $user, $password, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_EMULATE_PREPARES => false,
    ]);
    $result = sickwallet_apply_migrations($database, __DIR__ . "/migrations");
    if ($result["applied"] === []) {
        fwrite(STDOUT, "CryptoWallet database is already current.\n");
    } else {
        foreach ($result["applied"] as $migration) {
            fwrite(STDOUT, "Applied " . $migration . "\n");
        }
        fwrite(STDOUT, "CryptoWallet database upgrade complete.\n");
    }
    exit(0);
} catch (Throwable $error) {
    fwrite(STDERR, "CryptoWallet database upgrade failed: " . $error->getMessage() . "\n");
    exit(1);
}
