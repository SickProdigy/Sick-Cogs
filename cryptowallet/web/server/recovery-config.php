<?php
declare(strict_types=1);

function sickwallet_recovery_config(): array
{
    $environment = [
        'relay_secret' => (string) getenv('SICKWALLET_RECOVERY_RELAY_SECRET'),
        'database_dsn' => (string) getenv('SICKWALLET_DATABASE_DSN'),
        'database_user' => (string) getenv('SICKWALLET_DATABASE_USER'),
        'database_password' => (string) getenv('SICKWALLET_DATABASE_PASSWORD'),
    ];
    if ($environment['relay_secret'] !== '' || $environment['database_dsn'] !== '') {
        return $environment;
    }

    $path = __DIR__ . '/recovery-config.local.php';
    if (!is_file($path) || is_link($path)) {
        return [];
    }
    $configuration = require $path;
    return is_array($configuration) ? $configuration : [];
}
