# Alpha support roadmap

The bundled Limited Edition Alpha catalog contains 295 printings representing 290 distinct card names. Every printing has a stable `support_family` and `engine_status` in `data/alpha.json`. A card becomes `playable` only after its complete current Oracle behavior uses validated engine actions and has focused tests. The fixed red and green starter decks and the 60-card future pack pool remain unchanged until collection and deck-choice design is approved.

## Current inventory

| Support family | Printings | Current direction |
| --- | ---: | --- |
| Vanilla creatures | 15 | All playable since 0.7.0 |
| Creatures with abilities | 77 | 16 combat-ability creatures playable through 0.9.0; 61 remain |
| Instants and sorceries | 70 | 13 spells playable through 0.13.0; 57 remain |
| Enchantments | 68 | Auras, continuous effects, triggers, and replacement effects |
| Artifacts | 42 | Five Moxen playable in 0.13.0; 37 remain |
| Lands | 19 | All playable in 0.10.0 with land types and one-mana production |
| Ante cards | 3 | Explicitly excluded from literal play |
| Dexterity adaptation | 1 | Chaos Orb requires a deterministic digital design before promotion |

The 15 vanilla creatures are Pearled Unicorn, Savannah Lions, Merfolk of the Pearl Trident, Water Elemental, Scathe Zombies, Earth Elemental, Fire Elemental, Gray Ogre, Hill Giant, Hurloon Minotaur, Mons’s Goblin Raiders, Craw Wurm, Grizzly Bears, Ironroot Treefolk, and Obsianus Golem.

The 0.8.0 static-keyword promotion adds Air Elemental, Mahamoti Djinn, Phantom Monster, Roc of Kher Ridges, Scryb Sprites, Giant Spider, Serra Angel, Wall of Swords, Wall of Air, Wall of Stone, Wall of Ice, and Wall of Wood. Their supported behavior is deliberately narrow and complete: flying block legality, reach blocking, vigilance attack tapping, and defender attack restrictions. First strike was completed in 0.9.0 with a separate damage step and intervening priority. Trample remains reference-only until multiple-blocker damage assignment and its Discord choices are implemented together.

The 0.9.0 combat-timing promotion adds Elvish Archers, Bog Wraith, Shanodin Dryads, and Ironclaw Orcs. First strike uses a persisted first-strike damage step followed by a priority window before normal damage. Forestwalk and swampwalk inspect the defending battlefield’s land types, and Ironclaw Orcs checks the prospective attacker’s current power before blocking.

The 0.10.0 land promotion adds all 19 Alpha land printings: the nine basic-land artworks present in Alpha and all ten original dual lands except Volcanic Island, which was not printed in Limited Edition Alpha. Dual lands expose both basic land types and both mana choices. Automatic payment now searches possible colored assignments, so overlapping dual-land choices cannot falsely reject a payable cost.

The 0.11.0 targeted-spell promotion adds Lightning Bolt, Psionic Blast, Giant Growth, Righteousness, and Ancestral Recall. Targets are stored by stable player/permanent identity on the stack, checked before costs are paid, and rechecked for existence on resolution. Creature damage runs state-based death, temporary bonuses expire during cleanup, illegal vanished targets fizzle, Psionic Blast applies self-damage only when it resolves, and simultaneous zero-life loss is a draw.

The 0.12.0 mana-pool and land-destruction promotion adds Armageddon, Sinkhole, Flashfires, Stone Rain, Ice Storm, and Tsunami. Players may manually tap a land for a chosen color while they have priority; the public, persisted pool can pay later spells in that step and empties at the next step or phase boundary. Targeted destruction uses stable permanent identity and can fizzle, while mass effects inspect basic land types so Alpha duals interact correctly.

The 0.13.0 artifact-foundation promotion adds Mox Emerald, Mox Jet, Mox Pearl, Mox Ruby, Mox Sapphire, Shatter, and Disenchant. The Moxen cast for zero, resolve as persisted artifact permanents, can produce mana immediately, and participate in automatic or manual payment. Shatter and Disenchant validate permanent types before payment and again on resolution; Disenchant supports both artifact and enchantment targets. Black Lotus and Sol Ring remain reference-only until sacrifice costs and multi-mana activations are represented without losing surplus mana.

## Delivery stages

1. **Mana, lands, and vanilla creatures (complete):** colored and generic costs, backtracking-safe automatic land selection, all 19 Alpha lands, all 15 textless Alpha creatures, catalog status, persistence, and solo-AI compatibility.
2. **Creature combat abilities (in progress):** flying/reach interaction, vigilance, defender, first-strike timing, forestwalk/swampwalk, and Ironclaw Orcs’ block restriction are complete. Trample, other landwalk variants, protection, regeneration, and creature-specific rules remain.
3. **Spell and zone actions (in progress):** any-target damage, creature damage, self-damage, targeted draw, temporary creature pump, blocking-target restrictions, fizzle behavior, simultaneous zero-life draws, targeted/all/basic-type land destruction, artifact/enchantment destruction, and floating-mana responses are complete. Sacrifice, prevention, counterspells, discard, graveyard return, bounce, board-wide effects, modes, and X costs remain.
4. **Artifacts and activated abilities (in progress):** artifact permanents, one-mana tap abilities, mana pools, chosen colors, and artifact destruction are complete. Tap-plus-sacrifice costs, multi-mana production, reusable non-mana activations, and activation controls remain.
5. **Enchantments and continuous rules:** Auras, layer-aware stat changes, global effects, triggered abilities, replacement effects, and state-based cleanup.
6. **Complex Alpha behavior:** X costs, copying, control changes, extra turns, delayed effects, face-down state, card-specific choices, and remaining manual exceptions.
7. **Completion audit:** per-card behavior matrix, AI safety, persistence round trips, Discord component limits, complete-match simulations, and hands-on test matches.

## Explicit exceptions

- **Contract from Below, Darkpact, and Demonic Attorney:** ante is not implemented. The cog will never transfer actual collection ownership through ante. A future non-ownership digital substitute requires an explicit product decision and must be labeled as adapted behavior.
- **Chaos Orb:** physical card flipping is unsuitable for Discord. It remains reference-only until a deterministic, disclosed digital targeting adaptation is approved and tested.
- Future paper-only mechanics such as subgames are excluded by the same policy even though Alpha itself contains no subgame card.

## Promotion gate

A printing may change to `playable` only when mana and timing are correct, every target and choice is represented, resolution and state-based effects are complete, persistence survives reloads, the solo AI cannot bypass hidden information or legality, and focused plus full-suite tests pass on SGBTestAgent. Merely loading card text or artwork is not playability.
