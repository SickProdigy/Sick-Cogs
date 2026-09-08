# Clash of Clans Cog

The `coc` cog provides Clash of Clans clan lookup, current war status, and configurable war notifications for Red-DiscordBot.

## Requirements

- Red-DiscordBot `3.5.0` or newer.
- A Clash of Clans API key from https://developer.clashofclans.com/.
- The configured clan's war log must be public for current war data. Clan War League data is used as a fallback when available.

## Setup

Bot owners set the shared Clash of Clans API key:

```text
[p]set api clashofclans api_key,YOUR_KEY
```

Server moderators or users with `Manage Channels` set the clan tag:

```text
[p]cocset clan <clan_tag>
```

Set or change the war update channel explicitly. If no channel is passed, the current channel is used. This also turns war notifications on:

```text
[p]cocset warchannel
[p]cocset warchannel #war-updates
```

Use `[p]help cocset` for all server configuration. Use `[p]help cocset notifications` for the global notification toggle and status. If required setup is missing, `[p]coc` and notification enablement return one yellow warning card listing every missing item and the exact command needed to fix it.

## Commands

Show configured clan information:

```text
[p]coc
```

Look up another clan without changing the server's configured clan:

```text
[p]coc clan #CLANTAG
```

Look up a player profile:

```text
[p]coc player #PLAYERTAG
```

Player cards include available Builder Hall, Builder Base trophy, and Builder Base league totals. The API does not expose individual Builder Battle history, so battle-by-battle notifications are not available.

Show the configured clan's five most recent regular wars, or inspect another clan:

```text
[p]coc warlog
[p]coc warlog #CLANTAG
```

The public API's war log contains regular clan wars only. It does not provide an archive of past CWL seasons.

Show every available round for the currently active CWL group:

```text
[p]coc cwl
[p]coc cwl #CLANTAG
```

This live view includes completed and active rounds returned by the current league group. It does not save snapshots after that group expires.

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
[p]cocset notifications
```

Show the current notification setup:

```text
[p]cocset notifications status
```

Set the timezone used by war schedules for this Discord server:

```text
[p]cocset timezone America/New_York
[p]cocset timezone Europe/London
[p]cocset timezone UTC
```

Use an IANA timezone name so daylight-saving changes are handled automatically. Run `[p]cocset timezone` without a value to see the current setting.

Show or select the war attack-log notification format:

```text
[p]cocset warattacks
[p]cocset warattacks card
[p]cocset warattacks compact
```

The bare command shows the current format and explains both choices. Pass `card` or `compact` to select one explicitly. Use `[p]cocset notifications status` to review the complete notification setup.

## War Notifications

War notifications check about every 5 minutes and post to the configured war channel. Each event is tracked per war so it only fires once for that war.
Clan War League notifications use the same event settings but can be muted independently. Regular war notifications are unaffected:

```text
[p]cocset cwl
[p]cocset clanwarleague
```

Run either command again to toggle CWL notifications back on. CWL notifications are enabled by default.

Clan Capital Raid Weekend start and end notifications are independently disabled by default. Enable or disable them with:

```text
[p]cocset raidweekend
[p]cocset capitalraid
[p]cocset raids
```

The start notification announces the live weekend. The ending summary includes the API's available attacks, Capital loot, completed raids, destroyed districts, and offensive/defensive medal values. These updates use the configured war channel but do not require regular clan-war notifications to be enabled.

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
[p]cocset event prep on
[p]cocset event attacklog off
```

Change server warning times:

```text
[p]cocset prepwarning 5
[p]cocset endwarning 60
```

## Management and Role Mentions

Server administrators can optionally delegate CoC setup and notification management to one Discord role:

```text
[p]cocset managerrole @CoC Manager
[p]cocset managerrole clear
```

The configured role can use `[p]cocset` and its notification controls without receiving broader Discord permissions. Only server administrators can change the manager role.

## Role Mentions

Set one role for war notification mentions:

```text
[p]cocset notificationrole @War
```

Clear the mention role:

```text
[p]cocset notificationrole clear
```

Mention toggles are on by default for every event, but no ping is sent unless a mention role is configured.

Toggle mentions for one event:

```text
[p]cocset mention prepsoon on
[p]cocset mention attacklog off
```

## Attack Log Updates

When `attacklog` is enabled, war notifications send a focused `War Log Update` for new attacks. The default `compact` format sends an ordinary text-only Discord message with up to ten one-line attack entries. The optional `card` format uses an embed and shows each attack as a green friendly or red enemy entry with attacker, defender, stars, and destruction. Compact updates contain no embed or banner image, allowing several recent logs to remain visible at once.

Card attack-log embeds stay focused on the war type, matchup, and newly detected attacks; they omit generic status, team totals, schedules, and score summaries.

## Attack Status


## Gold Pass and CWL history limits

The Clash of Clans API Gold Pass resource provides the current season's start and end times; it is not an update, event, or reward feed, so this cog does not generate update notifications from it.

The API exposes the active CWL league group and its war tags while that group is available, but it does not provide historical CWL season archives. `[p]coc warlog` therefore reports regular clan-war history only.
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
- The bot owner configures the only active API key with `[p]set api clashofclans api_key,YOUR_KEY` in a DM or private channel.
- Current war lookup supports regular wars and silently uses the matching CWL war when regular war data is blocked or unavailable. War, attack-log, attack-status, event, and roundup cards state `Clan War` or `Clan War League (CWL)` directly so the shared workflow remains clear. During preparation, the main war card is yellow. During battle day, it is orange and shows only the war end time; enemy attack updates remain red, and completed-war roundups use a neutral blue with a green victory, red defeat, or neutral tie marker.
