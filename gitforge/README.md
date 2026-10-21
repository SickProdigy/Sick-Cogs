# GitForge

GitForge connects a Discord server to approved Gitea, GitHub, and GitLab repositories. It provides requester-bound issue drafts with an explicit preview and confirmation, bounded issue browsing, and separate channels for newly opened issues, CI failures or recoveries, and published releases.

## Setup

Self-hosted domains require approval from the bot owner:

    [p]gitownerset allowhost git.example.com

A server administrator then runs:

    [p]gitset

Use the buttons to add a provider connection and repository alias. Tokens are entered in a Discord modal, tested before saving, and stored in Red shared API tokens instead of guild configuration.

Repository controls:

    [p]gitset policy <alias> <admins|members>
    [p]gitset labels <alias> bug, discord-submitted
    [p]gitset issuechannel <alias> [channel]
    [p]gitset cichannel <alias> [channel]
    [p]gitset releasechannel <alias> [channel]
    [p]gitset prchannel <alias> [channel]
    [p]gitset removerepo <alias>
    [p]gitset removeconnection <alias>

Omit a notification channel to disable that route.

## Use

Open an issue draft:

    [p]git issue <repository-alias>

The requester can edit the title, description, and allowlisted labels in a modal, review the card, and explicitly submit or cancel. Replying to a Discord message before invoking the command adds its jump link to the forge issue attribution.

Browse, search, and inspect issues:

    [p]git issues <repository-alias>
    [p]git search <repository-alias> <search text>
    [p]git issue <repository-alias> <issue number>

Search returns at most ten repository-scoped issues across Gitea, GitHub, or GitLab. Supplying an issue number displays its state, author, labels, comment count, description, update time, and authoritative forge link. Read-only browsing does not require the repository issue-creation policy.

## Repository browsing

    [p]git repo <alias>
    [p]git commits <alias> [branch]
    [p]git prs <alias>
    [p]git release <alias>

These read-only commands use the existing guild connection and repository alias. Commit and pull/merge request lists return at most ten entries. Omitting a branch uses the provider default. Release cards use the provider's latest-release endpoint: GitHub/Gitea exclude drafts and prereleases; GitLab selects by release date. Tags alone are not releases. Missing releases are reported only after confirming repository access. Errors such as invalid branches, denied access, and rate limits remain errors.

Text is bounded and mentions disabled. Links are restricted to the configured HTTPS forge. As with existing issue browsing, registering a repository makes its readable content available through this guild's commands; administrators should only register repositories appropriate for their members. Prefix and hybrid command forms are supported.

## Monitoring

GitForge checks for due work every two minutes without exposing an HTTP listener. Equivalent issue
and CI requests are grouped by provider connection and repository, fetched once with bounded
concurrency, and distributed through each server's independent cursor. Repositories without an
active announcement destination are skipped. Provider failures back off per grouped request and do
not block unrelated repositories or Discord destinations.

The first successful poll seeds its cursor without reposting existing events. Later polls announce
newly observed issues and CI transitions into or out of failure. Optional webhook delivery can use
the same routing model in a later release.


## Release announcements (0.1.4)

Use `[p]gitset releasechannel <alias> #releases`. The first successful poll records existing
releases silently, including an empty repository baseline. Later polls post newly seen releases,
oldest first, with bounded notes, tag, date and a link to the configured forge. Mentions are disabled.
Gitea/GitHub drafts and prereleases, and future-dated GitLab releases, are excluded. GitLab does
not supply the same prerelease flag; its already-published releases are eligible regardless of tag naming.

Each poll reads one page of at most 50 release records per unique repository/credential combination.
This is bounded monitoring, not a complete historical sync: releases outside that page during a
long outage or large burst are not recovered. Each repository alias retains up to 200 delivered or
seeded release IDs. Release edits do not repost an already tracked ID (GitLab uses its release tag).
No release route means no release request. Release errors use the existing grouped backoff and do
not block issue or CI routes. A missing/inaccessible Discord destination does not consume pending
release IDs; successful sends before a later failure are saved for retry. As with ordinary Discord
posting, an ambiguous send or crash before saving can still produce a duplicate.

Omit the channel to pause the route. Re-enabling or moving channels keeps its history, so releases
published while paused can be caught within the bounded window. Re-registering the same alias
preserves release state; changing its repository or connection reseeds it. Existing issue/CI state
and settings are not migrated. No provider credentials or runtime dependencies are added.

Full live provider/Discord acceptance remains deferred until the planned command surface is ready.
Selected-push announcements remain a separate follow-up in #221.

## Pull/merge-request announcements (0.1.5)

Use `[p]gitset prchannel <alias> #development` (`mrchannel` is an alias). Omit the channel
to pause. The first successful poll silently seeds existing requests; later polls announce newly
observed requests and changes between open, closed, and merged, including known reopenings.
Title/body edits alone do not repost. These are read-only notifications, not merge controls.

One page of the 50 most recently updated requests is fetched per unique repository/credential
combination, using the existing two-minute loop, bounded concurrency, and failure backoff.
Disabled routes make no requests. Each alias keeps at most 200 states across reloads and channel
changes; changing its repository or connection reseeds. Missing destinations and failed sends do
not consume pending states; successful sends before a failure are retained. Mentions are disabled.

This observes snapshots, not a complete event log: multiple transitions between polls can collapse,
and long outages or bursts can exceed the 50-request window. Evicted states may be announced again
if subsequently observed. An ambiguous send or crash before saving can still duplicate a message.
Full live provider/Discord acceptance remains deferred in #221.
