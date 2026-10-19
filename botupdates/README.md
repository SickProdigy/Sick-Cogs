# BotUpdates

BotUpdates turns project changes into bot-branded announcements while keeping publication behind human review. Version 0.2.1 can collect public Sick-Cogs and configured GitHub upstream commits automatically, but it never approves or publishes a draft.

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
[p]botupdates draft sources 1
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

Scheduled slots are the 1st and 15th of each month in UTC. An hourly task checks whether the current slot is due; it does not repeatedly query Gitea between due slots. Starting records the current commit without creating an old-history draft. Each due run searches at most 100 commits for each saved cursor, creates one `digest` draft, attaches every commit as a source, and posts a preview to the private review channel.

If an unpublished draft is already active, collection waits rather than replacing it. API failures retain the cursor and store a credential-free status message. A missing cursor in the bounded history also stops collection instead of silently skipping changes. `collector check` runs the same process manually.

The collector reads the public `sickprodigy/Sick-Cogs` Gitea API and requires no repository credential. Optional AI-assisted wording is not implemented yet.

## Public GitHub upstream sources

Owners can register up to 10 named sources with an explicit branch and optional repository-relative cog path:

```text
[p]botupdatesowner upstream add dictionary aikaterna/aikaterna-cogs v3 dictionary
[p]botupdatesowner upstream
[p]botupdatesowner upstream enabled dictionary false
[p]botupdatesowner upstream enabled dictionary true
[p]botupdatesowner upstream remove dictionary
```

Registration checks the public repository and seeds the latest matching commit without drafting old history. Omit the path to watch the entire repository. Sources are opt-in; nothing is imported automatically from Downloader or `external_cogs.json`. Private repositories and other upstream hosts are not supported. No GitHub token or new dependency is required; requests use the [GitHub commits API](https://docs.github.com/en/rest/commits/commits#list-commits) branch and path filters.

Use the same `collector start`, `stop`, and `check` commands for the combined collector. Each upstream retains its own cursor across restarts and pauses. Resume includes changes since that saved cursor, subject to the history limit. Remove discards that source's cursor but leaves existing draft references intact; re-adding seeds current history and does **not** recover missed commits.

The hourly task makes no upstream requests between completed twice-monthly slots. A due run makes at most two requests per enabled source (50 commits per page), plus the project requests. Errors retry on the next hourly check, not every minute. GitHub rate limits, redirects/renamed repositories, empty or malformed history, and missing cursors stop the entire collection without consuming any source cursors. Status identifies the failed upstream. Pause or remove an obsolete source explicitly; collection never silently skips it.

All collected references, the draft, cursors and completed slot are saved together. An active draft defers new collection; manual creation and collection cannot replace one another. Overlapping sources attach the same commit once. Draft text distinguishes project changes from **available upstream changes**, which are not evidence that the bot installed them. Nothing installs cogs, approves drafts or publishes automatically. Review every reference with `botupdates draft sources <page>` (five per page), since the preview card is intentionally compact.

Full Discord review and delivery testing remains required before release.

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
