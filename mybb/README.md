# MyBB

Connect Discord to SickGaming/MyBB through two independent private plugins:

- **External Sign-In** links a member's website account and synchronizes current
  named entitlements such as `vip` to Discord roles.
- **MyBB API v1** optionally powers selected-forum announcements, restricted forum
  browsing, and reviewed Discord-to-forum publishing.

RSSPublisher is not required. It remains useful for arbitrary RSS/Atom feeds;
MyBB's built-in announcements provide a shorter setup for forum owners.

## Requirements

- MyBB External Sign-In 0.1.14 or newer for account linking and membership roles.
- SickProdigy MyBB API v1 1.0.0 or newer only for optional forum tools.
- Public HTTPS URLs. The bot needs outbound HTTPS only; no inbound bot port or
  callback listener is required.

These plugins are currently private SickGaming products. Obtain and install them
through the authorized SickGaming distribution for your license. Never share
client secrets or API tokens in Discord messages, screenshots, logs, or support
tickets.

## New-server setup

### 1. Account linking and membership roles

In MyBB Admin CP, create a dedicated confidential External Sign-In client:

- Enable **Account linking service**.
- Keep `openid` and `entitlements` in its scopes.
- Configure a baseline entitlement such as `discord_link` for every forum group
  allowed to link, independently of paid/VIP access.
- Allow every named entitlement this Discord server may consume, such as
  `discord_link`, `vip`, `vip_gold`, or `vip_diamond`.
- A linking-only client does not need callback URLs.
- Do not reuse a website OIDC client or API-v1 token.

In Discord, an administrator runs:

```text
[p]mybbset membership setup
```

Press **Set client** and enter the complete provider URL, client ID, and secret:

```text
https://forum.example.com/external-signin
```

Map entitlements and enable synchronization:

```text
[p]mybbset membership map discord_link @Website-Linked
[p]mybbset membership map vip @VIP
[p]mybbset membership enabled true
[p]mybbset membership
```

Members run `[p]mybb`, `[p]mybb link`, `[p]mybb connect`, or
`[p]mybb membership`; each opens the same private account-linking controls.
Press **Link MyBB**,
open the private approval URL, sign in on the forum, and approve the displayed
Discord ID and server context. The bot polls outbound and immediately synchronizes
roles. `[p]mybb membership sync` requests a manual refresh. A member may remain
linked with only `discord_link`; losing `vip` removes the cog-managed VIP role
without removing the website link or the linked-member role. New mappings also
apply to already-linked members on their next successful synchronization.

The cog sends the stable numeric Discord user ID as the authorization identity.
It also sends the current canonical Discord account username (never a nickname,
mention, or legacy discriminator) as optional display metadata during linking and
later membership checks. External Sign-In may copy that plain username into an
existing MyBB custom profile field named `Discord`; it must never use the mutable
username to select, transfer, or authorize a link.

The loop wakes once a minute but checks a guild only on its 15-minute due cycle.
It handles at most 100 linked members per pass with a rotating cursor. Failures
back off up to six hours. Every authenticated HTTP 200 response is authoritative:
an `active` response reconciles the complete returned entitlement set, while
`inactive` and `unlinked` remove applicable cog-managed membership roles. Timeouts,
malformed responses, identity-binding mismatches, 401/403, rate limits, and server
failures preserve roles for later retry. A profile-display update failure must not
change membership state, roles, or the durable link.

Optional forum browsing and publishing remain available through
`[p]mybb tools` and the dedicated forum subcommands.

### 2. Optional API-v1 forum tools

Skip this section if the server only needs membership roles.

In MyBB Admin CP, open **Configuration → API access**. Create a dedicated token
owned by an appropriate forum service account. Grant only what is required:

- `forums:read` and `threads:read` for announcements/browsing.
- `threads:write` only for Discord-to-forum publishing.
- Restrict its readable and writable forum lists in the API policy.

Only the Discord server owner can configure the shared API connector:

```text
[p]mybbset setup
```

Press **Connect board** and enter the board root (not `/api/v1`) plus the API-v1
token. Example board root: `https://forum.example.com`.

The shared connector never impersonates individual Discord members. Forum browsing,
search, and publishing are limited to the Discord server owner and owner-selected
publisher roles:

```text
[p]mybbset publisherrole add @Content Creator
[p]mybbset publishingforum add 12
```

Reply to a Discord message and create a reviewed draft with:

```text
[p]mybb draft 12 Useful idea from Discord
```

Nothing is posted until the authorized user reviews and presses **Publish**.
The API token's MyBB write policy and the cog's publishing-forum allowlist must
both permit the destination.

### 3. Optional new-thread announcements

Add a route for one MyBB forum and Discord channel:

```text
[p]mybbset announcements add 12 #forum-news link
[p]mybbset announcements start 12
```

`link` posts only the authoritative thread URL so Discord can use the forum's
Open Graph preview. `card` posts a consistent embed with title, author, a short
preview, and a link back to the complete forum thread:

```text
[p]mybbset announcements mode 12 card
```

Starting without a number silently seeds existing threads. To intentionally post
up to five recent threads during first setup:

```text
[p]mybbset announcements start 12 3
```

Manage routes with:

```text
[p]mybbset announcements
[p]mybbset announcements stop 12
[p]mybbset announcements remove 12
```

Announcements poll on a 15-minute due cycle with capped exponential backoff.
Successful sends advance the per-forum marker; failed sends remain eligible for
retry. Only the first 20 current API results are considered, so this is bounded
notification polling rather than full historical synchronization.

## Separation and security

- External Sign-In credentials cannot browse or post forum content.
- API-v1 tokens cannot prove Discord/MyBB account ownership or grant membership roles.
- Changing or disconnecting the API-v1 connector does not alter membership links.
- Removing the External Sign-In client intentionally revokes links and removes
  cog-owned roles but does not alter the API-v1 connector.
- The cog never adopts or removes pre-existing unowned roles. Use dedicated
  membership roles because a later manual grant of an already tracked role cannot
  be distinguished automatically.
- Link credentials, API tokens, and client secrets live in separate Red shared API
  token namespaces. Non-secret configuration stores mappings, delivery cursors,
  verified issuer/subject metadata, status, and role ownership.

## Existing forum commands

Authorized publishers may use `[p]mybb forums`, `[p]mybb threads`, `[p]mybb thread`,
and bounded recent `[p]mybb search`. Friendly publishing destinations remain
available through `[p]mybbset forumalias` and `[p]mybbset defaultforum`.
