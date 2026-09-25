# GameRoles

GameRoles lets trusted game or clan leaders add and remove only the Discord roles approved by server administrators.

## Interactive setup

Administrators or members with **Manage Roles** use:

```text
[p]gameroleset
```

The dashboard creates profiles such as `rust` or `ark`, selects the manager roles for each profile, and selects the member roles those managers may assign. The bot must have **Manage Roles**, and its highest role must be above every assignable role.

Delegated managers use:

```text
[p]gameroles
```

They only see profiles authorized by their own Discord roles. The dashboard lets them choose a profile, member, and approved role, then add or remove it.

## Command fallbacks

The shorter assignment form automatically finds an authorized profile containing the requested role:

```text
[p]gameroles add @member @Rust-Officer
[p]gameroles remove @member @Rust-Officer
```

The root command also accepts `gamerole`, `clanroles`, and `clanrole`. The remove command accepts `remove`, `rem`, and `unassign`.

The original explicit-profile form remains supported:

```text
[p]gameroles add rust @member @Rust-Officer
[p]gameroles remove rust @member @Rust-Officer
```

Administrator fallbacks live under `gameroleset`:

```text
[p]gameroleset manager add rust @Rust-General
[p]gameroleset manager remove rust @Rust-General
[p]gameroleset allow add rust @Rust-Officer
[p]gameroleset allow remove rust @Rust-Officer
[p]gameroleset show rust
```

Integration-managed roles and `@everyone` cannot be approved. Discord's audit log records the manager and profile for each successful change.
