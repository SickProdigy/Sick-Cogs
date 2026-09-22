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

This is an interim connector. The intended later experience is a browser authorization link with a
short-lived, one-time callback, so users do not need to paste tokens into Discord. That requires a
new ownership/authorization route in the MyBB plugin; the current public user lookup is not proof of
account ownership.

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

Reply to a Discord message and run:

```text
[p]mybb draft <forum_id> <subject>
```

A personally connected member publishes through their own token. Without a personal connection,
only Manage Server or the configured manager role may publish through the guild connector. The bot
shows **Edit**, **Publish**, and **Cancel** before sending anything.

The request includes the Discord jump URL, a stable `source=discord` / `source_id` pair, and an
idempotency key. Repeating the same source message updates its managed thread rather than creating a
duplicate. Discord attachments are represented by their CDN URLs.

## Stored data

Guild configuration contains the board URL, enabled state, default forum ID, and manager role ID.
Member configuration contains only a connection marker. Guild and member bearer tokens are stored
in separate Red shared-API-token namespaces. Personal tokens are removed when members disconnect,
when a board changes, when the guild connector is removed, or when Red processes that user's data
deletion request.
