# BotUpdates

BotUpdates creates bot-branded announcements from project and upstream references. Global delivery stays behind a human review workflow:

1. The owner configures a private review channel and trusted editors or publishers.
2. A publisher creates a `major`, `digest`, or `routine` draft.
3. Editors attach project/upstream commit, release, or changelog references.
4. Editors submit full replacement-text proposals; a publisher explicitly applies one.
5. A publisher explicitly approves the final preview and separately publishes it.
6. Successful deliveries and source references are recorded to prevent repeats.

No AI provider, repository credential, automatic collection, or scheduler is required by this prototype. Draft text stays human-controlled.

## Owner and editor workflow

```text
[p]botupdatesowner reviewchannel #private-review
[p]botupdatesowner editoruser add @Editor
[p]botupdatesowner editorrole add @UpdateEditors
[p]botupdatesowner publisher add @Publisher
[p]botupdates draft create major Bot Updates | Human-written announcement text
[p]botupdates draft source upstream https://example.com/release v2.0 Upstream Cog
[p]botupdates draft propose Revised human-written announcement text
[p]botupdates draft proposals
[p]botupdates draft apply 1
[p]botupdates draft preview
[p]botupdates draft approve
[p]botupdates draft publish
```

## Guild subscription

A server administrator selects a channel and delivery mode:

```text
[p]botupdates subscribe #bot-news major-only
[p]botupdates subscribe #bot-news twice-monthly
[p]botupdates subscribe #bot-news all-approved
[p]botupdates mention @Updates
[p]botupdates unsubscribe
```

`major-only` receives major drafts. `twice-monthly` receives major and manually approved digest drafts. `all-approved` also receives routine drafts. The prototype does not publish on a timer; delivery mode controls which approved draft types a guild receives.

Mentions are off by default. If configured, only that exact guild role may be mentioned. Failed guild deliveries remain retryable while successful deliveries are not repeated.
