# RoleTools

RoleTools is a Red-DiscordBot cog for managing self roles, sticky roles, automatic roles, reaction roles, button roles, dropdown roles, temporary roles, paid roles, and role relationship rules.

It is intended for servers that need more control and less manual message maintenance than Discord's built-in onboarding tools or separate one-purpose role cogs provide.

## Why Use Sick-Cogs RoleTools?

RoleTools combines the member interface, manager interface, publishing system, and assignment rules in one cog:

- **Built-in setup GUI:** `[p]roletools setup` uses Discord buttons, role selectors, channel selectors, and forms. Routine setup does not require memorizing long commands or creating internal option names.
- **One reusable role library:** Ordinary and Advanced roles can be reused across multiple menus instead of being configured separately for buttons, dropdowns, and reactions.
- **Multiple managed menus:** Publish the default All Roles menu plus focused menus for games, ranks, platforms, notifications, or VIP access. Each menu keeps its own title, instructions, layout, destination, and reaction settings.
- **Four scalable layouts:** Choose a private paged Button Role Menu, public dropdowns, a multi-message Single Reaction Card, or a Managed Reaction Channel.
- **Automation instead of message rebuilding:** RoleTools synchronizes managed messages, creates overflow pages, preserves reusable reaction-channel slots, removes surplus messages, and maintains reaction-to-role bindings.
- **Rules that cannot be bypassed:** Advanced roles stay outside Red's ordinary `selfrole` list, allowing RoleTools to enforce Red Bank costs, temporary access, requirements, inclusive roles, and conflicts.
- **Native Red compatibility:** Ordinary roles use Red Admin's canonical self-role list, so `[p]selfrole <role>` continues to work while RoleTools adds richer menus and automation around it.
- **Transactional paid roles:** Credit withdrawal, Discord assignment, and temporary-role persistence are handled as one operation, with a refund when assignment fails.

## User Commands

Run `[p]roletools` for the member guide.

- `[p]selfroles` - List the self-roles currently available to you. This is the short form of `[p]roletools viewroles`.
- `[p]selfroles <role>` - Filter that availability view to a role mention, ID, or name.
- `[p]selfrole <role>` - Use Red's native command to add or remove an ordinary self-role.
- `[p]roletools selfrole <role>` - Add or remove an Advanced role whose Bank cost or other RoleTools rules must be enforced.
- `[p]help roletools selfrole` - View detailed Advanced-role assignment behavior.

The availability card labels Advanced roles and tells members which assignment command applies.

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

Run `[p]roletools setup` to open the manager-only GUI. Its first card deliberately has three jobs:

- **Self-roles** manages ordinary roles. These live in Red Admin's canonical self-role list and work through native `[p]selfrole` as well as RoleTools menus.
- **Advanced roles (Bank/rules)** manages RoleTools-only roles. Use these for Red Bank costs, temporary access, required roles, inclusive roles, and conflicts so the rules cannot be bypassed through native `selfrole`.
- **Manage role menus** opens the publishing dashboard.

### Managed Role Menus

The menu dashboard lists every saved menu with its layout and publication state. **All roles (default)** follows both role lists automatically. Managers can also create named menus containing smaller subsets, such as Games, Ranks, Platforms, Notifications, or VIP Roles.

Selecting a menu shows its saved roles, current layout, and destination. From that editor a manager can:

- edit the public title and instructions;
- choose the publication layout;
- publish the menu, move it to another channel, or synchronize it;
- unpublish its Discord messages without losing the saved menu;
- configure reaction behavior;
- add or remove a named menu's role subset, or fill it from the complete library.

This keeps creation, inspection, publication, movement, synchronization, and removal in one GUI instead of spreading those operations across unrelated commands.

### Publication Layouts

- **Button Role Menu** publishes one button that opens private 25-role pages for each member. It is the cleanest default for a large library.
- **Public dropdowns** place as many as five 25-role selectors on the public message, supporting up to 125 roles.
- **Single Reaction Card** places up to 20 distinct reactions on each managed message and creates overflow messages automatically. Numbered emojis require no setup; managers may override individual roles with Unicode or accessible server-custom emojis.
- **Managed Reaction Channel** publishes one bot message per role. It uses 👍 by default or one manager-selected shared emoji. Synchronization edits and reuses existing slots, creates required bottom messages, removes surplus bottom messages, and updates bindings without clearing retained reactions that still use the selected emoji.

RoleTools validates duplicate and inaccessible custom emojis before saving them. If a Managed Reaction Channel reorder maps an existing slot to another role, member roles are not silently transferred; the optional notification channel explains that members should remove and add the displayed reaction again if they want the newly displayed role.

The command equivalents live under `[p]roletools menu`:

- `[p]roletools menu` - List saved role menus.
- `[p]roletools menu create <name> [title]` - Create an unpublished named menu.
- `[p]roletools menu view <name>` - Show its roles, layout, and destination.
- `[p]roletools menu add <name> <roles...>` / `remove` - Change a named menu's subset.
- `[p]roletools menu layout <name> <layout>` - Select `private`, `dropdown`, `reactions`, or `role_channel`.
- `[p]roletools menu publish <name> <channel>` - Publish or move the menu.
- `[p]roletools menu sync <name>` - Synchronize its managed messages.
- `[p]roletools menu unpublish <name>` - Remove managed messages but retain the saved configuration.

Legacy button, select, reaction, and message commands remain available for specialized layouts and compatibility.

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


## Upgrading And Replacing RoleTools

Upgrading from Sick-Cogs RoleTools on `main` is a copy-first import. This generation uses a new Config identifier and reads the current `main` namespace (`7194820561938472611`) as a migration source without modifying or clearing it. Guild, role, member, and relevant global settings are normalized into the new namespace before any persistent views or catalog automation starts.

Schema 2 then builds catalogs in the new namespace: simple self-assignable/removable roles are merged into Red Admin's native self-role list, while roles with Bank costs, durations, requirements, inclusions, or conflicts become Advanced. Existing manual Advanced classifications are preserved. Advanced roles are removed from the native list so their rules cannot be bypassed. If Red Admin is unavailable during conversion, roles remain safely Advanced and a review note is recorded.

When the Sick-Cogs `main` namespace is empty, the importer falls back to the older external RoleTools namespace and also copies the legacy StickyRoles and Autorole state. Neither prior RoleTools namespace is reused for normal operation or modified by the importer. Do not clear legacy Red data during replacement.

After installing and loading Sick-Cogs RoleTools, a bot owner can run:

```text
[p]roletools migrationstatus
```

Review any warnings before testing existing reaction roles, button/select messages, sticky/automatic roles, temporary roles, and member self roles with `[p]roletools selfrole <role>`.
