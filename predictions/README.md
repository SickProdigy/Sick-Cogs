# Predictions

Predictions provides server-local games with two independent modes:

- Free predictions retain the original bragging-rights leaderboard and never touch Red Bank.
- Optional play-credit predictions use Red Bank currency for stakes and pari-mutuel payouts. Credits are fictional server currency only; they are not money, investments, or guaranteed value.

## Quick start

Use the interactive creator to choose a free, fixed-stake, or ranged-stake prediction:

```text
[p]predict setup
```

The equivalent commands are:

```text
[p]predict create 1d Will our team win? | Yes | No
[p]predict createbank 1d 100 Will our team win? | Yes | No
[p]predict createbank 1d 10-500 Will our team win? | Yes | No
```

Outcome buttons appear on open prediction cards. Fixed-stake buttons show a confirmation; ranged stakes ask for an amount first. The confirmation names the configured currency, exact stake, and refund behavior before credits are withdrawn. Command entry remains available as `[p]predict stake <market-id> <amount> <outcome>`.

## Payout rule

A winning member receives their accepted stake back plus a share of the losing pool proportional to their stake. Integer rounding is deterministic. If nobody selected the resolved outcome, or the prediction is cancelled, every accepted stake is refunded. An optional house cut applies only to the losing pool and defaults to zero; enabling it requires a treasury member.

Accepted stakes are locked, though members may change their selected outcome without another withdrawal. Prepared withdrawals and settlement credits are journaled so retries reconcile instead of charging or paying twice. A blocked or ambiguous Bank operation freezes finalization for moderator review.

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

Bank integration is disabled by default. Disabling it stops new staked predictions but does not abandon existing funded markets. The limits command sets minimum stake, maximum stake, and optional per-user exposure. When Red Bank uses global balances, only the bot owner may enable Bank predictions because those balances span servers.

Anyone may create a market. Votes lock at its deadline. The creator settles after it closes; server managers may settle early or handle recovery. Free prediction winners receive one non-transferable server point.
