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

$jwksPath = dirname(__DIR__) . '/jwks.json';
if (!is_file($jwksPath) || !is_readable($jwksPath)) {
    http_response_code(503);
    echo json_encode(['error' => ['code' => 'configuration_unavailable', 'message' => 'Wallet authentication is unavailable.']]);
    exit;
}
$contents = file_get_contents($jwksPath);
$decoded = is_string($contents) ? json_decode($contents, true) : null;
if (!is_array($decoded) || !isset($decoded['keys']) || !is_array($decoded['keys'])) {
    http_response_code(503);
    echo json_encode(['error' => ['code' => 'authentication_unavailable', 'message' => 'Wallet authentication is unavailable.']]);
    exit;
}
echo json_encode($decoded, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES);
