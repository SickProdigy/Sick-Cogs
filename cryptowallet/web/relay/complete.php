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
    [$input, $raw] = sickwallet_relay_json_input();
    $installation = sickwallet_relay_authenticate($raw);
    if (!sickwallet_relay_complete($installation['installation_id'], $input)) {
        sickwallet_relay_error('completion_rejected', 'The relay lease is invalid or expired.', 409);
    }
    sickwallet_relay_response(['data' => ['completed' => true]]);
} catch (InvalidArgumentException | JsonException) {
    sickwallet_relay_error('invalid_request', 'Completion data is invalid.', 400);
} catch (RuntimeException $error) {
    $code = in_array($error->getMessage(), ['request_expired', 'request_replayed'], true)
        ? $error->getMessage()
        : 'invalid_authentication';
    sickwallet_relay_error($code, 'Relay authentication failed.', 401);
} catch (Throwable) {
    sickwallet_relay_error('relay_unavailable', 'The wallet relay is unavailable.', 503);
}
