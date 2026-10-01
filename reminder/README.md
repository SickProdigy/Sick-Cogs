# Reminder

Reminder schedules private reminder messages and restores pending reminders when the cog or bot
restarts.

## Commands

- `[p]remind <time> <text>` - Create a reminder. `remindme` is an alias.
- `[p]remind list` - List your pending reminders.
- `[p]remind forget one <number>` - Remove one pending reminder.
- `[p]remind forget all` - Remove all pending reminders.
- `[p]remind offset <hours>` - Set an optional UTC offset used for displayed calendar times.

Durations accept combinations such as `10m`, `1h30m`, or `2d12h`. Reminders are delivered by DM.
The configured offset only changes displayed calendar times; it does not change when a reminder is
delivered.
