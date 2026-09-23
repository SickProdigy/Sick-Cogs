# Navidrome

Connect each Discord guild to either a bot-owner-approved Navidrome profile or an isolated
guild-managed public HTTPS server. Members can inspect the
library and recent albums, administrators can manage Discord-linked Navidrome users, and new albums
can be announced without duplicate posts after reloads or restarts.

Library browsing uses Navidrome's Subsonic-compatible API. User administration uses the same native
`/auth/login` and `/api/user` routes as Navidrome's web interface because Navidrome returns HTTP
501 for the Subsonic user-write endpoints. The cog capability-tests the native surface and reports a
clear error when a server version does not provide it.

## Owner setup

Run `[p]navidromeset setup` as the bot owner and choose **Connect server**. Enter a short
connection name, the Navidrome URL, and a dedicated Navidrome administrator username/password.
The form tests both library access and native user management before saving anything, then selects
that connection for the Discord server.

The equivalent text-command fallback is:

```text
[p]set api navidrome_home username,SERVICE_USER password,SERVICE_PASSWORD
[p]navidromeowner connection add home https://music.example.com
[p]navidromeowner connection test home
[p]navidromeowner connection list
[p]navidromeset connection home
```

HTTPS is required by default. A bot owner can pass `true` as the final argument to explicitly allow
HTTP for a trusted private deployment. The configured account must be a Navidrome administrator to
use account provisioning and password management. Use a dedicated bot administrator account rather
than a personal account. A read-only account is sufficient only when user management will not be used.

## Guild-managed connections

Guild-managed connections are disabled by default. The bot owner may opt in globally:

```text
[p]navidromeowner guildconnections true
```

A member with Manage Server can then open `[p]navidromeset setup` and choose **Connect this server**.
The form accepts a public HTTPS Navidrome URL and dedicated administrator credentials. Credentials
are stored in the guild-isolated `navidrome_guild_<guild_id>` shared-token namespace and are never
displayed after submission. The cog validates library and native user access before keeping the
connection; a failed test restores the previous connection and credentials. Creating or replacing
the connection requires typing `CONNECT`, and connection tests are limited to one attempt per guild
per minute.

Guild-managed destinations must resolve only to public IP addresses. Resolution is checked when the
connection is saved and again before requests. HTTP, loopback, private, link-local, multicast, and
reserved destinations are rejected. Trusted LAN/HTTP deployments must continue using a bot-owner
profile; private guild-managed destinations are intentionally not allowlistable. DNS is checked
before every request and the connected peer address is checked before response data is accepted.
JSON and artwork responses have fixed size limits and all HTTP operations use bounded timeouts.
Announcements are disabled after three consecutive backend failures instead of retrying indefinitely.
Switching to a different server clears stale Discord-to-Navidrome mappings and disables
announcements. Disconnect with `[p]navidromeset disconnect confirm`; this also deletes the guild-owned
credentials.

## Server setup and optional notifications

Open the guided panel with `[p]navidromeset setup`. The primary controls connect or select a
Navidrome server and open **Manage users**. The bot owner can browse every account already present on
Navidrome, including accounts with no Discord link, and can create, edit, reset, or delete standalone
users. Its Discord-member picker remains available for accounts that should be linked to a member.

Recently-added album notifications are optional. Servers that want them can choose a channel, set
the interval, preview an album, and enable announcements. Equivalent text commands remain available:

```text
[p]navidromeset connection home
[p]navidromeset channel #new-music
[p]navidromeset interval 60
[p]navidromeset preview
[p]navidromeset enable
```

Enabling announcements records the current newest albums as a baseline. It does not publish the
existing library. Each guild keeps its own channel, schedule, and bounded album-ID history, even
when guilds share an approved connection.

## Member commands

- `[p]navidrome` or `[p]navidrome info` - show server/API version and library counts.
- `[p]navidrome stats` - alias for `info`.
- `[p]navidrome recent [count]` - show 1-10 recently added albums.
- `[p]navidrome account` - show your Discord-linked Navidrome username.

## Administration commands

- `[p]navidromeset` - show current settings.
- `[p]navidromeset setup` - open the guided interactive setup panel.
- `[p]navidromeset connection <name>` - select an approved connection.
- `[p]navidromeset disconnect confirm` - clear local mappings/history and delete guild-owned credentials.
- `[p]navidromeset channel [channel]` - choose the announcement channel.
- `[p]navidromeset interval <15-1440>` - set the polling interval in minutes.
- `[p]navidromeset enable|disable` - control scheduled album posts.
- `[p]navidromeset preview` - preview the latest album without changing delivery history.
- `[p]navidromeset status` - show configuration and the last successful check.
- `[p]navidromeset managerrole [role]` - delegate routine configuration without credential access.

### Linked user commands

- `[p]navidromeset user list` - list accounts created for this Discord server.
- `[p]navidromeset user info <member>` - read the linked remote account.
- `[p]navidromeset user create <member> <username> [email]` - create a non-admin user and DM a temporary password.
- `[p]navidromeset user name <member> <display name>` - change the display name.
- `[p]navidromeset user email <member> <email>` - change the email address.
- `[p]navidromeset user password <member>` - generate and DM a new temporary password.
- `[p]navidromeset user unlink <member>` - remove only the Discord mapping.
- `[p]navidromeset user delete <member> confirm` - permanently delete the remote user and mapping.

## Account security and scope

Account mappings are scoped to the Discord guild and contain only the Discord user ID, Navidrome
user ID, username, and creation timestamp. Generated passwords are sent by DM and are never stored
in Red configuration or logs. Newly created users are always non-admin. If initial credential
delivery fails, creation is rolled back. Deletion requires an explicit `confirm` argument, while
`unlink` keeps the remote account.

A guild administrator can manage only accounts mapped through that guild. The owner connection test
verifies that the configured credentials can access native user management. Removing an owner-managed
connection profile does not delete its shared API tokens; the bot owner removes those separately
through Red. Guild-owned credentials are deleted on confirmed disconnect or when the bot leaves the
guild.

An optional manager role may use routine settings and account tools, but creating, replacing, or
disconnecting a guild-owned connection always requires Manage Server. Red data-deletion requests
remove the requesting Discord user's local account mapping; they do not delete the remote Navidrome
account. Removing the bot from a guild clears that guild's configuration and guild-token namespace.

## Optional Lidarr requests

Lidarr integration is optional. Navidrome browsing and announcements continue normally without it.
Requests always search Navidrome first and stop if the artist or album is already available. Lidarr
does not acquire individual tracks directly, so members request an artist or album with:

```text
[p]navidrome request artist <name>
[p]navidrome request album <title and artist>
```

The command shows the selected Lidarr result, the exact `discord` and sanitized
`discord-user-<username>` attribution tags, and an explicit confirmation button before making a
change. Existing tags are retained. Duplicate requests report the already-managed state and add the
new requester tag to the managed artist when possible.

For a bot with one owner-managed connection, store `lidarr_url` and `lidarr_api_key` in the universal
`lidarr` Red shared-token namespace. Multi-profile bots use each connection's `navidrome_<name>`
namespace so credentials cannot cross profiles. Then validate the root folder and profiles:

```text
[p]navidromeowner lidarr configure [name] [root-folder] [quality-profile-id] [metadata-profile-id] [allow-http]
```

When only one Navidrome profile exists, omit every argument: the command selects that profile,
polls Lidarr, uses its first accessible root folder and configured default profiles, and infers HTTP
from the stored URL. With multiple profiles, provide the Navidrome profile name. Optional explicit
values override discovery.

For a guild-managed public HTTPS connection, use **Connect Lidarr** in `[p]navidromeset setup`. Its
URL and API key stay in the guild's isolated shared-token namespace. The form validates the root
folder and profiles before saving and restores the previous configuration on failure. Changing the
active Navidrome backend clears unrelated guild-owned Lidarr credentials.

Administrators configure request policy with:

```text
[p]navidromeset lidarr identity <linked_required|linked_optional|discord_only>
[p]navidromeset lidarr requesterrole [role]
[p]navidromeset lidarr limits <cooldown-seconds> <daily-limit>
[p]navidromeset lidarr audit [count]
```

`linked_required` is the default. A linked identity is revalidated against Navidrome at confirmation
time. All identity modes retain role checks, cooldowns, limits, confirmation, tags, and audit logging.
The bounded audit stores Discord identity, an optional linked Navidrome identity, requested media,
Lidarr foreign ID, timestamp, and outcome; it never stores credentials. Red user-data deletion
anonymizes the user's historical attribution and removes their local Navidrome mapping. Lidarr tags
are shared provenance metadata rather than authentication and are not renamed when usernames change.
