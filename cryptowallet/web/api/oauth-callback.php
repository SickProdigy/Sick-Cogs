<?php
declare(strict_types=1);

require_once dirname(__DIR__) . '/server/companion.php';

header('Cache-Control: no-store');
header('Referrer-Policy: no-referrer');
header('X-Content-Type-Options: nosniff');

$code = (string) ($_GET['code'] ?? '');
$state = (string) ($_GET['state'] ?? '');
if ($_SERVER['REQUEST_METHOD'] !== 'GET' || $code === ''
    || !preg_match('/^[A-Za-z0-9_-]{32,128}$/D', $state)
    || strlen($code) > 4096) {
    http_response_code(400);
    echo 'Discord did not return a valid wallet-session authorization.';
    exit;
}

try {
    sickwallet_forward_browser_response(
        '/oauth/callback?code=' . rawurlencode($code) . '&state=' . rawurlencode($state)
    );
} catch (Throwable) {
    http_response_code(502);
    echo 'The protected wallet service is temporarily unavailable.';
}
