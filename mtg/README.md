# MTG

MTG is an experimental two-player Magic: The Gathering rules prototype for Red.

Version 0.2.0 supports a small curated card pool and two fixed 60-card beginner decks. It adds stable Scryfall printing/oracle identifiers, card search and detail embeds, and paginated private hand images with a text fallback. It is not a complete Magic implementation and does not yet include collections, packs, trading, Commander, Standard rotation, or an economy.

## Play

1. Run `[p]mtg challenge @member`.
2. Both players privately inspect their hands with **View hand**.
3. Each player keeps or mulligans.
4. Use `[p]mtg play <hand-position> [target]`.
5. Use **Pass / next** or `[p]mtg pass` for priority and phase progression.
6. Use `[p]mtg attack <battlefield positions>`.
7. The defender uses `[p]mtg block <attacker-position:blocker-position>`.

Player targets use a Discord user ID. Permanent targets use `USER_ID:FIELD_POSITION`.
Pass no positions to attack or block to decline combat. Hands are returned as private ephemeral, numbered image pages and are never included in the public table. If artwork or rendering is unavailable, the same interaction falls back to private text.

## Supported rules

Opening hands and London-style mulligans, a randomized starting player, 20 life, five broad turn phases, one land per turn, automatic land tapping, creatures and summoning sickness, haste on Raging Goblin, a response stack with priority passing before phase changes and combat damage, combat, graveyards, empty-library loss, concessions, and persistent recovery.

This is a learning prototype. The supported-card list in `cards.py` is authoritative.

## Card data and artwork

Use `[p]mtg catalog [query]` to browse supported cards and `[p]mtg card <key or name>` for a detailed card view. Catalog entries store stable Scryfall printing and Oracle identifiers separately from active matches.

Card metadata and images are provided at runtime by [Scryfall](https://scryfall.com). Requests identify this project, use HTTPS and explicit Accept headers, and remain below Scryfall's published API ceiling. Images are decoded and size-checked before entering a private least-recently-used cache limited to 192 files and 128 MiB. Card images are not committed to this repository.

MTG is unofficial fan content permitted under the Wizards of the Coast Fan Content Policy. It is not approved or endorsed by Wizards. Portions of the referenced card materials are property of Wizards of the Coast LLC. This free prototype does not sell cards, packs, access, or generated proxy files.

The bundled playmat is an original generated background documented in `assets/README.md`; it contains no card faces, logos, trademarks, or game text.

Matches record successful player actions with sequence numbers and activity timestamps. State and persistent controls recover after cog reloads, while matches inactive for seven days end automatically so players can start new games.
