# MTG

MTG is an experimental two-player Magic: The Gathering rules prototype for Red.

Version 0.1.2 deliberately supports a small curated card pool and two fixed 60-card beginner decks. It is not a complete Magic implementation and does not yet include collections, packs, trading, Commander, Standard rotation, card artwork, or an economy.

## Play

1. Run `[p]mtg challenge @member`.
2. Both players privately inspect their hands with **View hand**.
3. Each player keeps or mulligans.
4. Use `[p]mtg play <hand-position> [target]`.
5. Use **Pass / next** or `[p]mtg pass` for priority and phase progression.
6. Use `[p]mtg attack <battlefield positions>`.
7. The defender uses `[p]mtg block <attacker-position:blocker-position>`.

Player targets use a Discord user ID. Permanent targets use `USER_ID:FIELD_POSITION`.
Pass no positions to attack or block to decline combat. Hands are returned ephemerally and never included in the public table.

## Supported rules

Opening hands and London-style mulligans, 20 life, five broad turn phases, one land per turn, automatic land tapping, creatures and summoning sickness, haste on Raging Goblin, a response stack with priority passing, combat, damage, graveyards, empty-library loss, concessions, and persistent recovery.

This is a learning prototype. The supported-card list in `cards.py` is authoritative.

Matches record successful player actions with sequence numbers and activity timestamps. State and persistent controls recover after cog reloads, while matches inactive for seven days end automatically so players can start new games.
