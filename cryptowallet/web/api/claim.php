<?php
declare(strict_types=1);

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');
header('X-Content-Type-Options: nosniff');

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    http_response_code(405);
    header('Allow: POST');
    echo json_encode(['error' => ['code' => 'method_not_allowed', 'message' => 'POST is required.']]);
    exit;
}

$library = (string) getenv('SICKWALLET_SERVER_LIBRARY');
if ($library === '' || !str_starts_with($library, '/') || !is_file($library)) {
    http_response_code(503);
    echo json_encode(['error' => ['code' => 'configuration_unavailable', 'message' => 'Wallet claiming is unavailable.']]);
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
    $input = json_decode((string) file_get_contents('php://input'), true, 8, JSON_THROW_ON_ERROR);
    $accessToken = (string) ($input['access_token'] ?? '');
    if ($accessToken === '' || strlen($accessToken) > 16384) {
        throw new InvalidArgumentException('Invalid access token.');
    }
    $response = sickwallet_request(
        'POST',
        '/api/v1/claim',
        ['access_token' => $accessToken],
        true,
        ['Cookie: ' . $cookieName . '=' . $browserToken]
    );
    echo json_encode($response, JSON_THROW_ON_ERROR);
} catch (Throwable $error) {
    http_response_code(403);
    echo json_encode(['error' => ['code' => 'claim_rejected', 'message' => 'Wallet control could not be verified.']]);
}
