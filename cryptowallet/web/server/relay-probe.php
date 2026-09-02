<?php
declare(strict_types=1);

require __DIR__ . '/relay.php';

if (PHP_SAPI !== 'cli') {
    http_response_code(404);
    exit;
}
if ($argc !== 2 || trim($argv[1]) === '') {
    fwrite(STDERR, "Usage: php relay-probe.php <installation-id>\n");
    exit(2);
}

$installationId = trim($argv[1]);
$database = sickwallet_relay_db();
$query = $database->prepare(
    'SELECT 1 FROM sickwallet_installations
     WHERE installation_id = ? AND revoked_at IS NULL'
);
$query->execute([$installationId]);
if ($query->fetchColumn() === false) {
    fwrite(STDERR, "Installation not found or revoked.\n");
    exit(1);
}

$bytes = random_bytes(16);
$bytes[6] = chr((ord($bytes[6]) & 0x0f) | 0x40);
$bytes[8] = chr((ord($bytes[8]) & 0x3f) | 0x80);
$hex = bin2hex($bytes);
$requestId = sprintf(
    '%s-%s-%s-%s-%s',
    substr($hex, 0, 8),
    substr($hex, 8, 4),
    substr($hex, 12, 4),
    substr($hex, 16, 4),
    substr($hex, 20)
);
$now = time();
$database->prepare(
    'INSERT INTO sickwallet_relay_messages
     (request_id, installation_id, operation, request_json, created_at, expires_at, available_at)
     VALUES (?, ?, ?, ?, ?, ?, ?)'
)->execute([$requestId, $installationId, 'probe', '{}', $now, $now + 60, $now]);

fwrite(STDOUT, "Probe {$requestId} queued; waiting up to 30 seconds.\n");
$deadline = microtime(true) + 30;
do {
    $query = $database->prepare(
        'SELECT result_json FROM sickwallet_relay_messages
         WHERE request_id = ? AND installation_id = ? AND completed_at IS NOT NULL'
    );
    $query->execute([$requestId, $installationId]);
    $result = $query->fetchColumn();
    if (is_string($result)) {
        $database->prepare(
            'UPDATE sickwallet_relay_messages SET acknowledged_at = ? WHERE request_id = ?'
        )->execute([time(), $requestId]);
        fwrite(STDOUT, $result . PHP_EOL);
        exit(0);
    }
    usleep(500000);
} while (microtime(true) < $deadline);

fwrite(STDERR, "Probe timed out. Check the cog relay status.\n");
exit(1);
