# Sick-Cogs

Sick-Cogs is a Red-DiscordBot cog repository maintained for SickProdigy's Discord projects.

The repo is organized around two goals:

- Keep `main` as the stable release branch.
- Use `develop` for active work, experiments, and cogs that are still being shaped.

Contribution notes are in [CONTRIBUTING.md](CONTRIBUTING.md).

## Installation

Add this repository with Red's Downloader:

```text
[p]repo add Sick-Cogs https://github.com/SickProdigy/Sick-Cogs
```

Review the available cogs and each cog's setup message before installing:

```text
[p]repo list Sick-Cogs
[p]cog install Sick-Cogs <cog>
[p]load <cog>
```

Use `[p]cog update` to install future updates. Replace `[p]` with your bot's configured prefix when typing commands.

## Available Cogs

- `avatar` - Display a Discord user's avatar as a direct, clickable image URL.
- `azerothcore` - AzerothCore/WoW server utilities using SOAP.
- `coc` - Clash of Clans clan and war status utilities.
- `dadjokes` - On-demand and scheduled random dad jokes.
- `dictionary` - English definitions, pronunciations, related words, and community slang.
- `donate` - Configurable donation information command.
- `giveaways` - Persistent button giveaways with core entry rules and weighted draws.
- `meme` - Local meme generation from original bundled templates and Discord avatars.
- `movies` - Configurable TMDb new movie release announcements.
- `nsfw` - NSFW-channel-only mature media with configurable external providers.
- `reminder` - Persistent personal reminders with restart recovery and timezone-aware display.
- `rsspublisher` - Configurable RSS and Atom feed publishing and notification tools.
- `runescape` - RuneScape/OSRS player hiscores and interactive wiki lookups.

Develop-only cogs and experiments are not part of the stable Index release.

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

## Credits

Several cogs began with, or retain portions of, work by community authors who are
listed in their cog metadata: skeith (Avatar), UltimatePancake (DadJokes), flaree
(Giveaways), Predä and aikaterna (NSFW), and TrustyJAID (RuneScape). RSSPublisher
was originally based on aikaterna's RSS cog and has since been substantially rewritten.
Their contributions remain credited regardless of current maintenance ownership. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for preserved upstream notices and links.

## License

Sick-Cogs original code is licensed under the MIT License. Third-party code keeps any license terms that apply to its original source.
