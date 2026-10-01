# GitForge

GitForge connects a Discord server to approved Gitea, GitHub, and GitLab repositories. It provides requester-bound issue drafts with an explicit preview and confirmation, bounded issue browsing, and separate channels for newly opened issues and CI failures or recoveries.

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
    [p]gitset removerepo <alias>
    [p]gitset removeconnection <alias>

Omit a notification channel to disable that route.

## Use

Open an issue draft:

    [p]git issue <repository-alias>

The requester can edit the title, description, and allowlisted labels in a modal, review the card, and explicitly submit or cancel. Replying to a Discord message before invoking the command adds its jump link to the forge issue attribution.

List recent open issues:

    [p]git issues <repository-alias>

## Monitoring

The initial release polls every two minutes, which works without exposing an HTTP listener. The first successful poll seeds its cursor without reposting existing events. Later polls announce newly observed issues and CI transitions into or out of failure. Optional webhook delivery can use the same routing model in a later release.
