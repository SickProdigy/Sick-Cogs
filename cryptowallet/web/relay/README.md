# Public relay endpoints

Upload only the PHP endpoint files in this directory beneath
`https://sickgaming.net/cryptowallet/relay/`. Do not place the database schema, configuration,
database password, or server library in the public document root.

The endpoints are:

- `pair.php`: atomically exchanges a ten-minute website-generated code for one installation.
- `poll.php`: holds an authenticated cog request for up to 15 seconds and leases one message.
- `complete.php`: accepts a correlated result only with the active lease token.

Each cog request is signed over its timestamp, nonce, HTTP method, exact URL path, and body digest.
Nonces are stored for the authentication window to reject replay. Messages are bounded, expire,
and can be retried after a lease expires.

Set `SICKWALLET_RELAY_LIBRARY` in the website PHP environment to the absolute, server-only copy
of `server/relay.php`. The cog uses only outbound HTTPS; these endpoints never connect to the bot.
