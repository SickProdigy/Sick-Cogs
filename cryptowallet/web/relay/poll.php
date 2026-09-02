<?php
declare(strict_types=1);

$library = (string) getenv('SICKWALLET_RELAY_LIBRARY');
if ($library === '' || !str_starts_with($library, '/') || !is_file($library)) {
    http_response_code(503);
    exit;
}
require $library;

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    sickwallet_relay_error('method_not_allowed', 'POST is required.', 405);
}
try {
    [, $raw] = sickwallet_relay_json_input();
    $installation = sickwallet_relay_authenticate($raw);
    $deadline = microtime(true) + 15;
    do {
        $message = sickwallet_relay_claim($installation['installation_id']);
        if ($message !== null) {
            sickwallet_relay_response(['data' => ['message' => $message]]);
        }
        usleep(250000);
    } while (microtime(true) < $deadline);
    sickwallet_relay_response(['data' => ['message' => null]]);
} catch (InvalidArgumentException | JsonException) {
    sickwallet_relay_error('invalid_request', 'Polling data is invalid.', 400);
} catch (RuntimeException $error) {
    $code = in_array($error->getMessage(), ['request_expired', 'request_replayed'], true)
        ? $error->getMessage()
        : 'invalid_authentication';
    sickwallet_relay_error($code, 'Relay authentication failed.', 401);
} catch (Throwable) {
    sickwallet_relay_error('relay_unavailable', 'The wallet relay is unavailable.', 503);
}
