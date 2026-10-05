# YuGiOh

YuGiOh is an experimental persistent two-player duel cog. Version 0.1.0 is a deliberately bounded legacy-style subset: fixed 40-card beginner decks, 8,000 Life Points, five-card opening hands, no first-turn draw, no external tournament ban list (the fixed catalog is the legality list), the six normal phases, tribute summons, battle positions, basic combat, three Normal Spells, and Sakuretsu Armor as a simple attack-response Trap.

Start with [p]yugioh challenge @member. Private hands are available through the View hand button. Use [p]yugioh rules and [p]yugioh catalog for the authoritative supported surface. Unknown effects and mechanics are rejected rather than approximated.

The duel engine is independent of Discord callbacks, state is persisted after each accepted action, mutations are serialized per duel, controls carry a state version, random opening order is stored, and inactive duels expire after seven days. The public renderer never receives private hand identities. Face-down cards use a single generic asset.

Card metadata and images are supplied at runtime by YGOPRODeck. Each image is downloaded once into a bounded private cache and validated before use; official card artwork is not committed here. Yu-Gi-Oh and related card content are owned by their respective rights holders. This unofficial noncommercial prototype is not endorsed by Konami.

## Commands

- challenge, status, hand, rules, catalog
- next
- summon HAND ZONE POSITION [TRIBUTE_ZONES...]
- setmonster HAND ZONE [TRIBUTE_ZONES...]
- set HAND ZONE
- activate HAND
- flip ZONE
- position ZONE
- attack ATTACKER_ZONE [TARGET_ZONE]
- trap SPELL_ZONE, resolve, concede

Extra Deck mechanics, constructed decks, collections, trading, packs, ranking, and a complete chain engine are intentionally deferred.
