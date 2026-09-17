# RoleTools

RoleTools is a Red-DiscordBot cog for managing self roles, sticky roles, automatic roles, reaction roles, button roles, select menu roles, temporary roles, and role relationship rules.

It is intended for servers that need more flexible role management than Discord's built-in onboarding tools provide.

## User Commands

Run `[p]roletools` in a server for a short member guide. These commands use RoleTools’ own server configuration; Red’s separate core `[p]selfrole` command is not a substitute.

- `[p]roletools viewroles` - View the self-roles currently available to you.
- `[p]roletools selfrole <role>` - Add or remove an available self-role by mention, ID, or name.
- `[p]help roletools selfrole` - View detailed command syntax and behavior.

## Moderator Commands

Run `[p]roletools adminhelp` for the short setup path.

- `[p]roletools selfassignable <true_or_false> <role>` - Allow or block users from adding a role to themselves.
- `[p]roletools selfremovable <true_or_false> <role>` - Allow or block users from removing a role from themselves.
- `[p]roletools sticky <true_or_false> <role>` - Reapply a role when a user rejoins the server.
- `[p]roletools autorole <true_or_false> <role>` - Apply a role automatically when a user joins.
- `[p]roletools cost <amount> <role>` - Require Red bank credits before a user can acquire a role.
- `[p]roletools giverole <role> <target...>` - Add a role to members, roles, channels, threads, or groups such as `everyone`, `here`, `bots`, and `humans`.
- `[p]roletools removerole <role> <target...>` - Remove a role from members, roles, channels, or groups.
- `[p]roletools forcerole <users...> <role>` - Force a sticky role for one or more users.
- `[p]roletools forceroleremove <users...> <role>` - Remove a forced sticky role entry.
- `[p]roletools viewroles` - Show self-roles available to the current member.
- `[p]roletools viewroles configured` - Show managers all stored RoleTools settings, including deleted role IDs.
- `[p]roletools viewroles <role>` - Inspect one role; managers receive the complete role report.
- `[p]roletools notify channel #channel` - Opt in to a channel notice for successful self-role, reaction, button, and select changes.
- `[p]roletools notify disable` - Stop those notices.

## Paid And Temporary Roles

RoleTools uses Red Bank for roles configured with `[p]roletools cost`. Paid role grants are serialized per member, validated before withdrawal, and refunded if Discord assignment or temporary-role persistence fails. Temporary-role scheduling supports both resetting an expiration and extending the remaining paid time; the extension helper is the foundation for forthcoming monthly, yearly, and custom-duration Role Shop offers.

Automatic recurring withdrawals are not enabled. Future subscription-style offers will use explicit manual renewal unless a separately reviewed opt-in design is added.

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

## Interactive Self-Role Setup

Run `[p]roletools setup` to open a manager-only setup card inspired by the Clanker launch workflow. It manages two catalogs:

- **Basic self-roles** use Red Admin's canonical `selfroles` list, so they work through `[p]selfrole` and on RoleTools cards.
- **Advanced self-roles** remain RoleTools-only so costs, requirements, conflicts, and temporary durations cannot be bypassed through Red's simpler command.

The setup card uses Discord role and channel selectors to add/remove roles, edit appearance, publish the selected menu layout, and synchronize it. Managers do not need to create internal option names. When publishing, choose one layout:

- **Private menu** puts one button on the public card and opens private 25-role pages. This is the cleanest choice for very large catalogs.
- **Public dropdowns** puts as many as five 25-role dropdowns directly on the public message, supporting up to 125 roles.
- **Single Reaction Card** assigns a distinct emoji to every role. RoleTools keeps 20 roles on each message, creates overflow messages automatically, and rebuilds its mappings when the shared catalogs change.
- **Managed Reaction Channel** publishes one bot message per role with 👍. Synchronization retains and edits existing message slots without clearing reactions, creates only the additional bottom messages it needs, removes surplus bottom messages, and remaps each slot to the alphabetized shared catalog.

Use **Sync published menu** after changing the catalogs. **Remove published menu** deletes every tracked public menu message and its reaction bindings while preserving the shared catalogs and Appearance settings. Existing Red `selfroleset` changes and changes made inside the setup card also trigger synchronization. If Managed Reaction Channel slots are remapped, existing member roles are not transferred; the optional RoleTools notification channel receives a notice explaining that members can remove and add 👍 again for a newly displayed role.

Every layout reads the same Basic and Advanced catalogs, so roles only need to be managed once.

The legacy `[p]roletools select`, button, reaction, and message commands remain available for advanced layouts and compatibility.

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


## Replacing external RoleTools

Sick-Cogs RoleTools uses a new Config namespace. Its first load imports the external RoleTools namespace plus legacy StickyRoles and Autorole data as read-only sources before persistent component views and temporary-role scheduling start. Do not clear legacy Red data during replacement.

After installing and loading Sick-Cogs RoleTools, a bot owner can run:

```text
[p]roletools migrationstatus
```

Review any warnings before testing existing reaction roles, button/select messages, sticky/automatic roles, temporary roles, and member self roles with `[p]roletools selfrole <role>`.
