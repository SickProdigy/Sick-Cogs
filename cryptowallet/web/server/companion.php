<?php
declare(strict_types=1);

const SICKWALLET_PROTOCOL = 'v1';

function sickwallet_backend_url(): string
{
    $url = rtrim((string) getenv('SICKWALLET_BACKEND_URL'), '/');
    if ($url === '' || filter_var($url, FILTER_VALIDATE_URL) === false) {
        throw new RuntimeException('SICKWALLET_BACKEND_URL is missing or invalid.');
    }
    $scheme = strtolower((string) parse_url($url, PHP_URL_SCHEME));
    if ($scheme !== 'https' && getenv('SICKWALLET_ALLOW_INSECURE_PRIVATE') !== '1') {
        throw new RuntimeException('The backend URL must use HTTPS.');
    }
    return $url;
}

function sickwallet_credential_file(): string
{
    $path = (string) getenv('SICKWALLET_CREDENTIAL_FILE');
    if ($path === '' || !str_starts_with($path, '/')) {
        throw new RuntimeException('SICKWALLET_CREDENTIAL_FILE must be an absolute path.');
    }
    return $path;
}

function sickwallet_save_credentials(array $credentials): void
{
    foreach (['installation_id', 'credential', 'deployment_id', 'discord_application_id'] as $key) {
        if (!isset($credentials[$key]) || (string) $credentials[$key] === '') {
            throw new RuntimeException("Pairing response omitted {$key}.");
        }
    }
    $path = sickwallet_credential_file();
    $directory = dirname($path);
    if (!is_dir($directory) || !is_writable($directory)) {
        throw new RuntimeException('Credential directory does not exist or is not writable.');
    }
    $temporary = tempnam($directory, '.sickwallet-');
    if ($temporary === false) {
        throw new RuntimeException('Could not create a temporary credential file.');
    }
    try {
        $json = json_encode($credentials, JSON_THROW_ON_ERROR | JSON_PRETTY_PRINT);
        if (file_put_contents($temporary, $json . PHP_EOL, LOCK_EX) === false) {
            throw new RuntimeException('Could not write the credential file.');
        }
        chmod($temporary, 0600);
        if (!rename($temporary, $path)) {
            throw new RuntimeException('Could not install the credential file atomically.');
        }
    } finally {
        if (is_file($temporary)) {
            unlink($temporary);
        }
    }
}

function sickwallet_load_credentials(): array
{
    $path = sickwallet_credential_file();
    if (!is_file($path)) {
        throw new RuntimeException('The companion website is not paired.');
    }
    $mode = fileperms($path);
    if ($mode !== false && (($mode & 0077) !== 0)) {
        throw new RuntimeException('Credential file permissions must not grant group or other access.');
    }
    return json_decode((string) file_get_contents($path), true, 16, JSON_THROW_ON_ERROR);
}

function sickwallet_request(string $method, string $path, ?array $json = null, bool $signed = true): array
{
    if (!str_starts_with($path, '/') || str_contains($path, '?')) {
        throw new RuntimeException('Backend path must be absolute and cannot contain a query string.');
    }
    $body = $json === null ? '' : json_encode($json, JSON_THROW_ON_ERROR);
    $headers = ['Accept: application/json'];
    if ($json !== null) {
        $headers[] = 'Content-Type: application/json';
    }
    if ($signed) {
        $credentials = sickwallet_load_credentials();
        $timestamp = (string) time();
        $nonce = rtrim(strtr(base64_encode(random_bytes(24)), '+/', '-_'), '=');
        $canonical = implode("\n", [
            SICKWALLET_PROTOCOL,
            $timestamp,
            $nonce,
            strtoupper($method),
            $path,
            hash('sha256', $body),
        ]);
        $headers[] = 'X-SickWallet-Installation: ' . $credentials['installation_id'];
        $headers[] = 'X-SickWallet-Timestamp: ' . $timestamp;
        $headers[] = 'X-SickWallet-Nonce: ' . $nonce;
        $headers[] = 'X-SickWallet-Signature: ' . hash_hmac('sha256', $canonical, $credentials['credential']);
    }
    $handle = curl_init(sickwallet_backend_url() . $path);
    curl_setopt_array($handle, [
        CURLOPT_CUSTOMREQUEST => strtoupper($method),
        CURLOPT_HTTPHEADER => $headers,
        CURLOPT_POSTFIELDS => $body,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_TIMEOUT => 15,
    ]);
    $response = curl_exec($handle);
    $status = (int) curl_getinfo($handle, CURLINFO_RESPONSE_CODE);
    $error = curl_error($handle);
    curl_close($handle);
    if ($response === false) {
        throw new RuntimeException('Backend request failed: ' . $error);
    }
    $decoded = json_decode($response, true, 32, JSON_THROW_ON_ERROR);
    if ($status < 200 || $status >= 300) {
        $message = $decoded['error']['message'] ?? "Backend returned HTTP {$status}.";
        throw new RuntimeException((string) $message);
    }
    return $decoded;
}
