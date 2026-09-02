<?php
declare(strict_types=1);

require __DIR__ . '/relay.php';

if (PHP_SAPI !== 'cli') {
    http_response_code(404);
    exit;
}

$code = rtrim(strtr(base64_encode(random_bytes(24)), '+/', '-_'), '=');
$now = time();
$expiresAt = $now + 600;
$database = sickwallet_relay_db();
$database->beginTransaction();
try {
    $database->prepare(
        'DELETE FROM sickwallet_pairing_codes WHERE expires_at <= ? OR consumed_at IS NOT NULL'
    )->execute([$now]);
    $database->prepare(
        'INSERT INTO sickwallet_pairing_codes (code_digest, expires_at, created_at)
         VALUES (?, ?, ?)'
    )->execute([hash('sha256', $code), $expiresAt, $now]);
    $database->commit();
} catch (Throwable $error) {
    if ($database->inTransaction()) {
        $database->rollBack();
    }
    fwrite(STDERR, "Pairing code creation failed.\n");
    exit(1);
}

fwrite(STDOUT, $code . PHP_EOL);
fwrite(STDOUT, "Expires at Unix time {$expiresAt}" . PHP_EOL);
