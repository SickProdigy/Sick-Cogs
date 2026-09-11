<?php
declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
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
    echo json_encode(['error' => ['code' => 'configuration_unavailable', 'message' => 'Wallet service is unavailable.']]);
    exit;
}
require $library;

$cookieName = '__Secure-sickwallet-session';
$browserToken = (string) ($_COOKIE[$cookieName] ?? '');
if (!preg_match('/^[A-Za-z0-9_-]{32,128}$/D', $browserToken)) {
    http_response_code(401);
    echo json_encode(['error' => ['code' => 'session_unavailable', 'message' => 'The wallet session is missing, invalid, or expired.']]);
    exit;
}

try {
    $response = sickwallet_request(
        'GET',
        '/api/v1/session',
        null,
        true,
        ['Cookie: ' . $cookieName . '=' . $browserToken]
    );
    echo json_encode($response, JSON_THROW_ON_ERROR);
} catch (Throwable $error) {
    http_response_code(401);
    echo json_encode(['error' => ['code' => 'session_unavailable', 'message' => 'The wallet session is missing, invalid, or expired.']]);
}
