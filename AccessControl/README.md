# AccessControl

AccessControl is one central policy cog for optional bot-wide guild allowlisting and
command, cog, or background-feature entitlements. Installation and migration leave
enforcement disabled. Bot owners and AccessControl commands always retain recovery access.

## Safe setup order

1. Load the cog and leave enforcement disabled.
2. Configure guild mode with [p]accesscontrol guildmode open or allowlist.
3. In allowlist mode, use [p]accesscontrol guild allow GUILD_ID.
4. Map exact commands, parent command groups, or cogs with
   [p]accesscontrol map set command "rocketleague alerts" rocketleague.notifications.
5. Add local access with grant role/user or issue guild access with
   [p]accesscontrol entitlement grant GUILD_ID CAPABILITY DAYS SPONSOR_ID.
6. Inspect decisions with [p]accesscontrol check CAPABILITY MEMBER.
7. Enable enforcement only after representative member and owner checks pass.

Use a capability of * for an all-feature guild entitlement. Zero days means permanent.
The VIP set/clear commands configure the authoritative SickGaming guild and role. When
configured, a sponsored entitlement fails closed if its sponsor cannot be found or no
longer holds that role.

The entitlement, grant, map, guild, audit, and status commands list current policy. Their
remove/revoke counterparts reverse configuration without deleting Discord objects.

## Integration boundary

Prefix commands can be protected centrally. A specific command mapping takes precedence
over its parent command mapping, which takes precedence over a whole-cog mapping. Red
Permissions and the target command's own checks still run.

Scheduled publishers and other background features must call:

    access = bot.get_cog("AccessControl")
    decision = await access.has_capability(guild, member, "rocketleague.notifications")

The return value contains allowed, reason, source, and capability fields. An integrating
cog must document whether a missing AccessControl cog fails open or closed.

discord.py global prefix checks do not cover slash/application commands. Those require an
explicit application-command integration and are outside version 0.2.0.

External billing or account-linking systems may issue and revoke entitlements later.
AccessControl does not process payments and stores no billing credentials.
