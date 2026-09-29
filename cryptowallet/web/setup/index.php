<?php
declare(strict_types=1);

session_start([
    'cookie_httponly' => true,
    'cookie_samesite' => 'Strict',
    'cookie_secure' => !empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off',
    'use_strict_mode' => true,
]);

header('Cache-Control: no-store');
header('Referrer-Policy: no-referrer');
header('X-Content-Type-Options: nosniff');
header("Content-Security-Policy: default-src 'none'; style-src 'self'; script-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'");

$serverDirectory = dirname(__DIR__) . '/server';
$installedLockPath = $serverDirectory . '/setup-locked';
$lockPath = $serverDirectory . '/setup.lock';
$migrationMarkerPath = $serverDirectory . '/migration-current';
$configPath = $serverDirectory . '/recovery-config.local.php';
$migrationDirectory = $serverDirectory . '/migrations';
require_once $serverDirectory . '/migration-runner.php';
require_once $serverDirectory . '/recovery-config.php';
$existingConfiguration = sickwallet_recovery_config();
$configured = str_starts_with((string) ($existingConfiguration['database_dsn'] ?? ''), 'mysql:')
    && (string) ($existingConfiguration['database_user'] ?? '') !== '';
$installed = $configured || is_file($installedLockPath);
$success = false;
$updated = false;
$alreadyCurrent = false;
$updateAvailable = false;
$error = '';
$relaySecret = '';

if (empty($_SESSION['sickwallet_setup_csrf'])) {
    $_SESSION['sickwallet_setup_csrf'] = bin2hex(random_bytes(32));
}

function setup_value(string $name, string $default = ''): string
{
    return htmlspecialchars((string) ($_POST[$name] ?? $default), ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function setup_write_configuration(string $path, array $configuration): void
{
    $contents = "<?php\ndeclare(strict_types=1);\n\nreturn "
        . var_export($configuration, true) . ";\n";
    $temporary = $path . '.' . bin2hex(random_bytes(8)) . '.tmp';
    $handle = fopen($temporary, 'x');
    if ($handle === false) {
        throw new RuntimeException('Could not create the private configuration file.');
    }
    try {
        if (!flock($handle, LOCK_EX) || fwrite($handle, $contents) !== strlen($contents)) {
            throw new RuntimeException('Could not write the private configuration file.');
        }
        fflush($handle);
        chmod($temporary, 0600);
    } finally {
        fclose($handle);
    }
    if (!rename($temporary, $path)) {
        @unlink($temporary);
        throw new RuntimeException('Could not activate the private configuration file.');
    }
}

function setup_database(string $dsn, string $user, string $password): PDO
{
    return new PDO($dsn, $user, $password, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_EMULATE_PREPARES => false,
    ]);
}

function setup_write_marker(string $path, string $fingerprint): void
{
    $temporary = $path . '.' . bin2hex(random_bytes(8)) . '.tmp';
    if (file_put_contents($temporary, $fingerprint . "\n", LOCK_EX) === false) {
        throw new RuntimeException('Could not record the database update state.');
    }
    chmod($temporary, 0600);
    if (!rename($temporary, $path)) {
        @unlink($temporary);
        throw new RuntimeException('Could not activate the database update state.');
    }
}

$migrationFingerprint = sickwallet_migration_fingerprint($migrationDirectory);
if ($_SERVER['REQUEST_METHOD'] !== 'POST' && $configured) {
    $recordedFingerprint = is_file($migrationMarkerPath) && !is_link($migrationMarkerPath)
        ? trim((string) file_get_contents($migrationMarkerPath))
        : '';
    $alreadyCurrent = $recordedFingerprint !== ''
        && hash_equals($migrationFingerprint, $recordedFingerprint);
    $updateAvailable = !$alreadyCurrent;
}

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $lock = fopen($lockPath, 'c');
    try {
        if ($lock === false || !flock($lock, LOCK_EX)) {
            throw new RuntimeException('The installer lock could not be acquired.');
        }
        $csrf = (string) ($_POST['csrf'] ?? '');
        if (!hash_equals((string) $_SESSION['sickwallet_setup_csrf'], $csrf)) {
            throw new RuntimeException('The setup form expired. Refresh it and try again.');
        }
        if (empty($_SERVER['HTTPS']) || $_SERVER['HTTPS'] === 'off') {
            throw new RuntimeException('HTTPS is required.');
        }
        if (!extension_loaded('pdo_mysql')) {
            throw new RuntimeException('The PHP PDO MySQL extension is not enabled.');
        }

        $action = (string) ($_POST['action'] ?? '');
        if ($installed) {
            if ($action !== 'update' || !$configured) {
                throw new RuntimeException('The database update is unavailable.');
            }
            $dsn = (string) $existingConfiguration['database_dsn'];
            $user = (string) $existingConfiguration['database_user'];
            $password = (string) ($existingConfiguration['database_password'] ?? '');
        } else {
            if ($action !== 'install') {
                throw new RuntimeException('The setup request is invalid.');
            }
            if (!extension_loaded('openssl')) {
                throw new RuntimeException('The PHP OpenSSL extension is not enabled.');
            }
            $host = trim((string) ($_POST['database_host'] ?? ''));
            $port = trim((string) ($_POST['database_port'] ?? '3306'));
            $database = trim((string) ($_POST['database_name'] ?? ''));
            $user = trim((string) ($_POST['database_user'] ?? ''));
            $password = (string) ($_POST['database_password'] ?? '');
            if (!preg_match('/^[A-Za-z0-9.-]{1,253}$/D', $host)
                || !ctype_digit($port) || (int) $port < 1 || (int) $port > 65535
                || !preg_match('/^[A-Za-z0-9_$-]{1,64}$/D', $database)
                || $user === '' || strlen($user) > 128 || $password === '') {
                throw new RuntimeException('The database details are incomplete or invalid.');
            }
            $dsn = sprintf('mysql:host=%s;port=%d;dbname=%s;charset=utf8mb4', $host, (int) $port, $database);
        }
        $connection = setup_database($dsn, $user, $password);
        $migrationResult = sickwallet_apply_migrations($connection, $migrationDirectory);
        setup_write_marker($migrationMarkerPath, $migrationFingerprint);
        if ($installed) {
            $alreadyCurrent = $migrationResult['applied'] === [];
            $updated = !$alreadyCurrent;
            $updateAvailable = false;
        } else {
            $relaySecret = rtrim(strtr(base64_encode(random_bytes(48)), '+/', '-_'), '=');
            setup_write_configuration($configPath, [
                'relay_secret' => $relaySecret,
                'database_dsn' => $dsn,
                'database_user' => $user,
                'database_password' => $password,
            ]);
            if (file_put_contents($installedLockPath, "installed\n", LOCK_EX) !== false) {
                chmod($installedLockPath, 0600);
            }
            $success = true;
            $installed = true;
            unset($_SESSION['sickwallet_setup_csrf']);
        }
    } catch (Throwable $exception) {
        if ($installed) {
            error_log('CryptoWallet database update failed: ' . $exception->getMessage());
            $error = 'The database update failed. Check the server log and try again.';
        } else {
            $error = $exception instanceof PDOException
                ? 'The database connection or schema installation failed.'
                : $exception->getMessage();
        }
    } finally {
        if (is_resource($lock)) {
            flock($lock, LOCK_UN);
            fclose($lock);
        }
    }
}
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CryptoWallet Companion Setup</title>
  <link rel="stylesheet" href="../styles.css">
</head>
<body>
  <main class="card">
    <p class="eyebrow">SickGaming CryptoWallet</p>
    <h1>Companion setup and upgrade</h1>
    <?php if ($success): ?>
      <div class="notice info"><strong>Installation complete.</strong><p>The database tables and private configuration were created, and setup is now locked.</p></div>
      <p>Run this owner-only command in a private Discord channel, then delete the message:</p>
      <div class="setup-command-row">
        <pre class="setup-command"><code id="setup-command">[p]set api cryptowallet_relay secret <?= htmlspecialchars($relaySecret, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8') ?></code></pre>
        <button class="setup-copy-button" id="copy-setup-command" type="button">Copy command</button>
      </div>
      <p id="copy-setup-status" role="status" aria-live="polite"></p>
      <p>This secret is shown only on this response. Store it in Red before leaving this page.</p>
    <?php elseif ($installed): ?>
      <?php if ($updated): ?>
        <div class="notice info"><strong>Database update complete.</strong><p>The bundled database updates were applied.</p></div>
      <?php elseif ($alreadyCurrent): ?>
        <div class="notice info"><strong>Database is current.</strong><p>No database update was needed.</p></div>
      <?php elseif ($error !== ''): ?>
        <div class="notice danger"><strong>Database update failed.</strong><p><?= htmlspecialchars($error, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8') ?></p></div>
      <?php elseif ($updateAvailable): ?>
        <div class="notice info"><strong>Database update available.</strong><p>The uploaded companion includes a database update that has not been applied.</p></div>
      <?php else: ?>
        <div class="notice info"><strong>Existing installation detected.</strong><p>Use this page after deploying an updated CryptoWallet companion.</p></div>
      <?php endif; ?>
      <?php if ($updateAvailable): ?>
        <form method="post" autocomplete="off">
          <input type="hidden" name="action" value="update">
          <input type="hidden" name="csrf" value="<?= htmlspecialchars((string) $_SESSION['sickwallet_setup_csrf'], ENT_QUOTES, 'UTF-8') ?>">
          <button type="submit">Run database update</button>
        </form>
      <?php endif; ?>
    <?php else: ?>
      <?php if ($error !== ''): ?>
        <div class="notice danger"><strong>Setup failed.</strong><p><?= htmlspecialchars($error, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8') ?></p></div>
      <?php endif; ?>
      <p>Create an empty database and database user in DirectAdmin first. This wizard tests the connection, installs only the CryptoWallet relay tables, generates the relay secret, and writes the private configuration.</p>
      <div class="notice danger"><strong>Complete setup promptly.</strong><p>This installer is public until installation succeeds and creates its lock.</p></div>
      <form method="post" autocomplete="off">
        <input type="hidden" name="action" value="install">
        <input type="hidden" name="csrf" value="<?= htmlspecialchars((string) $_SESSION['sickwallet_setup_csrf'], ENT_QUOTES, 'UTF-8') ?>">
        <label for="database_host">Database host</label>
        <input id="database_host" name="database_host" value="<?= setup_value('database_host', '127.0.0.1') ?>" required maxlength="253">
        <label for="database_port">Database port</label>
        <input id="database_port" name="database_port" value="<?= setup_value('database_port', '3306') ?>" required inputmode="numeric" maxlength="5">
        <label for="database_name">Database name</label>
        <input id="database_name" name="database_name" value="<?= setup_value('database_name') ?>" required maxlength="64">
        <label for="database_user">Database username</label>
        <input id="database_user" name="database_user" value="<?= setup_value('database_user') ?>" required maxlength="128">
        <label for="database_password">Database password</label>
        <input id="database_password" name="database_password" type="password" required maxlength="1024">
        <button type="submit">Install recovery relay</button>
      </form>
    <?php endif; ?>
  </main>
  <script src="./setup.js" defer></script>
</body>
</html>
