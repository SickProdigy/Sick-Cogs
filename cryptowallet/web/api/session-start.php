<?php
declare(strict_types=1);

require_once dirname(__DIR__) . '/server/companion.php';

header('Cache-Control: no-store');
header('Referrer-Policy: no-referrer');
header('X-Content-Type-Options: nosniff');

$token = (string) ($_GET['token'] ?? '');
if ($_SERVER['REQUEST_METHOD'] !== 'GET'
    || !preg_match('/^[A-Za-z0-9_-]{32,128}$/D', $token)) {
    http_response_code(404);
    echo 'This protected wallet session is invalid or unavailable.';
    exit;
}

try {
    sickwallet_forward_browser_response('/session/' . rawurlencode($token));
} catch (Throwable) {
    http_response_code(502);
    echo 'The protected wallet service is temporarily unavailable.';
}
