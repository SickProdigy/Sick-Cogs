# BotUpdates

BotUpdates turns project changes into bot-branded announcements while keeping publication behind human review. Version 0.2.0 can collect public Sick-Cogs commits automatically, but it never approves or publishes a draft.

## Review and publication

1. The owner configures a private review channel and trusted editors or publishers.
2. A publisher creates a manual draft, or the scheduled collector creates a digest draft.
3. Editors review sources and propose complete replacement text.
4. A publisher applies an edit, explicitly approves the result, and separately publishes it.
5. Successful deliveries and published source references are retained so retries and later collection do not duplicate work.

```text
[p]botupdatesowner reviewchannel #private-review
[p]botupdatesowner editoruser add @Editor
[p]botupdatesowner editorrole add @UpdateEditors
[p]botupdatesowner publisher add @Publisher
[p]botupdates draft preview
[p]botupdates draft propose Revised human-written announcement text
[p]botupdates draft proposals
[p]botupdates draft apply 1
[p]botupdates draft approve
[p]botupdates draft publish
```

Manual drafts and sources remain available:

```text
[p]botupdates draft create major Bot Updates | Human-written announcement text
[p]botupdates draft source upstream https://example.com/release v2.0 Upstream Cog
```

## Twice-monthly Sick-Cogs collection

The owner can seed the current public `main` commit and enable collection:

```text
[p]botupdatesowner collector
[p]botupdatesowner collector start
[p]botupdatesowner collector check
[p]botupdatesowner collector stop
```

Scheduled slots are the 1st and 15th of each month in UTC. An hourly task checks whether the current slot is due; it does not repeatedly query Gitea between due slots. Starting records the current commit without creating an old-history draft. Each due run fetches at most 100 commits after the saved cursor, creates one `digest` draft, attaches every commit as a source, and posts a preview to the private review channel.

If an unpublished draft is already active, collection waits rather than replacing it. API failures retain the cursor and store a credential-free status message. A missing cursor in the bounded history also stops collection instead of silently skipping changes. `collector check` runs the same process manually.

The collector reads the public `sickprodigy/Sick-Cogs` Gitea API and requires no repository credential. Automatic upstream-cog collection and optional AI-assisted wording are not implemented yet.

## Guild subscriptions

```text
[p]botupdates subscribe #bot-news major-only
[p]botupdates subscribe #bot-news twice-monthly
[p]botupdates subscribe #bot-news all-approved
[p]botupdates mention @Updates
[p]botupdates unsubscribe
```

`major-only` receives major drafts. `twice-monthly` receives major and approved digest drafts. `all-approved` also receives routine drafts. Delivery still occurs only when a publisher runs `draft publish`; the schedule creates review drafts, not public posts.

Mentions are disabled by default. When configured, only the selected guild role may be mentioned. Failed guild deliveries remain retryable while successful deliveries are not repeated.
