# Cleverbot

> **Retired:** Cleverbot no longer offers a supported API for programmatic access. This cog is
> hidden from repository listings and unavailable for new installations.

Cleverbot lets the bot reply with simulated conversation responses from Cleverbot-compatible external APIs.

It can respond to direct commands, bot mentions, direct messages if enabled by the bot owner, or every message in a configured channel. Server moderators can tune response style and control where automatic replies are allowed.

## Commands

- `[p]cleverbot <text>` - Send a message to Cleverbot and post the response.
- `[p]cleverbotset toggle` - Toggle automatic replies when the bot is mentioned.
- `[p]cleverbotset channel [#channel]` - Toggle automatic replies in a channel. Defaults to the current channel.
- `[p]cleverbotset mention` - Toggle whether responses mention the user.
- `[p]cleverbotset reply` - Toggle Discord reply-style responses.
- `[p]cleverbotset tweakinfo` - Show the current guild response tweaks.
- `[p]cleverbotset guildtweaks <tweak1> <tweak2> <tweak3>` - Set guild response tweaks from `0` to `100`; use `-1` to inherit global settings.

## Bot Owner Setup

- `[p]cleverbotset apikey <key>` - Set the Cleverbot.com API key.
- `[p]cleverbotset ioapikey <user> <key>` - Set Cleverbot.io credentials.
- `[p]cleverbotset tweaks <tweak1> <tweak2> <tweak3>` - Set global response tweaks.
- `[p]cleverbotset dm` - Toggle replies in direct messages.

Use these commands in direct messages when possible so credentials do not remain visible in a server channel.

## Allowlist And Blocklist

- `[p]cleverbotset allowlist add <channel/category/user/role...>` - Allow automatic replies for specific targets.
- `[p]cleverbotset allowlist remove <channel/category/user/role...>` - Remove targets from the allowlist.
- `[p]cleverbotset allowlist info` - Show the current allowlist.
- `[p]cleverbotset blocklist add <channel/category/user/role...>` - Block automatic replies for specific targets.
- `[p]cleverbotset blocklist remove <channel/category/user/role...>` - Remove targets from the blocklist.
- `[p]cleverbotset blocklist info` - Show the current blocklist.

This cog stores guild settings, response tweaks, and configured channel, category, role, or user IDs. Message content is sent to the configured external Cleverbot API for response generation.
