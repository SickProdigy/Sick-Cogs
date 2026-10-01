# Reminder

Reminder schedules private reminder messages and restores pending reminders when the cog or bot
restarts.

## Commands

- `[p]remind <time> <text>` - Create a reminder. `remindme` is an alias.
- `[p]remind list` - List your pending reminders.
- `[p]remind forget one <number>` - Remove one pending reminder.
- `[p]remind forget all` - Remove all pending reminders.
- `[p]remind offset <hours>` - Set an optional UTC offset used for displayed calendar times.

Durations accept combinations such as `10min`, `1h30min`, or `2d12h`. Reminders are delivered by DM.
Use `min` for minutes and `mo` for months; bare `m` is rejected as ambiguous.
In server channels, successful reminder commands receive a 👍 reaction and a short confirmation
reply. Other members can react to the command with 👍 to schedule the same private reminder for
themselves. The original author is subscribed automatically, repeated reactions do not create
duplicates, and reminders created in DMs are never shared. Use `[p]remind forget` to cancel pending
reminders. Invalid-duration guidance automatically disappears after a short delay.
The configured offset only changes displayed calendar times; it does not change when a reminder is
delivered.

When creating a reminder as a reply to another Discord message, the cog saves that message's jump
link and includes a **View original message** button in the reminder DM and a link in the pending-reminder list.
