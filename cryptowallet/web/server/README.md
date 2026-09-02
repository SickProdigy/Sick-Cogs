# Companion Website Server Setup

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
