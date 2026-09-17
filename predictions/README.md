# Predictions

A server-local prediction game for bragging rights. It does not use currency, tokens, wagers, wallets, prizes, or any real-world value.

```text
[p]predict create 1d Will our team win? | Yes | No
[p]predict vote 1 yes
[p]predict list
[p]predict settle 1 yes
[p]predict leaderboard
```

Anyone may create a market. Votes lock at its deadline. The creator settles after it closes; server managers may settle or override when necessary. Correct picks receive one non-transferable server point.

Use `[p]predictset channel #predictions` to send new markets to a dedicated channel, or leave it unset to post them in the channel where they were created.
