# Wolfram

Query Wolfram|Alpha for text answers, images, and step-by-step solutions.

## Setup

Create a Wolfram|Alpha AppID and store it through Red's shared API-token system in a private owner-only channel:

```text
[p]set api wolfram appid,APP_ID
```

Never paste an AppID into public chat, logs, or issue reports.

## Configuration ownership

Wolfram stores no cog configuration or credentials. The AppID lives only in Red's shared API-token storage.

## Commands

```text
[p]wolfram <question>
[p]wolframimage <question>
[p]wolframsolve <question>
```

Running `[p]wolfram` without a question displays command help without contacting Wolfram|Alpha.

## Errors

The cog distinguishes between:

- a missing AppID;
- a rejected AppID;
- provider rate limiting;
- request timeouts;
- network failures; and
- temporary provider errors.
