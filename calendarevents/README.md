# CalendarEvents

Create server-scoped events without connecting the bot to anyone's calendar account.

- [p]calendar add 2026-09-20T18:00Z 2026-09-20T20:00Z RLCS watch party
- [p]calendar list
- [p]calendar show EVENT-ID
- [p]calendar remove EVENT-ID

calendar show provides a Google Calendar template link and attaches a portable ICS file.
The initial release deliberately has no Google OAuth, automatic calendar writes, or message capture.
