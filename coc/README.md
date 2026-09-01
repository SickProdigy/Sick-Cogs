# Clash of Clans Cog

The `coc` cog provides Clash of Clans clan lookup, current war status, and configurable war notifications for Red-DiscordBot.

## Requirements

- Red-DiscordBot `3.5.0` or newer.
- A Clash of Clans API key from https://developer.clashofclans.com/.
- The configured clan's war log must be public for current war data. Clan War League data is used as a fallback when available.

## Setup

Bot owners set the shared Clash of Clans API key:

```text
[p]coc setapi <api_key>
```

Server moderators or users with `Manage Channels` set the clan tag:

```text
[p]coc setclan <clan_tag>
```

Set the war update channel. If no channel is passed, the current channel is used. This also turns war notifications on:

```text
[p]coc setwarchannel
[p]coc setwarchannel #war-updates
```

## Commands

Show configured clan information:

```text
[p]coc
```

Show current war details:

```text
[p]coc war
```

Show who has and has not attacked in the current war:

```text
[p]coc attacks
[p]coc attack
```

Toggle all war notifications for the server:

```text
[p]coc notifications
```

Show the current notification setup:

```text
[p]coc notifications status
```

## War Notifications

War notifications check about every 5 minutes and post to the configured war channel. Each event is tracked per war so it only fires once for that war.

Available event names:

```text
prep
prepsoon
battle
attacklog
endsoon
ended
```

Default behavior:

- `prep`: on, sends when preparation day starts.
- `prepsoon`: on, sends before battle day starts. Default is 5 minutes before start.
- `battle`: on, sends when battle day starts.
- `attacklog`: on, sends new individual attack updates during war.
- `endsoon`: on, sends before the war ends. Default is 60 minutes before end.
- `ended`: on, sends a roundup when the war ends.

Toggle one event:

```text
[p]coc notifications event prep on
[p]coc notifications event attacklog off
```

Change warning times:

```text
[p]coc notifications prepsoonminutes 5
[p]coc notifications endsoonminutes 60
```

## Role Mentions

Set one role for war notification mentions:

```text
[p]coc notifications role @War
```

Clear the mention role:

```text
[p]coc notifications clearrole
```

Mention toggles are on by default for every event, but no ping is sent unless a mention role is configured.

Toggle mentions for one event:

```text
[p]coc notifications mention prepsoon on
[p]coc notifications mention attacklog off
```

## Attack Log Updates

When `attacklog` is enabled, war notifications send a focused `War Log Update` for new attacks. Each attack is labeled as a friendly or enemy attack, then leads with the player who attacked, the target, stars, destruction percentage, and the current score summary.

Attack-log notification embeds do not show the generic status or team size text. Once battle day is active, they only show the war end time instead of repeating preparation and start times.

## Attack Status

Use `[p]coc attacks` or `[p]coc attack` to check the current war attack status for the configured clan.

The command shows:

- Current war state.
- Total attacks used and remaining.
- Stars as current/max possible.
- Member-by-member attacks used and stars.
- Red markers for members who still have attacks.
- Green markers for members who have used all attacks.
- War end time during battle day and after the war ends.

## War Roundup

When `ended` is enabled, the war-ended notification sends a roundup embed instead of the normal war status embed.

The roundup includes:

- Final result.
- Final stars as current/max possible, destruction, and attack totals for both clans.
- Member-by-member stars and attacks used for the configured clan.
- Top attackers from the configured clan.
- Unused attacks from the configured clan.
- Zero-star attack counts when any happened.
- War ended time.

## Notes

- Notification setup commands are limited to moderators or users with `Manage Channels`.
- `setapi` is bot-owner only because the API key is shared globally.
- Current war lookup supports regular wars and attempts a CWL fallback when regular war data is blocked or unavailable.
