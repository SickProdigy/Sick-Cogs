# Welcome


## Replacing the external Welcome cog

Sick-Cogs Welcome uses its own Config namespace. On first load it reads the legacy Trusty Welcome namespace (`144465786453`) as a one-time migration source and copies normalized guild settings into the Sick-Cogs namespace. It does not reuse the legacy namespace for normal operation.

Before replacing the external cog, review its current settings and messages. Then unload and uninstall it **without clearing Red data**, install/load Sick-Cogs Welcome, and check the owner-only migration report:

```text
[p]welcomeset migrationstatus
[p]welcomeset settings
```

The import preserves known greeting/goodbye settings, channels, enablement, grouping, whispers, bot messages and role, account-age rules, deletion behavior, filters, mention policy, and complete embed data. Malformed fields are replaced by safe defaults and recorded for review; reloads do not repeat a completed import.

Do not remove the legacy configuration until the status report and controlled greeting/goodbye tests confirm the new cog behaves as expected.
