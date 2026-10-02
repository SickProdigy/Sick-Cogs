# Companion Website Relay Setup

The shared companion uses a small public PHP/MySQL relay for one-time protected handoffs. The bot registers encrypted payloads through authenticated outbound HTTPS; browsers consume opaque handles once. There is no inbound cog listener, website pairing credential, or Discord OAuth bridge.

## Setup

Requirements: PHP 8.0+, PDO MySQL, OpenSSL, MySQL/MariaDB, and HTTPS. Publish the complete `web/` directory so the access-denied `server/` support files remain beside the public endpoints.

1. Create an empty database and least-privilege database user.
2. Visit `/cryptowallet/setup/` over HTTPS and enter the database details.
3. The wizard applies the numbered database migrations, generates a relay secret, writes `recovery-config.local.php` with mode `0600`, and creates `setup-locked`.
4. Run the displayed owner-only Red API-token command privately, then delete that Discord message.

Fresh installs use the same numbered migrations as upgrades. For an existing installation, back up the database and private configuration, deploy the complete updated `web/` directory, then visit `/cryptowallet/setup/` over HTTPS. **Run database update** appears when the uploaded migration bundle has not been checked by this deployment. A successful update check records the bundle fingerprint locally, including when the database was already current, so the button stays hidden until a future bundle changes. The updater applies only bundled missing migrations and reveals no database or migration details in the browser.

The migration runner takes database and filesystem locks and records migration checksums in `sickwallet_schema_migrations`. Never edit a migration after release; add the next numbered, restart-safe SQL file. `recovery-schema.sql` is a readable current-schema snapshot, not the upgrade mechanism.

Alternatively configure the database and relay manually:

```text
SICKWALLET_RECOVERY_RELAY_SECRET=<random secret of at least 32 characters>
SICKWALLET_DATABASE_DSN=mysql:host=127.0.0.1;dbname=sickwallet;charset=utf8mb4
SICKWALLET_DATABASE_USER=<least-privilege database user>
SICKWALLET_DATABASE_PASSWORD=<database password>
SICKWALLET_TRUSTED_CLIENT_IP_SERVER_KEY=<optional trusted proxy-overwritten PHP server key>
```

Store the same relay secret only in Red shared API tokens:

```text
[p]set api cryptowallet_relay secret <same random secret>
```

`web/api/recovery-handoff.php` atomically consumes recovery, TokenFactory external-wallet, and Clanker external-wallet handles. `web/api/tokenfactory-result.php` stores requester-bound TokenFactory results. `web/api/totp-enrollment.php` accepts only bounded RSA-OAEP ciphertext from a one-time browser enrollment and releases it once to an HMAC-authenticated bot poll; it never receives the plaintext seed or a TOTP code. `web/api/jwks.php` publishes only the public ES256 key used for direct signed authorization handoffs. `web/api/polymarket-connect.php` compares the official browser geoblock IP with the server-observed client address, encrypts eligible signer proofs at rest, and releases each five-minute result once to an authenticated bot poll. Set `SICKWALLET_TRUSTED_CLIENT_IP_SERVER_KEY` only when a trusted reverse proxy overwrites that PHP server variable; the default uses `REMOTE_ADDR` and fails closed on an address mismatch.

`[p]walletset view` reports relay readiness without showing secrets. Rotate the relay secret in both server-side stores together; existing unconsumed handles then become unusable. Never place the secret, CDP credentials, signing keys, or wallet secrets in Git, Discord, URLs, browser assets, or logs.
