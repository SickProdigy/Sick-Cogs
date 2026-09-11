<?php
declare(strict_types=1);

require __DIR__ . '/companion.php';

if (PHP_SAPI !== 'cli') {
    http_response_code(404);
    exit;
}
try {
    $response = sickwallet_request('GET', '/api/v1/server/status');
    fwrite(STDOUT, json_encode($response['data'], JSON_PRETTY_PRINT | JSON_THROW_ON_ERROR) . PHP_EOL);
} catch (Throwable $error) {
    fwrite(STDERR, 'Status check failed: ' . $error->getMessage() . PHP_EOL);
    exit(1);
}
