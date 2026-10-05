# Pokemon

Persistent cross-guild Pokémon catching and wild battles for Red.

## Included

- One global collection per Discord user across every guild served by the same bot.
- Bulbasaur, Charmander, or Squirtle starter; immutable owned-Pokémon IDs; six-member parties; ten 30-slot boxes; Pokédex tracking; and profiles.
- Opt-in spawn channels with randomized thresholds, cooldowns, repeat suppression, generation filters, rarity weighting, expiry, and administrator recovery.
- All 151 Generation 1 species bundled from validated PokéAPI records, plus owner-triggered cache sync for later generations.
- Persistent level-scaled wild battles with classic Fight/Pokémon/Bag/Run menus, four move slots, PP, accuracy, physical/special damage, priority/Speed ordering, type effectiveness, critical hits, supported status effects, switching, fainting, deterministic action history/recovery, XP, supported move learning, and every Gen 1 level-based evolution.
- Poké Ball inventory and restart-safe, idempotent catch settlement.
- Modern encounter art plus a generated Game Boy-inspired battle scene with bounded sprite/render caches and accessible embed text.
- An interactive Pokédex with paginated lists, direct entry selection, search, filters, generation selection, progressive seen/caught detail unlocks, and selectable Retro or Compact presentation.
- Ordered Kanto Gym Leader challenges, persistent badges, and a trainer-profile badge case without introducing a separate trainer XP level.
- Persistent party HP/status, server-configured Pokémon Center channels, and atomic Potion/Revive recovery.
- Red user-data deletion releases affected encounters.

Collections are global only within one bot installation. Separate bots do not share data.

## Setup

1. Load the cog.
2. Grant Embed Links and Attach Files in spawn channels.
3. Enable a spawn channel with `[p]pokemon set channel #channel` and designate any existing healing channel with `[p]pokemon set center #chat`.
4. Review settings with `[p]pokemon set status`; choose `[p]pokemon set pace active|normal|relaxed`, or configure threshold, cooldown, expiry, and generations individually.
5. Members choose `[p]pokemon starter bulbasaur|charmander|squirtle`.
6. Meaningful conversation triggers encounters, or an administrator can use `[p]pokemon set spawn` while testing.
7. Review the next Kanto Gym with `[p]pokemon gym` and challenge it with `[p]pokemon gym challenge`.

Player commands include `[p]pokemon collection`, `pokedex`, `pokedexstyle`, `gym`, `gym challenge`, `party`, `party add`, `party remove`, `profile`, `bag`, `use potion <slot-or-id>`, `use revive <slot-or-id>`, and `center`. Defeating a wild Pokémon awards XP; catching it adds it to the collection but awards no battle XP. The bundled Generation 1 roster works immediately. The bot owner can populate a later-generation cache with `[p]pokemon set catalogsync <1-9>`.

## Healing and persistent health

Battle HP and supported status now persist on owned Pokémon. Use `[p]pokemon center` from the server's configured Pokémon Center channel for free full-party HP, status, PP, and fainting recovery; Center use has a five-minute per-user cooldown. Outside a Center, `[p]pokemon use potion <slot-or-id>` restores up to 20 HP to a conscious Pokémon and `[p]pokemon use revive <slot-or-id>` returns a fainted Pokémon at half HP. New trainers receive five Potions and two Revives. Item consumption and health updates are stored together. The unrestricted development `heal` command has been removed.

## Trainer profile and Kanto Gyms

`[p]pokemon profile` displays the trainer's badge case, Pokédex progress, collection, bag, partner level/XP, and next Gym. Trainers challenge Brock through Giovanni in order. Each first victory awards that leader's badge exactly once; rematches are not required for progression. Gym battles award ordinary Pokémon battle XP, cannot award or consume a caught Gym Pokémon, and occupy the server's encounter slot until victory, defeat, forfeit, or expiry.

The initial Gym milestone uses each leader's signature Pokémon as a restart-safe ace challenge. Full leader teams are deliberately deferred until opponent-party support is added to the battle engine; badge storage and ordering do not need to change for that expansion.

## Pokédex styles

The Retro style is the initial bot default. The bot owner can change that with `[p]pokemon set pokedexstyle retro|compact`. Members can select their own style with `[p]pokemon pokedexstyle retro|compact` or return to the owner-selected default with `[p]pokemon pokedexstyle default`. The style selector inside the interactive Pokédex also saves the member's choice.

Presentation is intentionally modular: a style implements list and detail embed rendering and is registered in `POKEDEX_STYLES`. Navigation, filtering, search, generation selection, ownership checks, and unseen/seen/caught disclosure rules remain shared. Community themes must preserve those disclosure rules, remain readable on mobile, avoid proprietary assets, and retain usable embed text independent of decorative imagery. Unknown or removed style keys safely fall back to Retro.

## Boundaries

This remains a deliberately bounded first playable milestone. PvP, trading, active ability/EV/gender battle mechanics, full move coverage, stone and trade evolutions, biome/weather tables, and cross-bot ownership are deferred. Owned records already preserve ability, EV, gender, nature, origin, and catch provenance for future mechanics.

This is an unofficial, noncommercial fan project. It uses replaceable PokéAPI data/sprite providers; operators are responsible for reviewing provider terms and Pokémon-related intellectual-property requirements before public distribution.
