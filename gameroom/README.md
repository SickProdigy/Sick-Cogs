# GameRoom

GameRoom expands Red's built-in games with a single discovery surface and additional
free-play dice and card games.

## Commands

- `[p]games` or `[p]gameroom` opens the unified launcher.
- `[p]dice [notation]` rolls `d20`, `2d6`, `2d8+3`, and similar bounded notation.
- `[p]coinflip` flips a coin.
- `[p]blackjack` or `[p]21` starts an interactive hand.
- `[p]higherlower` starts a higher-or-lower run.
- `[p]highcard` draws once against the dealer.
- `[p]games rules [game]` shows concise rules.

The launcher buttons run quick games directly and open interactive card games in the
same message. Interactive games provide Play Again and Quit controls, visibly expire
after inactivity, and limit each member to one active game per channel.

When Red's General or Economy cogs are loaded, the launcher also lists their native
`roll`, `flip`, `rps`, and `slot` commands. GameRoom does not replace those
commands.

## Initial release boundary

Version 0.1.1 is free play only. It stores no game or user data. Blackjack uses one
shuffled 52-card deck, dealer stands on all 17s, and Double draws exactly one card
before standing. Higher-or-lower treats aces as high, continues on ties, and ends
after a wrong guess or ten correct guesses.

Bank wagering, multiplayer tables, Connect Four, checkers, tournaments, and
leaderboards are deferred until the single-player foundation has been validated.
