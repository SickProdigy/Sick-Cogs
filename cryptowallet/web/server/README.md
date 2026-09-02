# Companion Website Server Setup

## Outbound PHP/MySQL relay (current direction)

The website is the only public listener. The cog polls ordinary PHP endpoints over outbound HTTPS;
no bot port, tunnel, WebSocket process, or private route is required.

Requirements: PHP 8.1+, PDO MySQL, HTTPS, and a dedicated MySQL database/user. Confirm the target
web PHP runtime—not only CLI—has `pdo_mysql` enabled.

1. Import `relay-schema.sql` into the dedicated database.
2. Copy `relay-config.example.php` outside the public document root, rename it, insert the
   database credentials, and restrict it to the website account.
3. Copy `relay.php`, `relay-pair-code.php`, `relay-probe.php`, and `relay-cleanup.php`
   outside the public document root.
4. Set the PHP environment variables:

   ```text
   SICKWALLET_RELAY_CONFIG=/absolute/private/path/relay-config.php
   SICKWALLET_RELAY_LIBRARY=/absolute/private/path/relay.php
   ```

5. Upload the public files under `web/relay/` to
   `https://sickgaming.net/cryptowallet/relay/`.
6. Schedule `php relay-cleanup.php` every five minutes.

Generate a ten-minute pairing code on the website server with:

```bash
php relay-pair-code.php
```

Enter it through Red's owner-only `[p]set api` modal using service
`cryptowallet_relay_pairing` and the line `code YOUR_CODE`, then run
`[p]walletset relaypair`. Check `[p]walletset relaystatus`.

After pairing, verify the empty transport with:

```bash
php relay-probe.php INSTALLATION_ID
```

The database credential and relay library never enter the public document root. The installation
credential is returned once to the cog over HTTPS and is stored in Red shared API tokens.

## Legacy direct bridge (temporary during route migration)

Deploy this directory outside the public document root. It contains server-only pairing credentials
and request-signing code; only the static files in the parent `web/` directory are public assets.

Requirements: PHP 8.0+, PHP cURL, a private/HTTPS route to the cog, and an owner-only secret directory.

```text
SICKWALLET_BACKEND_URL=https://private-bot-endpoint.example
SICKWALLET_CREDENTIAL_FILE=/srv/sickwallet-secrets/companion.json
SICKWALLET_SERVER_LIBRARY=/srv/sickwallet-server/companion.php
```

HTTPS is required by default. `SICKWALLET_ALLOW_INSECURE_PRIVATE=1` permits HTTP only for an
already protected private/VPN test route; never use it across the public internet.

Pair and verify:

```text
1. Run [p]walletset pair in Discord.
2. Run: php pair.php <one-time-code>
3. Run: php status.php
```

`pair.php` stores credentials atomically with mode `0600`. Neither script runs through the web
SAPI. To rotate, pair again. To revoke, run `[p]walletset unpair` and delete the credential file.

The public `web/api/session.php` endpoint requires `SICKWALLET_SERVER_LIBRARY` to point to this
directory's `companion.php`. It validates the browser-session cookie and forwards it through a
freshly signed server-to-cog request. The installation credential never enters the browser.

Custom authentication and claiming add three public website endpoints:

- `web/api/jwks.php` publishes only the ES256 public key after retrieving it through a signed
  server-to-cog request. Configure its public HTTPS URL in CDP.
- `web/api/auth-token.php` accepts POST only, requires the verified HttpOnly browser-session
  cookie, and returns a short-lived user-bound CDP JWT. It never receives the signing key.
- `web/api/claim.php` forwards the short-lived CDP access token through a signed request. The
  cog validates that token with CDP and records a claim only when the CDP user and smart-account
  address exactly match the provisioned profile.

Add the website's exact HTTPS origin to the CDP project's domain allowlist before testing.

The private JWT signing key remains in the bot's Red shared API-token store. Do not copy it into
this website directory or its environment.
