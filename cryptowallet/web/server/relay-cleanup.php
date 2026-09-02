<?php
declare(strict_types=1);

require __DIR__ . '/relay.php';

if (PHP_SAPI !== 'cli') {
    http_response_code(404);
    exit;
}

$now = time();
$database = sickwallet_relay_db();
$database->beginTransaction();
try {
    $expiredMessages = $database->prepare(
        'DELETE FROM sickwallet_relay_messages
         WHERE expires_at <= ? OR (acknowledged_at IS NOT NULL AND acknowledged_at <= ?)'
    );
    $expiredMessages->execute([$now, $now - 300]);
    $expiredPairings = $database->prepare(
        'DELETE FROM sickwallet_pairing_codes
         WHERE expires_at <= ? OR consumed_at IS NOT NULL'
    );
    $expiredPairings->execute([$now]);
    $expiredNonces = $database->prepare(
        'DELETE FROM sickwallet_relay_nonces WHERE seen_at < ?'
    );
    $expiredNonces->execute([$now - SICKWALLET_RELAY_AUTH_WINDOW]);
    $database->commit();
    fwrite(
        STDOUT,
        sprintf(
            "Removed %d messages, %d pairing codes, and %d nonces.\n",
            $expiredMessages->rowCount(),
            $expiredPairings->rowCount(),
            $expiredNonces->rowCount()
        )
    );
} catch (Throwable) {
    if ($database->inTransaction()) {
        $database->rollBack();
    }
    fwrite(STDERR, "Relay cleanup failed.\n");
    exit(1);
}
