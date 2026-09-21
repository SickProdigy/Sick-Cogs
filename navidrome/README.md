# Navidrome

Connect each Discord guild to one bot-owner-approved Navidrome server. Members can view basic
library information and recently added albums, while administrators can publish new albums to a
configured channel without duplicate posts after reloads or restarts.

This initial release is intentionally read-only. Navidrome's documented compatibility surface
supports the media calls used here, but not the account creation and password-management calls
needed for safe provisioning. Those features will be added behind a version-checked provider
adapter rather than relying on undocumented internal endpoints.

## Owner setup

Choose a short connection name such as `home`. In a DM or other private channel, store a dedicated
Navidrome service account through Red's shared token store:

```text
[p]set api navidrome_home username,SERVICE_USER password,SERVICE_PASSWORD
```

Then approve and test its endpoint:

```text
[p]navidromeowner connection add home https://music.example.com
[p]navidromeowner connection test home
[p]navidromeowner connection list
```

HTTPS is required by default. A bot owner can pass `true` as the final argument to explicitly allow
HTTP for a trusted private deployment. Avoid using an administrator account when a read-only account
with access to the intended libraries is sufficient.

## Guild setup

Open the guided setup panel with `[p]navidromeset setup`. It lets an administrator choose an approved connection and channel, set the interval, test the connection, preview an album, and enable or disable announcements. The equivalent text commands remain available:

```text
[p]navidromeset connection home
[p]navidromeset channel #new-music
[p]navidromeset interval 60
[p]navidromeset preview
[p]navidromeset enable
```

Enabling announcements records the current newest albums as a baseline. It does not publish the
existing library. Each guild keeps its own announcement channel, schedule, and bounded album-ID
history even when guilds share an approved connection.

## Member commands

- `[p]navidrome` or `[p]navidrome info` — show server/API version and accessible library counts.
- `[p]navidrome stats` — alias for `info` in the initial release.
- `[p]navidrome recent [count]` — show 1–10 recently added albums.

## Administration commands

- `[p]navidromeset` — show current settings.
- `[p]navidromeset connection <name>` — select an approved connection and leave announcements off.
- `[p]navidromeset disconnect` — clear this guild's selection and announcement history.
- `[p]navidromeset channel [channel]` — choose the album announcement channel.
- `[p]navidromeset interval <15-1440>` — set the polling interval in minutes.
- `[p]navidromeset enable|disable` — control scheduled album posts.
- `[p]navidromeset preview` — send the latest album card without changing delivery history.
- `[p]navidromeset status` — show the guild's configuration and last successful check.

Bot-owner connection commands also include `[p]navidromeowner connection test <name>` and `[p]navidromeowner connection list`.

Credentials never appear in guild configuration or bot responses. Removing a connection profile
does not delete its shared API tokens; the bot owner can remove those separately with Red's API-token
management commands.
