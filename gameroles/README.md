# GameRoles

GameRoles gives trusted game or clan leaders a narrowly scoped way to add and remove approved Discord roles. Server administrators decide both who may manage each game and exactly which roles they may change.

Each profile is independent, so `rust` can use `@Rust-General` while `ark` uses `@Ark-General`.

## Setup

A server administrator or member with **Manage Roles** configures each profile:

```text
[p]gameroles manager add rust @Rust-General
[p]gameroles allow add rust @Rust-Enlisted
[p]gameroles allow add rust @Rust-Officer
[p]gameroles allow add rust @Rust-Major

[p]gameroles manager add ark @Ark-General
[p]gameroles allow add ark @Ark-Tribe
```

The bot must have **Manage Roles**, and its highest role must be above every role it assigns. Integration-managed roles and `@everyone` cannot be approved.

## Use

Configured game leaders can run:

```text
[p]gameroles add rust @member @Rust-Enlisted
[p]gameroles remove rust @member @Rust-Enlisted
```

Useful inspection and removal commands:

```text
[p]gameroles
[p]gameroles show rust
[p]gameroles manager remove rust @Rust-General
[p]gameroles allow remove rust @Rust-Enlisted
```

Game leaders cannot assign arbitrary roles: the requested role must be on that profile's administrator-controlled approved list. Discord's audit log records who requested each successful change.
