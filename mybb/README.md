# MyBB

Connect each Discord server to its own installation of the **SickProdigy MyBB API v1 plugin**.
The guild connector provides shared forum discovery, while members may optionally add a personal,
scoped token so MyBB applies their individual permissions and authorship. A reviewed publishing
flow can turn a Discord message into a managed MyBB thread.

RSSPublisher remains the better cog for automatic new-thread announcements. MyBB is for deliberate
browsing, recent-thread search, and creating content.

## Requirements

- MyBB with `mybb_api-v1_plugin` 1.0.0 or newer
- A publicly reachable HTTPS board URL
- A dedicated, restricted guild token with `forums:read` and `threads:read`
- `threads:write` on any guild or personal token used to publish

Connector URLs may not include credentials, query strings, or fragments. Redirects and destinations
that resolve to private, loopback, or link-local addresses are rejected. Give every Discord server
and member a separate revocable token rather than reusing an administrator token.

## Connect a Discord server

A member with Manage Server runs:

```text
[p]mybbset setup
```

Use **Connect board** to enter the board URL and restricted guild token. The cog validates both the
public health route and authenticated forum access before saving it. Then choose a default forum and
leave the bridge enabled. Every Discord guild has an independent URL and token namespace.

Text-command fallbacks:

```text
[p]mybbset status
[p]mybbset defaultforum 2
[p]mybbset forumalias set ideas 120
[p]mybbset forumalias set support 45
[p]mybbset forumalias
[p]mybbset managerrole @Forum Staff
[p]mybbset enabled true
[p]mybbset disconnect confirm
```

Changing the guild to a different board removes its existing personal connections so a token from
one MyBB installation is never sent to another. Disconnecting also disables the bridge and removes
all personal connections for that guild.

## Personal connections

A member can run:

```text
[p]mybb connect
```

The private panel accepts a scoped personal API token for the guild's configured board. Requests
prefer that personal token, letting MyBB enforce the member's forum visibility and posting rights.
`[p]mybb disconnect` removes it. Tokens are never displayed in Discord responses.

Personal forum API access is separate from verified membership linking. A personal
API token controls forum browsing and publishing; it is never accepted as proof of
account ownership for Discord roles.

## Membership roles

Install External Sign-In 0.1.13 or newer. Create a dedicated confidential client,
enable **Account linking service**, and list every named entitlement this Discord
server may consume. Do not reuse a website OIDC client or the API v1 token.

Configure its client ID and secret privately, map entitlements, then enable sync:

```text
[p]mybbset membership setup
[p]mybbset membership
[p]mybbset membership map vip @VIP
[p]mybbset membership unmap vip
[p]mybbset membership enabled true
```

Members use `[p]mybb membership` and press **Link MyBB**. The cog starts the
provider's device-style flow, shows the member a private approval URL, polls outbound,
verifies issuer/Discord ID/guild context, stores the returned link credential in Red
shared API tokens, and immediately synchronizes roles. No inbound bot port is needed.
`[p]mybb membership sync` performs a manual check.
The membership panel disables **Link MyBB** until server setup is enabled and
disables sync/unlink controls until that member has completed a verified link.

Names are case-sensitive provider permissions (for example `vip`), not numeric
forum group IDs. Up to 50 mappings are stored per guild. Mapping changes require
Manage Server and Manage Roles (or administrator access); roles must be below both
the bot and configuring member, except the guild owner has no member hierarchy
restriction. Everyone, integration, and administrative/moderation roles are rejected.

The loop wakes once a minute but checks a guild only when its 15-minute due cycle
arrives. Provider failures exponentially back off up to six hours. Only authenticated
HTTP 200 `inactive` or `unlinked` states remove cog-owned roles. Timeouts, malformed
responses, 401/403, rate limits and server errors preserve roles for retry. Role
ownership is recorded before grants; failed Discord changes remain retryable. The cog
never adopts or removes a pre-existing unowned role. Use dedicated membership roles,
because a later manual grant of a role already tracked as cog-owned is indistinguishable.

Intentional unlink revokes the provider association before removing roles and local
credentials. If provider confirmation is ambiguous, the operation stops and preserves
access. Changing boards, removing the membership client, server disconnect and Red
user-data deletion follow the same confirmed-cleanup rule. External Sign-In client
secret rotation revokes its links and therefore requires members to link again.

## Discovery commands

- `[p]mybb` — interactive home card.
- `[p]mybb forums` — forums visible through the member or guild connector.
- `[p]mybb threads [forum_id] [page]` — recent threads; defaults to the configured forum.
- `[p]mybb thread <thread_id>` — open one readable thread.
- `[p]mybb search <terms>` — search up to 300 recent thread summaries in the default forum.

The API does not currently expose full-text search. The bounded search is intentionally labeled as a
recent search and does not scrape MyBB HTML. A future API search route should enforce the active
token's normal MyBB visibility rules and replace this fallback.

## Publishing a Discord message

Server managers can map memorable names to approved destination forums. Reply to a Discord message
and publish using an alias, the explicit `default` destination, or a numeric forum ID:

```text
[p]mybb draft ideas <subject>
[p]mybb draft default <subject>
[p]mybb draft 120 <subject>
```

Manage aliases with `[p]mybbset forumalias set <name> <forum_id>` and
`[p]mybbset forumalias remove <name>`. Publishing requires an explicit destination so a misspelled
alias cannot silently route a thread to the server default.

A personally connected member publishes through their own token. Without a personal connection,
only Manage Server or the configured manager role may publish through the guild connector. The bot
shows **Edit**, **Publish**, and **Cancel** before sending anything.

The request includes the Discord jump URL, a stable `source=discord` / `source_id` pair, and an
idempotency key. Repeating the same source message updates its managed thread rather than creating a
duplicate. Discord attachments are represented by their CDN URLs.

## Stored data

Guild configuration contains the board URL, enabled state, default forum ID, forum aliases, and manager role ID.
Member configuration contains only a connection marker. Guild and member bearer tokens are stored
in separate Red shared-API-token namespaces. Personal tokens are removed when members disconnect,
when a board changes, when the guild connector is removed, or when Red processes that user's data
deletion request.
