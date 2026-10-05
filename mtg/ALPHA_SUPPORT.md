# Alpha support roadmap

The bundled Limited Edition Alpha catalog contains 295 printings representing 290 distinct card names. Every printing has a stable `support_family` and `engine_status` in `data/alpha.json`. A card becomes `playable` only after its complete current Oracle behavior uses validated engine actions and has focused tests. The fixed red and green starter decks and the 60-card future pack pool remain unchanged until collection and deck-choice design is approved.

## Current inventory

| Support family | Printings | Current direction |
| --- | ---: | --- |
| Vanilla creatures | 15 | All playable since 0.7.0 |
| Creatures with abilities | 77 | 12 static-keyword creatures playable in 0.8.0; 65 remain |
| Instants and sorceries | 70 | Targets, zones, countering, prevention, X costs, and special resolutions |
| Enchantments | 68 | Auras, continuous effects, triggers, and replacement effects |
| Artifacts | 42 | Artifact permanents, mana abilities, activation costs, and continuous effects |
| Lands | 19 | Basic and nonbasic mana production, land types, and continuous effects |
| Ante cards | 3 | Explicitly excluded from literal play |
| Dexterity adaptation | 1 | Chaos Orb requires a deterministic digital design before promotion |

The 15 vanilla creatures are Pearled Unicorn, Savannah Lions, Merfolk of the Pearl Trident, Water Elemental, Scathe Zombies, Earth Elemental, Fire Elemental, Gray Ogre, Hill Giant, Hurloon Minotaur, Mons’s Goblin Raiders, Craw Wurm, Grizzly Bears, Ironroot Treefolk, and Obsianus Golem.

The 0.8.0 static-keyword promotion adds Air Elemental, Mahamoti Djinn, Phantom Monster, Roc of Kher Ridges, Scryb Sprites, Giant Spider, Serra Angel, Wall of Swords, Wall of Air, Wall of Stone, Wall of Ice, and Wall of Wood. Their supported behavior is deliberately narrow and complete: flying block legality, reach blocking, vigilance attack tapping, and defender attack restrictions. First strike and trample remain reference-only until their combat damage steps, intervening priority, multiple-blocker assignment, and Discord choices are implemented together.

## Delivery stages

1. **Mana and vanilla creatures:** colored and generic costs, automatic legal land selection, all 15 textless Alpha creatures, catalog status, persistence, and solo-AI compatibility.
2. **Creature combat abilities (in progress):** flying/reach interaction, vigilance, and defender restrictions are complete. First strike, trample, landwalk, protection, regeneration, and creature-specific static rules remain.
3. **Spell and zone actions:** damage variants, destroy/sacrifice, prevention, counterspells, discard, graveyard return, bounce, board-wide effects, and target legality.
4. **Artifacts and activated abilities:** artifact permanents, mana pools, chosen colors, tap/sacrifice costs, reusable activations, and activation controls.
5. **Enchantments and continuous rules:** Auras, layer-aware stat changes, global effects, triggered abilities, replacement effects, and state-based cleanup.
6. **Complex Alpha behavior:** X costs, copying, control changes, extra turns, delayed effects, face-down state, card-specific choices, and remaining manual exceptions.
7. **Completion audit:** per-card behavior matrix, AI safety, persistence round trips, Discord component limits, complete-match simulations, and hands-on test matches.

## Explicit exceptions

- **Contract from Below, Darkpact, and Demonic Attorney:** ante is not implemented. The cog will never transfer actual collection ownership through ante. A future non-ownership digital substitute requires an explicit product decision and must be labeled as adapted behavior.
- **Chaos Orb:** physical card flipping is unsuitable for Discord. It remains reference-only until a deterministic, disclosed digital targeting adaptation is approved and tested.
- Future paper-only mechanics such as subgames are excluded by the same policy even though Alpha itself contains no subgame card.

## Promotion gate

A printing may change to `playable` only when mana and timing are correct, every target and choice is represented, resolution and state-based effects are complete, persistence survives reloads, the solo AI cannot bypass hidden information or legality, and focused plus full-suite tests pass on SGBTestAgent. Merely loading card text or artwork is not playability.
