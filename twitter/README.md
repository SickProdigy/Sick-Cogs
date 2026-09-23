# Twitter

Publish new posts from selected X accounts into selected Discord channels. The initial provider is
the official X API v2. It resolves each username once, stores the numeric user ID, and polls with a
persisted `since_id` cursor so setup does not dump old posts and ordinary polling requests only new
content.

## Native X API setup

Create an X developer app, fund its pay-per-use balance, and give the bot owner its app-only bearer
token. Store it in Red's shared API token storage:

```text
[p]set api twitter bearer_token,YOUR_TOKEN
```

The token is never stored in guild configuration or displayed. X currently bills reads per resource
returned, including user lookups and posts. Review the current pricing in the X developer console
before enabling this cog.

## Server setup

Members with Manage Server can configure subscriptions:

```text
[p]twitterset test XDevelopers
[p]twitterset add #news XDevelopers
[p]twitterset add #gaming SomeGame
[p]twitterset interval 5
[p]twitterset enabled true
[p]twitterset status
[p]twitterset list
```

Adding a subscription records the account's latest post as its starting cursor, so only future posts
are announced. The same account can target multiple channels. Remove one mapping with:

```text
[p]twitterset remove #news XDevelopers
```

Optionally delegate subscription management to a staff role:

```text
[p]twitterset managerrole @Staff
```

Only Manage Server can change that role. Replies and reposts are excluded in the initial release.
Unavailable channels are removed safely. Authentication, insufficient-credit, and rate-limit errors
are logged without exposing the bearer token.

## Provider direction

The API client is isolated from Discord delivery so a terms-compliant RSS or other free provider can
be added later. Unofficial scraping is intentionally not part of the native API milestone because it
is unreliable and may conflict with X's terms.
