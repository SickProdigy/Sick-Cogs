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
    [$input] = sickwallet_relay_json_input();
    $result = sickwallet_relay_pair($input);
    sickwallet_relay_response(['data' => $result], 201);
} catch (InvalidArgumentException | JsonException) {
    sickwallet_relay_error('invalid_request', 'Pairing data is invalid.', 400);
} catch (RuntimeException $error) {
    $status = $error->getMessage() === 'pairing_rejected' ? 403 : 503;
    sickwallet_relay_error(
        $status === 403 ? 'pairing_rejected' : 'relay_unavailable',
        $status === 403 ? 'The pairing code is invalid or expired.' : 'The wallet relay is unavailable.',
        $status
    );
} catch (Throwable) {
    sickwallet_relay_error('relay_unavailable', 'The wallet relay is unavailable.', 503);
}
