# GameRoom

GameRoom expands Red's built-in games with a single discovery surface, additional
free-play games, and optional fictional-currency wagers through Red's bank.

## Commands

- `[p]games` or `[p]gameroom` opens the unified launcher.
- `[p]dice [notation]` rolls `d20`, `2d6`, `2d8+3`, and similar bounded notation.
- `[p]coinflip` flips a coin for free.
- `[p]coinflip <heads|tails> <amount>` places an enabled even-money wager.
- `[p]blackjack` or `[p]21` starts a free interactive hand.
- `[p]higherlower` starts a free higher-or-lower run.
- `[p]highcard [amount]` draws once against the dealer.
- `[p]craps [amount]` plays basic pass-line craps.
- `[p]games rules [game]` shows concise rules.

The launcher buttons run quick games directly and open interactive card games in the
same message. Interactive games provide Play Again and Quit controls, visibly expire
after inactivity, and limit each member to one active game per channel.

When Red's General or Economy cogs are loaded, the launcher also lists their native
`roll`, `flip`, `rps`, and `slot` commands. GameRoom does not replace them.

## Wager configuration

Wagering is disabled by default and uses fictional Red bank currency only.

Administrators with Manage Server can configure it:

```text
[p]gameroomset
[p]gameroomset wagering true
[p]gameroomset minimum 10
[p]gameroomset maximum 1000
[p]gameroomset dailyloss 5000
[p]gameroomset ledger 10
```

A zero daily-loss limit disables that limit. Every wager is withdrawn before its
outcome is revealed and receives a transaction ID. Coin flip, high card, and pass-line
craps pay even money. High-card ties refund the stake. Payouts cannot exceed Red's
configured maximum balance.

GameRoom keeps a bounded transaction ledger. If a restart interrupts withdrawal,
settlement, or refund, that record becomes `uncertain` rather than being retried and
possibly paid twice. Administrators can see sanitized recent records and should ask
the bot owner to reconcile uncertain entries.

## Rules

Blackjack uses one shuffled 52-card deck. The dealer stands on all 17s, and Double
draws exactly one card before standing. Higher-or-lower treats aces as high, continues
on ties, and ends after a wrong guess or ten correct guesses.

Pass-line craps uses two six-sided dice. A come-out roll of 7 or 11 wins; 2, 3, or 12
loses. Any other total becomes the point. Rolling the point again before 7 wins.

## Current boundary

Version 0.2.0 supports free play plus immediate wagers for coin flip, high card, and
pass-line craps. Blackjack wagers and Double, higher-or-lower cash-out, craps side
bets, multiplayer buy-in pots, Connect Four, checkers, tournaments, and leaderboards
remain deferred until the transaction boundary has completed live acceptance.
