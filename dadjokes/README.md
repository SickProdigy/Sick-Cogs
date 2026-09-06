# DadJokes

Random dad jokes from [icanhazdadjoke.com](https://icanhazdadjoke.com/).

## Commands

- `[p]dadjoke` - Fetch one random dad joke in the current channel.

## Scheduled random dad jokes

Server moderators with `Manage Server` can configure automatic dad joke posts:

- `[p]dadjokeset channel [#channel]` - Set the channel for scheduled jokes. Defaults to the current channel.
- `[p]dadjokeset interval <minutes>` - Set how long to wait before the next scheduled joke. Minimum: 5 minutes. Maximum: 43200 minutes (30 days).
- `[p]dadjokeset enable` - Enable scheduled random dad jokes.
- `[p]dadjokeset disable` - Disable scheduled random dad jokes.
- `[p]dadjokeset force` - Send a joke to the configured channel now and reset the wait timer.
- `[p]dadjokeset settings` - Show the current scheduled joke settings.

The scheduler stores guild-level channel and timing settings only. It does not store user data.
