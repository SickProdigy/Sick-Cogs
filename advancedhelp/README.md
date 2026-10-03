# AdvancedHelp

AdvancedHelp replaces only Red's bare prefix help landing page with an audience-first,
interactive menu. Direct help such as [p]help command, [p]help group subcommand, and
[p]help CogName continues through Red's standard formatter.

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

Only one custom Red help formatter can be active. Loading fails cleanly if another
formatter owns the slot. Unloading AdvancedHelp restores Red's default formatter.
