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
header("Content-Security-Policy: default-src 'none'; style-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'");

$serverDirectory = dirname(__DIR__) . '/server';
$enablePath = $serverDirectory . '/setup-enabled';
$lockPath = $serverDirectory . '/setup.lock';
$configPath = $serverDirectory . '/recovery-config.local.php';
$schemaPath = $serverDirectory . '/recovery-schema.sql';
$installed = is_file($configPath);
$enabled = is_file($enablePath) && !is_link($enablePath);
$success = false;
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

if ($_SERVER['REQUEST_METHOD'] === 'POST' && !$installed && $enabled) {
    $lock = fopen($lockPath, 'c');
    try {
        if ($lock === false || !flock($lock, LOCK_EX)) {
            throw new RuntimeException('The installer lock could not be acquired.');
        }
        if (is_file($configPath)) {
            throw new RuntimeException('CryptoWallet recovery is already configured.');
        }
        $csrf = (string) ($_POST['csrf'] ?? '');
        if (!hash_equals((string) $_SESSION['sickwallet_setup_csrf'], $csrf)) {
            throw new RuntimeException('The setup form expired. Refresh it and try again.');
        }
        if (empty($_SERVER['HTTPS']) || $_SERVER['HTTPS'] === 'off') {
            throw new RuntimeException('HTTPS is required to run this installer.');
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
        if (!extension_loaded('pdo_mysql')) {
            throw new RuntimeException('The PHP PDO MySQL extension is not enabled.');
        }
        if (!extension_loaded('openssl')) {
            throw new RuntimeException('The PHP OpenSSL extension is not enabled.');
        }

        $dsn = sprintf('mysql:host=%s;port=%d;dbname=%s;charset=utf8mb4', $host, (int) $port, $database);
        $connection = new PDO($dsn, $user, $password, [
            PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
            PDO::ATTR_EMULATE_PREPARES => false,
        ]);
        $schema = file_get_contents($schemaPath);
        if ($schema === false || trim($schema) === '') {
            throw new RuntimeException('The recovery schema could not be loaded.');
        }
        $statements = preg_split('/;[[:space:]]*(?:$|\R)/', trim($schema));
        foreach ($statements as $statement) {
            if (trim((string) $statement) !== '') {
                $connection->exec($statement);
            }
        }
        $relaySecret = rtrim(strtr(base64_encode(random_bytes(48)), '+/', '-_'), '=');
        setup_write_configuration($configPath, [
            'relay_secret' => $relaySecret,
            'database_dsn' => $dsn,
            'database_user' => $user,
            'database_password' => $password,
        ]);
        @unlink($enablePath);
        $success = true;
        $installed = true;
        unset($_SESSION['sickwallet_setup_csrf']);
    } catch (Throwable $exception) {
        $error = $exception instanceof PDOException
            ? 'The database connection or schema installation failed.'
            : $exception->getMessage();
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
  <title>CryptoWallet Recovery Setup</title>
  <link rel="stylesheet" href="../styles.css">
</head>
<body>
  <main class="card">
    <p class="eyebrow">SickGaming CryptoWallet</p>
    <h1>Recovery relay setup</h1>
    <?php if ($success): ?>
      <div class="notice info"><strong>Installation complete.</strong><p>The database tables and private configuration were created, and setup is now locked.</p></div>
      <p>Run this owner-only command in a private Discord channel, then delete the message:</p>
      <pre><code>[p]set api cryptowallet_relay secret <?= htmlspecialchars($relaySecret, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8') ?></code></pre>
      <p>This secret is shown only on this response. Store it in Red before leaving this page.</p>
    <?php elseif ($installed): ?>
      <div class="notice info"><strong>Setup is locked.</strong><p>CryptoWallet recovery is already configured.</p></div>
    <?php elseif (!$enabled): ?>
      <div class="notice danger"><strong>Setup is disabled.</strong><p>Create an empty <code>server/setup-enabled</code> file, then reload this page. It is removed after successful installation.</p></div>
    <?php else: ?>
      <?php if ($error !== ''): ?>
        <div class="notice danger"><strong>Setup failed.</strong><p><?= htmlspecialchars($error, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8') ?></p></div>
      <?php endif; ?>
      <p>Create an empty database and database user in DirectAdmin first. This wizard tests the connection, installs only the CryptoWallet relay tables, generates the relay secret, and writes the private configuration.</p>
      <form method="post" autocomplete="off">
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
</body>
</html>
