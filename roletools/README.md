# RoleTools

RoleTools is a companion to Red-DiscordBot’s built-in self-role system. It keeps ordinary self-roles native and familiar, then adds the menus, automation, and policy controls needed when a server outgrows a simple role list.

Members can continue using Red’s `[p]selfrole` command. Managers can build on that foundation with buttons, dropdowns, reactions, paid or temporary roles, prerequisites, conflicts, notifications, and private access groups, all without maintaining several disconnected role cogs.

## Built On Red, Not Around It

RoleTools separates roles by what they need:

- **Ordinary self-roles** stay in Red Admin’s canonical self-role list. They work with native `[p]selfrole`, appear in RoleTools menus, and remain compatible with Red’s existing permission and Downloader behavior.
- **Advanced roles** are for special actions or assignment rules, such as Red Bank costs, temporary access, prerequisite roles, bundled roles, or mutually exclusive choices. RoleTools keeps these outside the native list so members cannot bypass their rules.
- **Private access groups** add an optional membership layer. A member must hold the group’s access role before choosing the roles inside that group. Ordinary roles are never placed into a group automatically.

This lets a server start with Red’s straightforward self-roles and introduce advanced behavior only where it provides a real benefit. Existing reaction-role messages and stored role mappings remain part of the same system rather than being replaced.

## What RoleTools Adds

- **Guided setup:** `[p]roletools setup` uses Discord buttons, role selectors, channel selectors, and forms instead of requiring managers to memorize a large command tree.
- **One shared role library:** Ordinary and Advanced roles can appear in multiple focused menus without duplicating their configuration.
- **Managed role menus:** Publish an automatic All Roles menu or smaller menus for games, ranks, platforms, notifications, and VIP access. Each menu has its own text, layout, destination, and lifecycle.
- **Scalable layouts:** Choose a private paged Button Role Menu, public dropdowns, a Single Reaction Card, or a Managed Reaction Channel. Large role collections are split around Discord’s limits automatically.
- **Safe advanced actions:** Bank withdrawal, role assignment, and temporary-role persistence are handled together. Failed assignments are refunded instead of leaving members charged without a role.
- **Rules across every RoleTools path:** Costs, durations, prerequisites, included roles, conflicts, and private-group access are checked consistently for commands, buttons, dropdowns, and reactions.
- **Less message maintenance:** Synchronization reuses existing messages where possible, creates overflow pages, removes surplus managed messages, and preserves reaction bindings.
- **Clear member and manager views:** Members see what they can choose; managers can inspect complete configuration, missing roles, and publication health.
- **Optional notifications:** Servers can log successful self-role, reaction, button, and dropdown changes without making notices mandatory.

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

Run `[p]roletools setup` to open the manager-only GUI. The first card separates the four main setup areas:

- **Self-roles** manages ordinary roles. These live in Red Admin's canonical self-role list and work through native `[p]selfrole` as well as RoleTools menus.
- **Advanced roles (Bank/rules)** manages RoleTools-only roles. Use these for Red Bank costs, temporary access, required roles, inclusive roles, and conflicts so the rules cannot be bypassed through native `selfrole`.
- **Manage role menus** opens the publishing dashboard.
- **Private access groups** protects a collection of roles behind a membership role, with optional prerequisites, Bank cost, and temporary access.

### Managed Role Menus

The menu dashboard lists every saved menu with its layout and publication state. **All roles (default)** follows both role lists automatically. Managers can also create named menus containing smaller subsets, such as Games, Ranks, Platforms, Notifications, or VIP Roles.

Selecting a menu shows its saved roles, current layout, and destination. From that editor a manager can:

- edit the public title and instructions;
- choose the publication layout;
- publish the menu, move it to another channel, or synchronize it;
- unpublish its Discord messages without losing the saved menu;
- preview and diagnose without changing the live publication;
- duplicate an unpublished draft, archive/restore it, or permanently delete it with confirmation;
- configure reaction behavior;
- add or remove a named menu's role subset, or fill it from the complete library.

This keeps creation, inspection, publication, movement, synchronization, and removal in one GUI instead of spreading those operations across unrelated commands. The manager shows a compact summary of every saved menu. Pagination controls appear only when more than 25 saved menus require them.

### Publication Layouts

- **Button Role Menu** publishes one button that opens private 25-role pages for each member. Optional role groups become named private categories/pages; otherwise automatic alphabetical pages remain the default.
- **Public dropdowns** place as many as five 25-role selectors on the public message. Each role can have a custom label, description, emoji, and intentional group; blank groups retain automatic 25-role pagination.
- **Single Reaction Card** places up to 20 distinct reactions on each managed message and creates overflow messages automatically. Numbered emojis require no setup; managers may override individual roles with Unicode or accessible server-custom emojis.
- **Managed Reaction Channel** publishes one bot message per role. It uses 👍 by default or one manager-selected shared emoji. Synchronization edits and reuses existing slots, creates required bottom messages, removes surplus bottom messages, and updates bindings without clearing retained reactions that still use the selected emoji.

RoleTools validates duplicate and inaccessible custom emojis before saving them. If a Managed Reaction Channel reorder maps an existing slot to another role, member roles are not silently transferred; the optional notification channel explains that members should remove and add the displayed reaction again if they want the newly displayed role.

The command equivalents live under `[p]roletools menu`:

- `[p]roletools menu` - List saved role menus.
- `[p]roletools menu create <name> [title]` - Create an unpublished named menu.
- `[p]roletools menu view <name>` - Show its roles, layout, and destination.
- `[p]roletools menu add <name> <roles...>` / `remove` - Change a named menu's subset.
- `[p]roletools menu layout <name> <layout>` - Select `private`, `dropdown`, `reactions`, or `role_channel`.
- `[p]roletools menu presentation <name> <role> [label | description | emoji | group]` - Customize or reset one role's menu presentation.
- `[p]roletools menu publish <name> <channel>` - Publish or move the menu.
- `[p]roletools menu sync <name>` - Synchronize its managed messages.
- `[p]roletools menu unpublish <name>` - Remove managed messages but retain the saved configuration.
- `[p]roletools menu preview <name>` / `diagnose` - Inspect a draft or publication without changing it.
- `[p]roletools menu duplicate <name> <new name>` - Copy configuration into an unpublished draft.
- `[p]roletools menu archive <name>` / `restore` - Hide or restore an unpublished saved menu.
- `[p]roletools menu delete <name> confirm` - Permanently delete an unpublished saved menu.

Legacy button, select, reaction, and message commands remain available for specialized layouts and compatibility.

## Private Access Groups

Private access groups collect roles that only members with a chosen access role may select. They can support VIP areas, courses, teams, supporter areas, ranks, or private game sections.

Open `[p]roletools setup` and choose **Private access groups** to create a group, select the roles members can choose, and assign the access role members must hold first. Command equivalents provide the complete rule set:

```text
[p]roletools group
[p]roletools group create VIP Gold
[p]roletools group view vip-gold
[p]roletools group roles add vip-gold @VIP-Lounge @VIP-Games
[p]roletools group accessrole vip-gold @VIP-Gold
[p]roletools group required add vip-gold @VIP
[p]roletools group required mode vip-gold all
[p]roletools group conflicts add vip-gold @Suspended
[p]roletools group cost vip-gold 500
[p]roletools group duration vip-gold 43200
[p]roletools group publish vip-gold #roles dropdown
```

Members use `[p]roletools group join vip-gold` to acquire the access role after prerequisites, blocked roles, hierarchy, and Red Bank balance are checked. Duration values are minutes; `0` means permanent. Joining never creates an automatic renewal. `[p]roletools group leave vip-gold` removes the access role and roles belonging to that group without refunding the one-time access cost.

Each group owns an ordinary named role menu, so it can publish as a Button Role Menu, Public Dropdowns, Single Reaction Card, or Managed Reaction Channel. Group roles are moved into the Advanced catalog and every RoleTools assignment path rechecks the group's prerequisites, blocked roles, archive state, and access role. This prevents native `selfrole`, component, or reaction paths from bypassing the group's access rules.

Groups can be described, archived/restored, and safely deleted with:

```text
[p]roletools group description vip-gold Premium server areas and game groups.
[p]roletools group archive vip-gold
[p]roletools group restore vip-gold
[p]roletools group delete vip-gold confirm
```

Changing or clearing an access role restores the cost and duration settings that role had before the group took control. Deleting a group does not delete Discord roles.

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

## Upgrading Or Migrating

Updates from an existing Sick-Cogs RoleTools installation preserve its configuration and managed-message mappings. Existing reaction roles, buttons, dropdowns, sticky roles, automatic roles, and temporary-role records are preserved.

When replacing the older external RoleTools cog, Sick-Cogs RoleTools can import its RoleTools configuration along with legacy StickyRoles and Autorole data. The import reads the legacy namespaces as sources and does not automatically delete them.

After an upgrade or import, a bot owner can run `[p]roletools migrationstatus` to review the migration state and any warnings.
