# Predictions

Predictions provides server-local games with two independent modes:

- Free predictions retain the original bragging-rights leaderboard and never touch Red Bank.
- Optional play-credit predictions use Red Bank currency for entries and pari-mutuel payouts. Credits are fictional server currency only; they are not money, investments, or guaranteed value.

## Interactive home

Open the Predictions home panel with either command:

```text
[p]predict
[p]predictions
```

The panel provides **Start**, **Open**, **Recent**, **Mine**, and **Leaderboard** actions. Starting a prediction uses one interactive flow: choose Free prediction, Fixed entry, or Choose amount, then enter the duration, question, and outcomes. Play-credit choices appear only when a server administrator has enabled them.

Every open prediction card has outcome buttons. Free entries record the pick immediately. A fixed play-credit entry shows its exact cost before confirmation; a flexible entry asks for an amount first. The card also has a **Manage** button for its creator and server managers, providing Resolve, Cancel and refund, and Audit actions.

## Command fallback

The same main activities remain available through commands and `[p]help predict`:

```text
[p]predict start
[p]predict list
[p]predict recent
[p]predict mine
[p]predict status <market-id>
[p]predict pick <market-id>
[p]predict resolve <market-id> <outcome>
[p]predict cancel <market-id>
[p]predict leaderboard
```

`status` shows the prediction and your existing pick or accepted entry. `pick` reopens its interactive card. Older creation and entry commands remain hidden compatibility routes but are not part of the normal user flow.

## Payout rule

A winning member receives their accepted entry back plus a share of the losing pool proportional to their entry. Integer rounding is deterministic. If nobody selected the resolved outcome, or the prediction is cancelled, every accepted entry is refunded. An optional house cut applies only to the losing pool and defaults to zero; enabling it requires a treasury member.

Accepted amounts are locked, though members may change their selected outcome without another withdrawal. Prepared withdrawals and settlement credits are journaled so retries reconcile instead of charging or paying twice. A blocked or ambiguous Bank operation freezes finalization for moderator review.

## Administration

```text
[p]predictset bank
[p]predictset bank enable
[p]predictset bank disable
[p]predictset bank limits 10 10000 50000
[p]predictset bank housecut 0
[p]predictset bank housecut 5 @TreasuryMember
[p]predict audit <market-id>
[p]predictset channel #predictions
```

Bank integration is disabled by default. Disabling it stops new play-credit predictions but does not abandon existing funded markets. The limits command sets minimum entry, maximum entry, and optional per-user exposure. When Red Bank uses global balances, only the bot owner may enable play-credit predictions because those balances span servers.

Anyone may start a prediction. Entries lock at its deadline. The creator resolves it after it closes; server managers may resolve early or handle recovery. Free prediction winners receive one non-transferable server point.
