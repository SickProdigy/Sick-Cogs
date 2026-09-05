# Companion Website Server Setup

The recovery page requires a small public PHP/MySQL relay. It accepts authenticated outbound
registration from the bot and atomically consumes opaque browser handles once. It is not required
for wallet provisioning, authorization, balances, activity, or sends. The legacy inbound cog
listener and pairing tools below remain optional and must not be publicly exposed.

## One-time recovery relay

Requirements: PHP 8.0+, PDO MySQL, OpenSSL, MySQL/MariaDB, and HTTPS. Apply
`recovery-schema.sql`, publish `web/api/recovery-handoff.php` with the other public website assets,
and configure the web runtime:

```text
SICKWALLET_RECOVERY_RELAY_SECRET=<random secret of at least 32 characters>
SICKWALLET_DATABASE_DSN=mysql:host=127.0.0.1;dbname=sickwallet;charset=utf8mb4
SICKWALLET_DATABASE_USER=<least-privilege database user>
SICKWALLET_DATABASE_PASSWORD=<database password>
```

Store the same relay secret only in Red's shared API-token store:

```text
[p]set api cryptowallet_relay secret <same random secret>
```

`[p]walletset view` reports whether the relay and HTTPS approval URL are configured without
displaying secrets. Recovery fails closed until both sides are configured. Rotate the relay secret
by changing both server-side stores together; existing unconsumed links become unusable. Do not
put the secret in Git, Discord messages, URLs, browser assets, or logs.

## Legacy private companion infrastructure

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

- `web/api/jwks.php` publishes only the ES256 public key from the static `web/jwks.json` file.
  Configure its public HTTPS URL in CDP; it does not require companion pairing.
- `web/api/auth-token.php` accepts POST only, requires the verified HttpOnly browser-session
  cookie, and returns a short-lived user-bound CDP JWT. It never receives the signing key.
- `web/api/claim.php` forwards the short-lived CDP access token through a signed request. The
  cog validates that token with CDP and records a claim only when the CDP user and smart-account
  address exactly match the provisioned profile.

Add the website's exact HTTPS origin to the CDP project's domain allowlist before testing.

The private JWT signing key remains in the bot's Red shared API-token store. Do not copy it into
this website directory or its environment.
