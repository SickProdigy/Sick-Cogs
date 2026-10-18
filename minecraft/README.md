# Minecraft

Minecraft monitors multiple named Java and Bedrock servers, announces outage/recovery transitions,
and can publish official Java and Bedrock game updates. It has no RCON, panel, console, allowlist,
file, kick, or ban operations.

## Add servers

```text
[p]minecraftset add survival java play.example.com
[p]minecraftset add bedrock bedrock play.example.com 19132
[p]minecraftset default survival
[p]minecraft
[p]minecraft servers
[p]minecraft connect bedrock
```

When a Java port is omitted, the cog checks `_minecraft._tcp` SRV and otherwise uses 25565.
Bedrock defaults to UDP 19132. Java and Bedrock protocols, caches, and displayed edition labels remain
separate.

## Transition announcements

```text
[p]minecraftset channel #minecraft-status
[p]minecraftset role @Minecraft
[p]minecraftset interval 5
[p]minecraftset monitoring true
```

The first state is seeded silently. Later online/offline transitions post once. Last-good information
remains visible and is marked stale during an outage. Java sample player names are hidden by default;
enable them explicitly with `[p]minecraftset playersample true`.

## Official game updates

```text
[p]minecraftset updates channel #minecraft-updates
[p]minecraftset updates role @Minecraft
[p]minecraftset updates interval 15
[p]minecraftset updates snapshots true
[p]minecraftset updates previews true
[p]minecraftset updates start
```

Stable Java and Bedrock releases are enabled by default. Java snapshots, prereleases, and release
candidates and Bedrock previews/betas are opt-in. Starting seeds the current official history without
posting it; later updates are delivered once, oldest first, and remain deduplicated across restarts.
Enabling an additional filter also seeds its existing posts before announcements begin.

The cog uses Minecraft's official Feedback changelog sections because they provide human-readable
release notes, summaries, timestamps, and stable article links. Mojang's Java version manifest was
evaluated but is not used for announcements because it provides version metadata rather than release
notes. All enabled guilds share one source fetch per due cycle, and failed checks wait for the configured
interval before retrying.

Users can view the newest update with `[p]minecraft updates`, the second newest with
`[p]minecraft updates 2`, or a compact list with `[p]minecraft updates list 5`. Administrators can use
`[p]minecraftset updates` for status, `preview`, `check`, `sources`, `stop`, and the filter commands.

## Network policy

The public configured hostname is displayed while resolved IP addresses remain private. DNS, TCP, UDP,
JSON, string, and packet sizes are bounded. Loopback, link-local, multicast, reserved, unspecified,
and unapproved private/LAN targets are blocked after ordinary and SRV resolution.

The bot owner may approve an exact private hostname:

```text
[p]minecraftowner privatehost add minecraft.internal.example
```

## Deferred

Account linking, Java UUID/Bedrock identity handling, allowlist workflows, RCON, and hosting-panel
integration require separate security and permission design.
