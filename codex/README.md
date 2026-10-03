# Codex usage reminders

This cog privately tracks a manually entered Codex allowance-cycle schedule and optional
remaining-percentage estimate. It sends reset reminders by DM and never posts a member's
estimate or schedule to a server.

## Why tracking is manual

Official OpenAI documentation supports Sign in with ChatGPT for eligible plan-backed
inference, but directs apps to ChatGPT Settings → Usage for usage tracking. It does not
document an API that returns a personal remaining allowance or reset timestamp. A
limit-exceeded response also cannot be used to infer a reset time. This cog therefore does
not scrape ChatGPT, collect cookies, store OAuth tokens, or invent live balances.

References:

- https://developers.openai.com/siwc/token-sharing-open-source
- https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions
- https://developers.openai.com/siwc/token-sharing-open-source/errors-and-recovery

## Commands

- [p]codex — DM your private status.
- [p]codex setup HOURS_UNTIL_RESET [CYCLE_HOURS=168] — start recurring tracking.
- [p]codex remaining PERCENT — update your manual estimate.
- [p]codex reminders HOURS... — choose offsets such as 48 24 1.
- [p]codex threshold PERCENT — set the low-estimate threshold.
- [p]codex pause or resume — control delivery.
- [p]codex disconnect true — delete all stored Codex reminder data.
- [p]codex about — explain the official-data boundary.

The provider interface is intentionally separate so a future documented usage-status API
can replace manual snapshots without changing scheduling or privacy behavior.
