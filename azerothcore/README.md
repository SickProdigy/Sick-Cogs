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

1. Configure the SOAP endpoint the cog should use with `azerothcore set soap_url <ip:port>`.
2. (Optional) Configure SOAP basic auth using `azerothcore set soap_auth <user> <pass>`.
3. Check the current configuration with `azerothcore set view`, then test connectivity with `azerothcore soapcheck`.
4. Add display text with `azerothcore set servername`, `azerothcore set realmlist`, and `azerothcore set infodescription`.
5. Add the Discord roles that are allowed to create accounts with `azerothcore set accountcreationrole <role...>`.
6. (Optional) Adjust advanced SOAP behavior with `azerothcore set timeout`, `azerothcore set onlinecommand`, `azerothcore set infocommand`, or `azerothcore set createcommand`.

### Helpful examples

- `azerothcore set realmlist wow.sickgaming.net`
- `azerothcore set servername My Realm`
- `azerothcore set soap_url 192.168.86.139:17878`
- `azerothcore set soap_auth soapuser abcd1234`
- `azerothcore set accountcreationrole @admin`
- `azerothcore set timeout 10`
- `azerothcore set onlinecommand account onlinelist`
- `azerothcore set playerbotprefix RNDBOT`
- `azerothcore createuser playername player@example.com @player`
- `azerothcore accountcreate playername player@example.com @player`
- `azerothcore onlineprobe`

Account creation roles can be provided as role mentions, role IDs, or exact role names.

### Restricted Commands

`azerothcore createuser`, `azerothcore accountcreate`, and `azerothcore raw` are restricted to server owners, server admins/managers, and configured account-creation roles.

`azerothcore createuser` and `azerothcore accountcreate` generate a password, create the AzerothCore account through SOAP, and DM a welcome packet with the username, password, configured realmlist, and bundled banner image. If a Discord member is provided as the final argument, that member gets the DM; otherwise the command author gets it. Public channel confirmations never include the generated password.

If your server exposes SOAP on a non-default host/port, update the SOAP URL accordingly.

`azerothcore info` pulls technical status from the `server info` SOAP command. AzerothCore may not return a friendly realm name or public description through SOAP, so use these settings for display:

```text
azerothcore set servername <name>
azerothcore set infodescription <description>
azerothcore set realmlist <text>
```

`azerothcore online` uses the configured server name in the embed title. If no server name is set, it uses `World of Warcraft`. The configured realmlist is shown as a small footer when available, while the full description and connection details stay focused on `azerothcore info`.

### SOAP URL and Basic Auth

AzerothCore's SOAP interface commonly listens on port 7878 inside the server/container. This repository can publish that as another host port, such as `DOCKER_SOAP_EXTERNAL_PORT=17878`. If your host IP is `192.168.86.139` and host port `17878` maps to the SOAP service, configure the cog like this:

`azerothcore set soap_url 192.168.86.139:17878`
`azerothcore set soap_auth soapuser abcd1234`

The cog also supports Basic Auth credentials embedded in the URL, but separate `soap_auth` configuration is preferred so the bot does not repeat credentials in normal setup responses.

If `azerothcore info` or `azerothcore online` times out, check that the configured IP/hostname is reachable from the Discord bot host, the SOAP port is published or available on the Docker network, and SOAP is enabled in AzerothCore.
Use `azerothcore soapcheck` to test the configured host and port from the bot before troubleshooting SOAP credentials or command permissions.

## Troubleshooting

`azerothcore soapcheck` only tests whether the configured host and port accept a TCP connection. A passing TCP check does not prove that AzerothCore SOAP accepted the request. Use `azerothcore info` or `azerothcore online` to test the actual SOAP command path.

Common results:

- `SOAP URL has not been configured yet`: set the endpoint with `azerothcore set soap_url <ip:port>`.
- `host answered, but nothing is listening`: the IP is reachable, but that port is not bound on that interface.
- `connection was reset`: something accepted the connection and closed it before returning a SOAP response. Check `SOAP.Enabled`, `SOAP.IP`, `SOAP.Port`, and whether the port is really the AzerothCore SOAP port.
- `HTTP 401` or auth-related SOAP fault: check `azerothcore set soap_auth <user> <pass>`.
- `HTTP 403` or permission-related SOAP fault: the account likely needs GM/security access in `account_access`.
- `HTTP 500` with a SOAP fault: SOAP is responding; read the fault text for the next fix.
- `Command 'players' does not exist`: reset the old command with `azerothcore set onlinecommand reset`, or configure another command that exists on your AzerothCore build.

`azerothcore online` defaults to `account onlinelist` so it can show who is online instead of only counts. AzerothCore documents this as a higher-security GM command, so the SOAP account may need elevated access or the command security may need to be adjusted in AzerothCore's command table. If the online-list command cannot run or does not return names, `azerothcore online` falls back to `server info` counts when possible.

PlayerBot accounts are filtered out of the main `azerothcore online` character list when their account names start with a configured prefix. The default prefix is `RNDBOT`. `azerothcore online` still shows a count of online AI players under the real-player list. Use `azerothcore playerbots` to show those online bot characters separately. Manage prefixes with:

```text
azerothcore set playerbotprefix
azerothcore set playerbotprefix RNDBOT
azerothcore set playerbotprefix RNDBOT BOT
azerothcore set playerbotprefix reset
azerothcore set playerbotprefix clear
```

Use `azerothcore onlineprobe` to test the default online-list command, chat-style `.account onlinelist`, and `server info` from Discord. To test a custom read-only command, run `azerothcore onlineprobe <command>`, then configure a working parser-friendly command with `azerothcore set onlinecommand <command>`.

This cog intentionally does not ask for direct database access. If your AzerothCore build cannot return exact names through SOAP, the better long-term path is a small server-side module or API endpoint that exposes a safe online-player response.

`server info` can usually show connected players, characters in world, connection peak, uptime, and build/revision details, but it does not include character or account names. If your server has a custom command that lists character names, set it with:

```text
azerothcore set onlinecommand <command>
```

Reset command templates with:

```text
azerothcore set onlinecommand reset
azerothcore set infocommand reset
azerothcore set createcommand reset
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

Security note: SOAP credentials are sensitive. Prefer `azerothcore set soap_auth` over embedding passwords in URLs or shell history.
