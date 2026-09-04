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
[p]coc set clan <clan_tag>
```

Set or change the war update channel explicitly. If no channel is passed, the current channel is used. This also turns war notifications on:

```text
[p]coc set warchannel
[p]coc set warchannel #war-updates
```

Use `[p]help coc set` for all server configuration. Use `[p]help coc notifications` for the global notification toggle and status. If required setup is missing, `[p]coc` and notification enablement return one yellow warning card listing every missing item and the exact command needed to fix it.

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

Toggle all war notifications for the server. When enabling them without a configured destination, the current channel is saved automatically:

```text
[p]coc notifications
```

Show the current notification setup:

```text
[p]coc notifications status
```

Set the timezone used by war schedules for this Discord server:

```text
[p]coc set timezone America/New_York
[p]coc set timezone Europe/London
[p]coc set timezone UTC
```

Use an IANA timezone name so daylight-saving changes are handled automatically. Run `[p]coc set timezone` without a value to see the current setting.

Toggle between detailed attack cards and compact one-line entries, or select a mode explicitly:

```text
[p]coc set attack
[p]coc set attack card
[p]coc set attack compact
```

The bare command toggles the current format. Pass `card` or `compact` when you need to select one explicitly. Use `[p]coc notifications status` to view the current format.

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
[p]coc set event prep on
[p]coc set event attacklog off
```

Change server warning times:

```text
[p]coc set prepwarning 5
[p]coc set endwarning 60
```

## Management and Role Mentions

Server administrators can optionally delegate CoC setup and notification management to one Discord role:

```text
[p]coc set managerrole @CoC Manager
[p]coc set managerrole clear
```

The configured role can use the commands under `[p]coc set` and `[p]coc notifications` without receiving broader Discord permissions. Only server administrators can change the manager role.

## Role Mentions

Set one role for war notification mentions:

```text
[p]coc set notificationrole @War
```

Clear the mention role:

```text
[p]coc set notificationrole clear
```

Mention toggles are on by default for every event, but no ping is sent unless a mention role is configured.

Toggle mentions for one event:

```text
[p]coc set mention prepsoon on
[p]coc set mention attacklog off
```

## Attack Log Updates

When `attacklog` is enabled, war notifications send a focused `War Log Update` for new attacks. The default `card` format labels each entry `Friendly Attack` or `Enemy Attack` without redundantly repeating the clan name, then shows the attacker, defender, stars, destruction, and current score summary. The optional `compact` format places up to ten attacks into short one-line entries with blue friendly, orange enemy-player, and red enemy-attack markers.

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
- Current war lookup supports regular wars and silently uses the matching CWL war when regular war data is blocked or unavailable. War, attack-log, attack-status, event, and roundup cards state `Clan War` or `Clan War League (CWL)` directly so the shared workflow remains clear. During preparation, the main war card is yellow. During battle day, it is orange and shows only the war end time; enemy attack updates remain red, and completed-war roundups use a neutral blue with a green victory, red defeat, or neutral tie marker.
