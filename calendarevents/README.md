# CalendarEvents

CalendarEvents connects each Discord server to a Google Calendar owned by that community. Google is the source of truth: Discord manages events while Apple Calendar, Outlook, MyBB, websites, and other tools can subscribe to the same calendar.

The bot uses one Google service account. A server owner shares only their chosen calendar with that service-account email, so servers do not share calendar data and nobody grants access to a personal Google account.

## Bot-owner setup

1. Create a Google Cloud project and enable the Google Calendar API.
2. Create a service account. It needs no Google Cloud project roles.
3. Create a JSON key for the service account.
4. Privately configure Red's shared API tokens:

   ```
   [p]set api googlecalendar client_email,<client_email> private_key,<private_key>
   ```

Keep the private key's line breaks encoded as `\n` if needed. Never run this command publicly, commit the JSON key, or put it in cog configuration.

## Server setup

A member with **Manage Server** runs:

1. `[p]calendarset serviceaccount` to see the service-account email.
2. Share the desired Google Calendar with that email and grant **Make changes to events**.
3. Copy its calendar ID from Google Calendar's *Integrate calendar* settings.
4. Run `[p]calendarset calendar <calendar ID>`. Access is verified before it is saved.
5. Optionally set `channel #events`, `managerrole @Event Team`, `timezone America/New_York`, and an ICS subscription URL.

Use `[p]calendarset info` for a safe settings summary. It never prints the calendar ID or private ICS URL.

## Commands

- `[p]calendar` - setup status and a short guide
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
