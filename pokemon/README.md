# Pokemon

Persistent cross-guild Pokémon catching and wild battles for Red.

Player commands use `[p]pokemon`, with `[p]poke` and `[p]pkmn` available as shorter aliases. Setup commands use `[p]pokemonset`, with `[p]pokeset` and `[p]pkmnset` as aliases.

## Included

- One global collection per Discord user across every guild served by the same bot, shown as an alphabetical nine-Pokémon grid with private add/replace party controls.
- Level-1 Bulbasaur, Charmander, or Squirtle starter with a generated laboratory carousel and personalized received-Pokémon reveal; immutable owned-Pokémon IDs; six-member parties; ten 30-slot boxes; Pokédex tracking; and profiles.
- Opt-in spawn channels with randomized thresholds, cooldowns, repeat suppression, owner-bounded generation filters, approachable rarity tiers, 15-minute wild encounter expiry, and administrator recovery.
- All 151 Generation 1 species bundled from validated PokéAPI records, including base experience and growth rates, plus owner-triggered cache sync for later generations.
- A generated gold six-slot horizontal party lineup with no backend ownership IDs exposed.
- Generation-aware battles persist a `standard` mechanics ruleset separately from Kanto campaign content, allowing later regions without rewriting owned Pokémon or active battles.
- Persistent level-scaled wild battles with classic Fight/Pokémon/Bag/Run menus, all 147 Red/Blue/Yellow level-up moves used by the original 151 species, authentic four-move progression, PP, accuracy, physical/special damage, priority/level-scaled Speed ordering, ruleset-versioned type effectiveness, same-type attack bonuses, modern critical-hit stages and stat-stage handling, level/IV-scaled combat stats for owned and wild Pokémon, critical hits, classic effectiveness/immunity/status/progression narration, persistent standard-ruleset sleep and confusion durations, type-based status immunities, burn Attack reduction, deterministic thaw/confusion behavior, supported status/stat/healing/fixed-damage/multi-hit/drain/recoil effects, switching, fainting, deterministic action history/recovery, XP, generated evolution and move-learning reveal cards, restart-safe four-move replacement choices, species base-XP rewards, authentic growth curves, conscious-participant XP sharing, and every Gen 1 level-based evolution.
- Generation-aware HP, species catch-rate, status, and ball-modifier calculations with Poké, Great, and Ultra Ball inventory and restart-safe, idempotent settlement.
- An explicit support audit classifies all 147 bundled moves as accurate, deliberately standardized, or deferred; order-sensitive flinching and Haze stat resets are supported.
- A validated catalog-version manifest independently versions typing, stats, learnsets, evolution methods, abilities, items, weather, and terrain; battles and owned Pokémon preserve their content provenance.
- Centered encounter art with in-image name, sex, level, and HP plus generated Game Boy-inspired battle scenes, bottom-box catch messages, battle-relative defeat and escape scenes, bounded sprite/render caches, and accessible embed text.
- An interactive Pokédex with paginated lists, direct entry selection, search, filters, generation selection, progressive seen/caught detail unlocks, and selectable Retro or Compact presentation.
- Ordered Kanto Gym Leader challenges, persistent badges, and a trainer-profile badge case without introducing a separate trainer XP level.
- Persistent party HP/status, server-configured Pokémon Center channels, and atomic Potion/Revive recovery.
- Red user-data deletion releases affected encounters.

Collections are global only within one bot installation. Separate bots do not share data.

## Setup

1. Load the cog.
2. Grant Embed Links and Attach Files in spawn channels.
3. Enable a spawn channel with `[p]pokemonset channel #channel` and designate any existing healing channel with `[p]pokemonset center #chat`.
4. Review settings with `[p]pokemonset settings` (or `status`). Timed spawning defaults to one encounter per server every 60 minutes in a randomly selected configured channel. Use `[p]pokemonset timer <minutes>` to make it slower.
5. Members choose `[p]pokemon starter bulbasaur|charmander|squirtle`.
6. Timed encounters bring members back without requiring existing conversation. The earlier activity system remains available with `[p]pokemonset mode activity`; restore timers with `[p]pokemonset mode timed`.
7. Review the next Kanto Gym with `[p]pokemon gym` and challenge it with `[p]pokemon gym challenge`.

Player commands include `[p]pokemon collection`, `pokedex`, `pokedexstyle`, `gym`, `gym challenge`, `party`, `party add`, `party remove`, `moves`, `profile [@member]`, `profilestyle retro|gold`, `bag`, `use potion <slot-or-name>`, `use revive <slot-or-name>`, and `center`. Defeating a wild Pokémon awards its species/level reward across conscious Pokémon that participated; catching awards half that ruleset-controlled reward and adds the encountered Pokémon at its wild level. Battle Bag offers Poké, Great, and Ultra Balls with progressively stronger catch modifiers. When a Pokémon already knows four moves, its next species-authentic level-up move pauses for the trainer to choose a move to forget or give up learning it; use `[p]pokemon moves` to reopen an interrupted choice. The first successful catch of each species displays a dedicated Pokédex registration card; repeat catches skip it. The bundled Generation 1 roster works immediately. The bot owner can populate a later-generation cache with `[p]pokemonset catalogsync <1-9>`; sync selects level-up learnsets from that generation’s game versions and labels the resulting cache with its catalog release.


## Encounter fairness and control

Timed spawning is the default: each enabled server receives at most one scheduled encounter per hour, posted to one randomly selected configured spawn channel. Server administrators may increase the interval as far as one week but cannot reduce it below 60 minutes. The schedule is persisted across restarts, pauses while another encounter is active, and is visible through `[p]pokemonset settings`. The optional activity mode retains its bounded meaningful-message thresholds and cooldowns for servers that prefer it. Ordinary wild levels follow the strongest Pokémon owned by trainers active in that server during the previous five minutes, using a weighted range from two levels below through four above and a level-30 ceiling; without recent trainers they begin near level 1. Species rarity remains independent of level. Wild encounters remain open for 15 minutes by default. The bot owner controls that lifetime, the friendly/standard/challenging rarity curve, available generations, activity-mode floors, and whether legendary or mythical species may enter ordinary spawns. Special species are event-only by default. Manual server spawns obey the selected mode’s rate limit; the bot owner retains a testing bypass.

Owner controls are `[p]pokemonset globalstatus`, `[p]pokemonset encountertime <minutes>`, `[p]pokemonset globallimits <activity> <seconds>`, `[p]pokemonset globalgenerations <1-9...>`, `[p]pokemonset rarity friendly|standard|challenging`, and `[p]pokemonset specials true|false`. Server battle duration remains adjustable through `[p]pokemonset battleexpiry <minutes>`; wild lifetime is not server-configurable. Bot owners can reset a development-test profile with `[p]pokemonset resetplayer @user confirm`; this clears that user's complete Pokémon progress only.

## Healing and persistent health

Battle HP and primary status—including remaining sleep turns—persist on owned Pokémon; confusion is volatile and clears on switching. Use `[p]pokemon center` from the server's configured Pokémon Center channel for free full-party HP, status, PP, and fainting recovery; Center use has a five-minute per-user cooldown. Outside a Center, `[p]pokemon use potion <slot-or-name>` restores up to 20 HP to a conscious Pokémon and `[p]pokemon use revive <slot-or-name>` returns a fainted Pokémon at half HP. New trainers receive ten Poké Balls, three Great Balls, one Ultra Ball, five Potions, and two Revives; the versioned migration adds the new ball tiers without replacing existing inventory. Item consumption and health updates are stored together. The unrestricted development `heal` command has been removed.

## Trainer profile and Kanto Gyms

`[p]pokemon profile [@member]` displays your or another server member's generated trainer card, badge case, Pokédex progress, collection, bag, partner level/XP, and next Gym. Trainers challenge Brock through Giovanni in order. Members choose their own Retro or Gold card with `[p]pokemon profilestyle retro|gold`; viewers see the trainer's saved style. Each first victory awards that leader's badge exactly once; rematches are not required for progression. Gym battles award ordinary Pokémon battle XP, cannot award or consume a caught Gym Pokémon, and occupy the server's encounter slot until victory, defeat, forfeit, or expiry.

The initial Gym milestone uses each leader's signature Pokémon as a restart-safe ace challenge. Full leader teams are deliberately deferred until opponent-party support is added to the battle engine; badge storage and ordering do not need to change for that expansion.

## Pokédex styles

The Retro style is the initial bot default. The bot owner can change that with `[p]pokemonset pokedexstyle retro|compact`. Members can select their own style with `[p]pokemon pokedexstyle retro|compact` or return to the owner-selected default with `[p]pokemon pokedexstyle default`.

Presentation is intentionally modular: a style implements list and detail embed rendering and is registered in `POKEDEX_STYLES`. Navigation, filtering, search, generation selection, ownership checks, and unseen/seen/caught disclosure rules remain shared. Community themes must preserve those disclosure rules, remain readable on mobile, avoid proprietary assets, and retain usable embed text independent of decorative imagery. Unknown or removed style keys safely fall back to Retro.

## Boundaries

This remains a deliberately bounded playable milestone. PvP, trading, active ability/EV/gender battle mechanics, TM/HM/tutor move acquisition, stone and trade evolutions, biome/weather tables, and cross-bot ownership are deferred. The RBY level-up catalog is complete, and `move_audit.json` records the support classification and rationale for every bundled move. Deferred mechanics such as Transform, Metronome, Counter, field screens, trapping, and multi-turn charging remain explicit until each receives dedicated state and tests. Owned records already preserve ability, EV, gender, nature, origin, and catch provenance for future mechanics.

This is an unofficial, noncommercial fan project. It uses replaceable PokéAPI data/sprite providers; operators are responsible for reviewing provider terms and Pokémon-related intellectual-property requirements before public distribution.
