# RoleTools

RoleTools is a Red-DiscordBot cog for managing self roles, sticky roles, automatic roles, reaction roles, button roles, select menu roles, temporary roles, and role relationship rules.

It is intended for servers that need more flexible role management than Discord's built-in onboarding tools provide.

## User Commands

- `[p]roletools selfrole <role>` - Add or remove a role that has been configured as self-assignable or self-removable.

## Moderator Commands

- `[p]roletools selfassignable <true_or_false> <role>` - Allow or block users from adding a role to themselves.
- `[p]roletools selfremovable <true_or_false> <role>` - Allow or block users from removing a role from themselves.
- `[p]roletools sticky <true_or_false> <role>` - Reapply a role when a user rejoins the server.
- `[p]roletools autorole <true_or_false> <role>` - Apply a role automatically when a user joins.
- `[p]roletools cost <amount> <role>` - Require Red bank credits before a user can acquire a role.
- `[p]roletools giverole <role> <target...>` - Add a role to members, roles, channels, threads, or groups such as `everyone`, `here`, `bots`, and `humans`.
- `[p]roletools removerole <role> <target...>` - Remove a role from members, roles, channels, or groups.
- `[p]roletools forcerole <users...> <role>` - Force a sticky role for one or more users.
- `[p]roletools forceroleremove <users...> <role>` - Remove a forced sticky role entry.
- `[p]roletools viewroles [role]` - View RoleTools settings for server roles.

## Role Rules

- `[p]roletools include add <role> <included_role...>` - Grant related roles together.
- `[p]roletools include mutual <roles...>` - Make several roles mutually inclusive.
- `[p]roletools include remove <role> <included_role...>` - Remove inclusive role rules.
- `[p]roletools exclude add <role> <excluded_role...>` - Remove conflicting roles when a role is assigned.
- `[p]roletools exclude mutual <roles...>` - Make several roles mutually exclusive.
- `[p]roletools exclude remove <role> <excluded_role...>` - Remove exclusive role rules.
- `[p]roletools required add <role> <required_role...>` - Require roles before a role can be assigned.
- `[p]roletools required remove <role> <required_role...>` - Remove required role rules.
- `[p]roletools required any <role> <true_or_false>` - Decide whether any required role is enough or all are needed.

## Reaction, Button, And Select Roles

- `[p]roletools reaction create <message> <emoji> <role>` - Add a reaction role to a message.
- `[p]roletools reaction remove <message> <emoji>` - Remove a reaction role from a message.
- `[p]roletools reaction bulk <message> <emoji role...>` - Configure multiple reaction roles at once.
- `[p]roletools reaction reactroles` - View configured reaction roles.
- `[p]roletools buttons create <name> <role> [extras]` - Create a reusable role button.
- `[p]roletools buttons delete <name>` - Delete a role button.
- `[p]roletools buttons view` - View configured role buttons.
- `[p]roletools select create <name> <options...> [extras]` - Create a reusable role select menu.
- `[p]roletools select createoption <name> <role> [extras]` - Add an option to a select menu.
- `[p]roletools select delete <name>` - Delete a select menu.
- `[p]roletools select deleteoption <name>` - Delete a select option.
- `[p]roletools select view` - View configured select menus.
- `[p]roletools select viewoptions` - View configured select options.

## Role Messages And Temporary Roles

- `[p]roletools message send <channel> <buttons...> <menus...> [text]` - Send a managed role message with saved buttons and select menus.
- `[p]roletools message edit <message> <buttons...> <menus...>` - Edit a bot message to use saved buttons and select menus.
- `[p]roletools message sendselect <channel> <menus...> [text]` - Send saved select menus with an optional message.
- `[p]roletools message editselect <message> <menus...>` - Edit a bot message to use saved select menus.
- `[p]roletools message sendbutton <channel> <buttons...> [text]` - Send saved buttons with an optional message.
- `[p]roletools message editbutton <message> <buttons...>` - Edit a bot message to use saved buttons.
- `[p]roletools temporary set <role> [duration]` - Set how long a role lasts after RoleTools applies it.
- `[p]roletools temporary list [member]` - List pending temporary role removals.

This cog stores role configuration, message/component IDs, and user IDs needed for sticky and temporary role behavior.
