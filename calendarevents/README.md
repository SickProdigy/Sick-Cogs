# CalendarEvents

CalendarEvents manages Discord's native scheduled events and can connect each Discord server to its own Google Calendar. Discord events and repeated reminders work without Google. Google remains the planned shared source for eventual Discord, Apple Calendar, Outlook, MyBB, and website synchronization.

The optional Google integration uses one bot-owned service account. A server owner shares only their chosen calendar with that service-account email, so servers do not share calendar data and nobody grants access to a personal Google account.

## Discord scheduled events and reminders

Run `[p]calendar` to open the dashboard. A server manager or configured calendar manager can create an external Discord scheduled event with a title, timezone-aware start/end, location or link, and description. The dashboard also lists the server's upcoming native Discord events.

Configure repeated reminders with:

```text
[p]calendarset reminderchannel #general
[p]calendarset reminderrole @Events
[p]calendarset reminders 1440 720 360 60
```

That example posts at roughly 24 hours, 12 hours, 6 hours, and 1 hour before each event. Choose 1–8 unique offsets between 0 and 40320 minutes; `0` means event start. Omit the channel to disable bot reminders and omit the role to stop mentions. Discord's own Interested notifications continue to work independently.

Reminder delivery state is persisted, so cog reloads and bot restarts do not intentionally repeat a delivered reminder. If reminders are enabled after an offset already passed, the cog sends only the closest currently relevant reminder rather than posting every missed reminder at once.

## Optional Google bot-owner setup

1. Create a Google Cloud project and enable the Google Calendar API.
2. Create a service account. It needs no Google Cloud project roles.
3. Create a JSON key for the service account.
4. Privately configure Red's shared API tokens:

   ```
   [p]set api googlecalendar client_email,<client_email> private_key,<private_key>
   ```

Keep the private key's line breaks encoded as `\n` if needed. Never run this command publicly, commit the JSON key, or put it in cog configuration.

## Optional Google server setup

A member with **Manage Server** runs:

1. `[p]calendarset serviceaccount` to see the service-account email.
2. Share the desired Google Calendar with that email and grant **Make changes to events**.
3. Copy its calendar ID from Google Calendar's *Integrate calendar* settings.
4. Run `[p]calendarset calendar <calendar ID>`. Access is verified before it is saved.
5. Optionally set `channel #events`, `managerrole @Event Team`, `timezone America/New_York`, and an ICS subscription URL.

Use `[p]calendarset info` for a safe settings summary. It never prints the calendar ID or private ICS URL.

## Commands

- `[p]calendar` - interactive Discord event dashboard and setup status
- `[p]calendar add 2026-09-20T18:00-04:00 2026-09-20T20:00-04:00 Team meeting`
- `[p]calendar list`
- `[p]calendar show <event ID>` - Google link and portable ICS copy
- `[p]calendar remove <event ID>`
- `[p]calendarset` - server configuration

Times currently use ISO 8601 with an explicit offset. Creating and deleting events requires **Manage Server** or the configured calendar manager role.

## Apple Calendar, Outlook, and MyBB

Google Calendar can publish an ICS subscription URL from *Integrate calendar*. Apple Calendar, Outlook, MyBB plugins, and websites can consume it, mirroring the same events without making Discord a second source of truth.

A private ICS URL acts like a password. Only give it to trusted integrations. The optional `calendarset ics` setting stores it for future integrations but never displays it.

## Data boundaries

Google stores event data. Red stores each guild's calendar mapping, optional manager role/channel/timezone/ICS settings, and minimal creator references. Service-account credentials live only in Red's shared API-token store.
