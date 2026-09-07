# Giveaways

Giveaways is a Red-DiscordBot cog for running button-based giveaways with scheduled endings and optional entry requirements.

Moderators can start simple giveaways or advanced giveaways with custom embeds, winner counts, role restrictions, weighted entries, credit costs, account-age checks, and server-join-age checks.

## Commands

- `[p]giveaway start [#channel] <time> <prize>` - Start a basic giveaway. Alias: `[p]gw start`.
- `[p]giveaway advanced <flags>` - Start an advanced giveaway. Alias: `[p]gw adv`.
- `[p]giveaway end <message_id>` - End a running giveaway and draw winners.
- `[p]giveaway reroll <message_id>` - Draw new winners for a completed giveaway.
- `[p]giveaway cancel <message_id>` - Cancel an active giveaway without drawing a winner.
- `[p]giveaway entrants <message_id>` - List entrants for a running giveaway.
- `[p]giveaway info <message_id>` - Show giveaway settings and entrant count.
- `[p]giveaway list` - List running giveaways in the server.
- `[p]giveaway edit <message_id> <flags>` - Edit an active giveaway.
- `[p]giveaway explain` - Show all supported advanced giveaway flags.

## Advanced Flags

Advanced giveaways require a prize and either a duration or an explicit date/time:

```text
gw advanced --prize Nitro --duration 2h --winners 2 --roles Member --notify --congratulate
```

Useful options include:

- `--channel`, `--emoji`, `--description`, `--image`, `--thumbnail`, `--button-text`, and `--button-style` for presentation.
- `--roles`, `--blacklist`, `--bypass-roles`, `--joined`, and `--created` for entry requirements.
- `--winners`, `--multiplier`, `--multi-roles`, and `--multientry` for winner and entry behavior.
- `--cost` for Red bank credit entry costs.
- `--show-requirements`, `--announce`, `--notify`, and `--congratulate` for user-facing behavior.

Legacy third-party level-service integrations are deferred from this focused core release.

This cog stores giveaway settings, message IDs, entrant user IDs, and winner records for active and completed giveaways.
