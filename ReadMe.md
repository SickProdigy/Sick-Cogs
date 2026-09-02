# Sick-Cogs

Sick-Cogs is a Red-DiscordBot cog repository maintained for SickProdigy's Discord projects.

The repo is organized around two goals:

- Keep `main` as the stable release branch.
- Use `develop` for active work, experiments, and cogs that are still being shaped.

Contribution notes are in [CONTRIBUTING.md](CONTRIBUTING.md).

## Maintained Cogs

- `azerothcore` - AzerothCore/WoW server utilities using SOAP.
- `coc` - Clash of Clans clan and war status utilities.
- `donate` - Configurable donation information command.
- `runescape` - RuneScape/OSRS player hiscores and interactive wiki lookups.

## Experimental or Learning Cogs

These folders may be used for testing, learning, or local customization before they become maintained Sick-Cogs:

- `assistant`
- `dadjokes`
- `dictionary`
- `reminder`
- `rlstats`
- `rss`
- `welcome`
- `wolfram`

## External Cog Sync Helper

Source links for refreshable external cog folders live in `external_cogs.json`.

Preview configured sources:

```bash
python sync_external_cogs.py --list
```

Preview a specific sync without replacing files:

```bash
python sync_external_cogs.py --only dadjokes
```

Apply updates to selected local cog folders:

```bash
python sync_external_cogs.py --apply --only dadjokes
```

Apply all configured external cog updates:

```bash
python sync_external_cogs.py --apply
```

The sync helper replaces selected local cog folders from upstream. Commit or stash local edits before using `--apply`.

## License

Sick-Cogs original code is licensed under the MIT License. Third-party code keeps any license terms that apply to its original source.
