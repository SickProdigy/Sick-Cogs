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
[p]wolframexample [mathematics|science|society|everyday|surprises]
[p]wolframrandom [category]
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

## Curated examples

`[p]wolframexample` picks a bundled example without making an API request. Use an optional category — `mathematics`, `science`, `society`, `everyday`, or `surprises` — to narrow it. The response links to the attributed Wolfram|Alpha Examples category and shows the normal `[p]wolfram` command to run it.
