# AdvancedHelp

AdvancedHelp is disabled by default and leaves Red's standard help formatter active when
installed or loaded. A bot owner can enable its audience-first interactive bare-help menu
with `[p]advancedhelpset enable` and restore Red help with `[p]advancedhelpset disable`.
The choice persists across reloads and restarts. When enabled, direct help such as
`[p]help command`, `[p]help group subcommand`, and `[p]help CogName` continues through
Red's standard formatter.

The menu is built from Red's own filtered help mapping in the current context. It never
grants access. Hidden, disabled, denied, parent-checked, and Red Permissions-controlled
commands remain subject to Red's checks before they can appear.

Default audiences are Member, Staff, Server Owner, and Bot Owner. Bot-owner overrides can
adjust a command's presentation audience or category. Optional role-backed groups copy
already-visible categories into specialized guidance areas; they cannot reveal or grant
commands.

Run [p]advancedhelpset for status. Configure channel or DM delivery, timeout, audience
overrides, category overrides, audience and category appearance/order, and role-backed
groups (including their appearance/order) with its subcommands. Prefix help
cannot be ephemeral. If embeds/components are unavailable, AdvancedHelp sends a filtered
text summary. DM failures are reported in the invoking channel.

## Command inventory export

Bot owners can run:

```text
[p]docsreport
```

AdvancedHelp responds with `sickgamingbot-command-inventory.json`, a deterministic,
sanitized inventory of every registered command and subcommand. It includes command
paths, aliases, module/category, short descriptions, signatures, hidden/enabled state,
inherited declared access, and AdvancedHelp audience classification. It also records
the Red and AdvancedHelp versions plus loaded cog and extension names.

The report contains command metadata only. It does not export guild configuration,
Discord IDs, messages, credentials, tokens, endpoints, filesystem paths, or callback
source. Live Red permissions, disabled state, parent checks, role hierarchy, and server
rules remain authoritative.

Only one custom Red help formatter can be active. Enabling or reloading an enabled
AdvancedHelp fails cleanly if another formatter owns the slot. Disabling or unloading
AdvancedHelp restores Red's default formatter when AdvancedHelp owns the slot.
