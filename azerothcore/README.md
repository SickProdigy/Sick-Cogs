# AzerothCore

This cog uses AzerothCore's SOAP interface as its transport.

It issues console commands to the server via SOAP (no separate REST bridge or telnet/RA needed).

## What it does

- `azerothcore info` shows general server information.
- `azerothcore online` shows real online character names when the SOAP account can run the online-list command, then falls back to connected player counts.
- `azerothcore playerbots` shows online PlayerBots separately from real players.
- `azerothcore soapcheck` tests whether the configured SOAP host and port are reachable.
- `azerothcore createuser` creates a new account, but only for approved Discord roles. Staff can optionally target another Discord member so the bot DMs that member directly.

Commands can also be run with the short aliases `wow` or `ac`, such as `wow online` or `ac online`.

## Setup

1. Configure the SOAP endpoint the cog should use with `wowset soap_url <ip:port>`.
2. (Optional) Configure SOAP basic auth using `wowset soap_auth <user> <pass>`.
3. Check the current configuration with `wowset view`, then test connectivity with `azerothcore soapcheck`.
4. Add display text with `wowset servername`, `wowset realmlist`, and `wowset infodescription`.
5. As the Red bot owner, enable account management in the trusted Discord server with `azerothcore trustguild true`.
6. Add the Discord roles that are allowed to create accounts with `wowset accountcreationrole <role...>`.
6. (Optional) Adjust advanced SOAP behavior with `wowset timeout`, `wowset onlinecommand`, `wowset infocommand`, or `wowset createcommand`.

### Helpful examples

- `wowset realmlist wow.sickgaming.net`
- `wowset servername My Realm`
- `wowset soap_url 192.168.86.139:17878`
- `wowset soap_auth soapuser abcd1234`
- `wowset accountcreationrole @admin`
- `wowset timeout 10`
- `wowset onlinecommand account onlinelist`
- `wowset playerbotprefix RNDBOT`
- `azerothcore createuser playername player@example.com @player`
- `azerothcore accountcreate playername player@example.com @player`
- `azerothcore onlineprobe`

Account creation roles can be provided as role mentions, role IDs, or exact role names.

### Restricted Commands

Account-changing commands default to disabled in every Discord server. The Red bot owner must run `azerothcore trustguild true` in each explicitly trusted guild. `azerothcore createuser`, `azerothcore accountcreate`, and `azerothcore raw` are then restricted to server owners, server admins/managers, and configured account-creation roles.

`azerothcore createuser` and `azerothcore accountcreate` generate a password, create the AzerothCore account through SOAP, and DM a welcome packet with the username, password, configured realmlist, and bundled banner image. If a Discord member is provided as the final argument, that member gets the DM; otherwise the command author gets it. Public channel confirmations never include the generated password.

If your server exposes SOAP on a non-default host/port, update the SOAP URL accordingly.

`azerothcore info` pulls technical status from the `server info` SOAP command. AzerothCore may not return a friendly realm name or public description through SOAP, so use these settings for display:

```text
wowset servername <name>
wowset infodescription <description>
wowset realmlist <text>
```

`azerothcore online` uses the configured server name in the embed title. If no server name is set, it uses `World of Warcraft`. The configured realmlist is shown as a small footer when available, while the full description and connection details stay focused on `azerothcore info`.

### SOAP URL and Basic Auth

AzerothCore's SOAP interface commonly listens on port 7878 inside the server/container. This repository can publish that as another host port, such as `DOCKER_SOAP_EXTERNAL_PORT=17878`. If your host IP is `192.168.86.139` and host port `17878` maps to the SOAP service, configure the cog like this:

`wowset soap_url 192.168.86.139:17878`
`wowset soap_auth soapuser abcd1234`

The cog also supports Basic Auth credentials embedded in the URL, but separate `soap_auth` configuration is preferred so the bot does not repeat credentials in normal setup responses.

If `azerothcore info` or `azerothcore online` times out, check that the configured IP/hostname is reachable from the Discord bot host, the SOAP port is published or available on the Docker network, and SOAP is enabled in AzerothCore.
Use `azerothcore soapcheck` to test the configured host and port from the bot before troubleshooting SOAP credentials or command permissions.

## Private ac-api-v1 connection

AzerothCore 1.2 uses the private ac-api-v1 module for character profiles, named online
characters, and leaderboards. The Discord bot does not connect to
MariaDB/MySQL and must never receive database credentials. SOAP remains independent and can
provide server information, account creation, raw administrative commands, and Playerbots
output that the API does not yet expose.

Install and configure /sickprodigy/ac-api-v1 inside the AzerothCore worldserver first. The
module needs its own dedicated SELECT-only CommunityDatabaseInfo account for
acore_characters; that credential stays in the module configuration.

### Red shared API tokens

Use Red's owner-only shared-token command in a private channel or DM. The namespace is
azerothcore_api:

    [p]set api azerothcore_api base_url http://ac-worldserver:8088
    [p]set api azerothcore_api community_token REPLACE_WITH_COMMUNITY_READ_TOKEN
    [p]set api azerothcore_api timeout 10

Use the private Docker/LAN/WireGuard address on which ac-api-v1 listens. Never embed a token
in base_url. community_token enables profiles, online characters, realm status, and
leaderboards. Account creation uses the separately configured SOAP connection.

Run [p]azerothcore apicheck to verify capability discovery and realm status. The legacy
databasecheck and dbcheck names remain aliases during migration.

Existing SOAP features remain available without ac-api-v1. The `[p]azerothcore online`
command uses ac-api-v1 when configured so it can distinguish humans from Playerbots, then
falls back to SOAP on installations without the module. `[p]azerothcore apionline` explicitly
requires the API.

The `[p]azerothcore info` card always uses SOAP for server identity, build, and uptime. When
ac-api-v1 is configured and reachable, it enriches the card with separately classified
`Players Online`, `AI Companions Online`, and human-only `Player Characters Created` values.
Older API responses without authoritative audience-filtered realm totals are rejected rather
than displayed as player counts. Without the module, the card falls back to SOAP's `Connected
Sessions` and `Characters in World` values.

AI classification comes from ac-api-v1 authoritative Playerbots data. Human and AI
leaderboards are requested separately with the API audience filter; the cog never infers AI
status from character or account names. Online cards show at most 10 names per audience and summarize the
remainder.

## Troubleshooting

`azerothcore soapcheck` only tests whether the configured host and port accept a TCP connection. A passing TCP check does not prove that AzerothCore SOAP accepted the request. Use `azerothcore info` or `azerothcore online` to test the actual SOAP command path.

Common results:

- `SOAP URL has not been configured yet`: set the endpoint with `wowset soap_url <ip:port>`.
- `host answered, but nothing is listening`: the IP is reachable, but that port is not bound on that interface.
- `connection was reset`: something accepted the connection and closed it before returning a SOAP response. Check `SOAP.Enabled`, `SOAP.IP`, `SOAP.Port`, and whether the port is really the AzerothCore SOAP port.
- `HTTP 401` or auth-related SOAP fault: check `wowset soap_auth <user> <pass>`.
- `HTTP 403` or permission-related SOAP fault: the account likely needs GM/security access in `account_access`.
- `HTTP 500` with a SOAP fault: SOAP is responding; read the fault text for the next fix.
- `Command 'players' does not exist`: reset the old command with `wowset onlinecommand reset`, or configure another command that exists on your AzerothCore build.

`azerothcore online` defaults to `account onlinelist` so it can show who is online instead of only counts. AzerothCore documents this as a higher-security GM command, so the SOAP account may need elevated access or the command security may need to be adjusted in AzerothCore's command table. If the online-list command cannot run or does not return names, `azerothcore online` falls back to `server info` counts when possible.

PlayerBot accounts are filtered out of the main `azerothcore online` character list when their account names start with a configured prefix. The default prefix is `RNDBOT`. `azerothcore online` still shows a count of online AI players under the real-player list. Use `azerothcore playerbots` to show those online bot characters separately. Manage prefixes with:

```text
wowset playerbotprefix
wowset playerbotprefix RNDBOT
wowset playerbotprefix RNDBOT BOT
wowset playerbotprefix reset
wowset playerbotprefix clear
```

Use `azerothcore onlineprobe` to test the default online-list command, chat-style `.account onlinelist`, and `server info` from Discord. To test a custom read-only command, run `azerothcore onlineprobe <command>`, then configure a working parser-friendly command with `wowset onlinecommand <command>`.

The cog intentionally has no direct database access. Named characters, separated human and AI online lists, and audience-filtered leaderboards come through the private ac-api-v1 module. SOAP remains available for administrative commands and legacy Playerbots output.

`server info` can usually show connected players, characters in world, connection peak, uptime, and build/revision details, but it does not include character or account names. If your server has a custom command that lists character names, set it with:

```text
wowset onlinecommand <command>
```

Reset command templates with:

```text
wowset onlinecommand reset
wowset infocommand reset
wowset createcommand reset
```

Useful Debian checks:

```bash
ss -ltnp | grep -E '17878|7878'
docker compose ps
docker compose port worldserver 7878
docker logs ac-worldserver --tail=200 | grep -i soap
```

A working Docker publish usually maps the host port to the container's SOAP port:

```text
0.0.0.0:17878->7878/tcp
```

AzerothCore SOAP settings should be in the active `worldserver.conf`, for example:

```ini
SOAP.Enabled = 1
SOAP.IP = "0.0.0.0"
SOAP.Port = 7878
```

If `SOAP.IP` is `127.0.0.1` inside the container, Docker port publishing may accept a connection without the SOAP service being reachable correctly from outside the container. Use `0.0.0.0` when exposing SOAP through Docker, then restart the worldserver container.

SOAP accounts need enough access to run console commands. For remote SOAP, the account should usually have realm `-1`, for example from the worldserver console:

```text
account set gmlevel <username> 3 -1
```

After changing `worldserver.conf`, restart the worldserver container before testing again:

```bash
docker compose restart worldserver
```

## Using SOAP (examples and Docker notes)

Prefer running the Discord bot on the same Docker Compose network as the AzerothCore containers so you don't need to expose SOAP publicly. If you must access SOAP from the host, ensure the host port is published to the container's SOAP port.

Docker example (publish SOAP port):

```yaml
services:
  worldserver:
    ports:
      - "${DOCKER_WORLD_EXTERNAL_PORT:-8085}:8085"
      - "${DOCKER_AUTH_EXTERNAL_PORT:-3724}:3724"
      - "${DOCKER_SOAP_EXTERNAL_PORT:-17878}:7878"
```

Security note: SOAP credentials are sensitive. Prefer `wowset soap_auth` over embedding passwords in URLs or shell history.
