# Incidents

Incidents is a staff-only interactive browser and reporting center for Red's existing ModLog cases. Red ModLog remains the source of truth; this cog does not copy moderation cases into another database.

## Staff dashboard

```text
[p]incidents
```

The dashboard provides:

- Paginated case-detail cards
- Search by case number
- Search by member mention or Discord user ID, including departed users retained by ModLog
- Action-type and 1–365 day filters
- Staff activity summaries
- Time since the latest overall incident, warning, kick, mute, and ban

Case cards show the action, user and ID, moderator, reason, date, expiration, channel, and amendment information when Red recorded those fields.

## Access setup

Moderators and members with **Manage Messages**, **Manage Server**, or **Administrator** can use the Incident Center automatically. Administrators can grant additional staff roles access with:

```text
[p]incidentset
```

Command fallbacks are also available:

```text
[p]incidentset access add @StaffRole
[p]incidentset access remove @StaffRole
```

## Text-command fallbacks

```text
[p]incidents recent [limit]
[p]incidents member <mention_or_user_id>
[p]incidents case <case_number>
[p]incidents filter <action> [days=30]
[p]incidents summary [days=7]
```

Load Red's `modlog` and normal moderation cogs first; they remain responsible for creating and maintaining cases.

Scheduled roundups, status cards, evidence references, web access, and cross-server ban systems are intentionally outside this testing release.
