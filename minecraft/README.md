# Minecraft

Minecraft monitors multiple named Java and Bedrock servers and can announce outage/recovery
transitions to a configured Discord channel. Version 0.1.0 is status-only: it has no RCON, panel,
console, allowlist, file, kick, or ban operations.

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
