# MTG

MTG is an experimental solo and two-player Magic: The Gathering rules prototype for Red.

Version 0.6.0 supports a curated playable pool of 60 Scryfall-backed cards, the complete original Limited Edition Alpha reference catalog, and two fixed 60-card beginner decks. Alpha contributes 295 printings representing 290 distinct card names; its five basic lands each have two original artworks. Alpha cards are browseable but remain reference-only until their individual mechanics are implemented and tested. The cog is not a complete Magic implementation and does not yet include collections, packs, trading, Commander, Standard rotation, or an economy.

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

## Solo play

Run `[p]mtg solo [red|green] [easy|normal]` to play against the bot with the selected starter deck. `[p]mtg solo normal` is shorthand for a normal game using the red deck. Easy makes deterministic but intentionally incomplete attacks and blocks; normal evaluates its affordable plays and uses all legal attackers. The bot uses the same engine actions, mana, timing, targeting, priority, and combat rules as a human, and its decisions never inspect the human hand. Solo state and bot decisions recover after reloads. Solo games grant no cards, packs, currency, or other rewards.

## Supported rules

Opening hands and London-style mulligans, a randomized starting player, 20 life, five broad turn phases, one land per turn, automatic land tapping, creatures and summoning sickness, haste on Raging Goblin, a response stack with priority passing before phase changes and combat damage, combat, graveyards, empty-library loss, concessions, and persistent recovery.

This is a learning prototype. The versioned playable catalog in `data/cards.json` remains authoritative for matches, starter decks, and future pack pools. It contains five basics, thirty creatures, thirteen instants, and twelve sorceries. Every playable entry is limited to an engine-supported shape. The separate `data/alpha.json` catalog contains all 295 Limited Edition Alpha printings as historical reference records; those records cannot enter decks, matches, or pack pools. Pack generation and ownership remain deliberately unimplemented.

## Card data and artwork

Use `[p]mtg catalog [all|playable|alpha] [page|search]` to browse paged records. For example, `[p]mtg catalog alpha 2` opens the second Alpha page and `[p]mtg catalog alpha lotus` searches Alpha. Use `[p]mtg card <key or name>` for details; `[p]mtg card alpha Black Lotus` forces an Alpha lookup, and exact Alpha printing keys use `lea:NUMBER`. Every detail view labels whether a card is playable or reference-only. Catalog entries store stable Scryfall printing and Oracle identifiers separately from active matches.

Card metadata and images are provided at runtime by [Scryfall](https://scryfall.com). Requests identify this project, use HTTPS and explicit Accept headers, and remain below Scryfall's published API ceiling. Images are decoded and size-checked before entering a private least-recently-used cache limited to 192 files and 128 MiB. Card images are not committed to this repository.

The bundled Alpha metadata was retrieved from the Scryfall `set:lea` printing search. Maintainers can reproduce it with `python mtg/scripts/update_alpha_catalog.py`; the script validates the set count and unique printing identifiers, restricts pagination to Scryfall HTTPS URLs, and paces requests. No Alpha card artwork is bundled.

MTG is unofficial fan content permitted under the Wizards of the Coast Fan Content Policy. It is not approved or endorsed by Wizards. Portions of the referenced card materials are property of Wizards of the Coast LLC. This free prototype does not sell cards, packs, access, or generated proxy files.

The bundled playmat is an original generated background documented in `assets/README.md`; it contains no card faces, logos, trademarks, or game text.

Matches record successful player actions with sequence numbers and activity timestamps. State and persistent controls recover after cog reloads, while matches inactive for seven days end automatically so players can start new games.
