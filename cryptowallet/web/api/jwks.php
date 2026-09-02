<?php
declare(strict_types=1);

header('Content-Type: application/jwk-set+json; charset=utf-8');
header('Cache-Control: public, max-age=300');
header('X-Content-Type-Options: nosniff');

if ($_SERVER['REQUEST_METHOD'] !== 'GET') {
    http_response_code(405);
    header('Allow: GET');
    echo json_encode(['error' => ['code' => 'method_not_allowed', 'message' => 'GET is required.']]);
    exit;
}

$library = (string) getenv('SICKWALLET_SERVER_LIBRARY');
if ($library === '' || !str_starts_with($library, '/') || !is_file($library)) {
    http_response_code(503);
    echo json_encode(['error' => ['code' => 'configuration_unavailable', 'message' => 'Wallet authentication is unavailable.']]);
    exit;
}
require $library;

try {
    $response = sickwallet_request('GET', '/api/v1/jwks');
    echo json_encode($response, JSON_THROW_ON_ERROR);
} catch (Throwable $error) {
    http_response_code(503);
    echo json_encode(['error' => ['code' => 'authentication_unavailable', 'message' => 'Wallet authentication is unavailable.']]);
}
