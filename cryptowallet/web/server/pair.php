<?php
declare(strict_types=1);

require __DIR__ . '/companion.php';

if (PHP_SAPI !== 'cli') {
    http_response_code(404);
    exit;
}
if ($argc !== 2 || trim($argv[1]) === '') {
    fwrite(STDERR, "Usage: php pair.php <one-time-code>\n");
    exit(2);
}
try {
    $response = sickwallet_request('POST', '/api/v1/pair', ['code' => trim($argv[1])], false);
    sickwallet_save_credentials($response['data'] ?? []);
    fwrite(STDOUT, "Companion website paired successfully.\n");
} catch (Throwable $error) {
    fwrite(STDERR, 'Pairing failed: ' . $error->getMessage() . PHP_EOL);
    exit(1);
}
