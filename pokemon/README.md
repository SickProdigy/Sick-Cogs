# Pokemon

Persistent cross-guild Pokémon catching and wild battles for Red.

## Included

- One global collection per Discord user across every guild served by the same bot.
- Bulbasaur, Charmander, or Squirtle starter; immutable owned-Pokémon IDs; six-member parties; paginated boxes and profiles.
- Opt-in spawn channels with randomized thresholds, cooldowns, repeat suppression, generation filters, rarity weighting, expiry, and administrator recovery.
- All 151 Generation 1 species bundled from validated PokéAPI records, plus owner-triggered cache sync for later generations.
- Persistent exclusive wild battles with PP, accuracy, priority/Speed ordering, type effectiveness, critical hits, supported status effects, switching, fainting, deterministic RNG recovery, experience, leveling, and supported evolutions.
- Poké Ball inventory and restart-safe, idempotent catch settlement.
- Modern encounter art plus a generated Game Boy-inspired battle scene with bounded sprite/render caches and accessible embed text.
- Red user-data deletion releases affected encounters.

Collections are global only within one bot installation. Separate bots do not share data.

## Setup

1. Load the cog.
2. Grant Embed Links and Attach Files in spawn channels.
3. Enable a channel with `[p]pokemon set channel #channel`.
4. Review settings with `[p]pokemon set status`; optionally configure threshold, cooldown, expiry, or generations.
5. Members choose `[p]pokemon starter bulbasaur|charmander|squirtle`.
6. Meaningful conversation triggers encounters, or an administrator can use `[p]pokemon set spawn` while testing.

Player commands include `[p]pokemon collection`, `party`, `party add`, `party remove`, `profile`, and `heal`. The bundled Generation 1 roster works immediately. The bot owner can populate a later-generation cache with `[p]pokemon set catalogsync <1-9>`.

## Boundaries

This remains a deliberately bounded first playable milestone. PvP, trading, boxes with hard capacity limits, abilities, EVs, gender mechanics, full move/category coverage, biome/weather tables, and cross-bot ownership are deferred.

This is an unofficial, noncommercial fan project. It uses replaceable PokéAPI data/sprite providers; operators are responsible for reviewing provider terms and Pokémon-related intellectual-property requirements before public distribution.
