# GMod

GMod provides safe Garry's Mod server status cards and optional announcement channels for server
state changes and official Garry's Mod Steam news. Version 1.0.0 intentionally has no RCON, console,
ban, kick, file, or Workshop-management surface.

## Add a public server

```text
[p]gmodset add main play.example.com 27015
[p]gmodset default main
[p]gmod
[p]gmod servers
[p]gmod connect
```

The configured hostname remains visible; resolved IP addresses are never displayed or saved. DNS and
UDP work have short timeouts and response-size bounds. Public addresses are allowed by default.
Loopback, link-local, multicast, reserved, and unspecified addresses are always rejected.

Private/LAN servers require the bot owner to approve the exact configured hostname first:

```text
[p]gmodowner privatehost add gmod.internal.example
[p]gmodset add lan gmod.internal.example 27015
```

This prevents ordinary guild administrators from using the bot as a general internal-network scanner.

## Server state announcements

```text
[p]gmodset statuschannel #gmod-status
[p]gmodset statusrole @GMod
[p]gmodset statusinterval 5
[p]gmodset monitoring true
```

The first check seeds online/offline state silently. Later transitions post once to the configured
channel. Last-good server information is retained and clearly marked stale during an outage.

## Official Steam update announcements

```text
[p]gmodset updateschannel #gmod-updates
[p]gmodset updatesrole @GMod
[p]gmodset updatesinterval 10
[p]gmodset updates start
```

Choosing the channel seeds the current official Steam items so setup does not flood old posts.
Future Garry's Mod Steam announcements are posted once, oldest to newest, across reloads and restarts.

Use `[p]gmod updates [count]` to browse recent official posts and
`[p]gmodset updatescheck` to check immediately.

## Boundaries

- A2S_INFO supplies server name, map, game, player/bot counts, capacity, version, VAC/password state,
  and latency. Player names are not queried in this release.
- Split A2S_INFO packets are rejected instead of allocating or decompressing unbounded data.
- Status and Steam polling intervals are limited to 5–1,440 minutes.
- Only Manage Server or Red administrators can change guild settings.
- Role mentions are bounded to the configured role; external text cannot mention users or everyone.
- RCON and state-changing server administration remain future, separately reviewed work.
