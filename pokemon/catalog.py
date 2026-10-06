"""Bounded PokéAPI catalog cache with bundled fallback data."""

import asyncio
import json
from pathlib import Path

import aiohttp

from .data import MOVES, SPECIES, Species

API_ROOT = "https://pokeapi.co/api/v2"
USER_AGENT = "Sick-Cogs-Pokemon/0.16.5 (+https://gitea.rcs1.top/sickprodigy/Sick-Cogs)"
MAX_SPECIES = 1025


class CatalogError(RuntimeError):
    pass


class PokemonCatalog:
    def __init__(self, path: Path, bundled_path: Path = None):
        self.path = path
        self.bundled_path = bundled_path

    def load(self) -> int:
        loaded = 0
        for path in (self.bundled_path, self.path):
            if path is None or not path.exists():
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                parsed = [self.parse_cached(item) for item in raw.get("species", [])]
            except (OSError, ValueError, TypeError, KeyError) as exc:
                raise CatalogError("The Pokémon catalog cache is invalid.") from exc
            for item in parsed:
                previous=SPECIES.get(item.id)
                if previous and previous.learnset and not item.learnset:
                    learned=previous.learnset
                    moves=tuple(move for level,move in learned if level<=5)[-4:] or item.moves
                    item=Species(
                        item.id,item.name,item.types,item.hp,item.attack,item.defense,
                        item.speed,item.catch_rate,moves,learned,previous.abilities,
                        previous.gender_rate,previous.special_attack,
                        previous.special_defense,
                    )
                SPECIES[item.id] = item
            loaded = max(loaded, len(parsed))
        return loaded

    async def sync_generation(self, generation: int) -> int:
        if not 1 <= generation <= 9:
            raise CatalogError("Generation must be between 1 and 9.")
        timeout = aiohttp.ClientTimeout(total=30)
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            generation_data = await self._get(session, f"/generation/{generation}")
            names = sorted(
                str(item["name"])
                for item in generation_data.get("pokemon_species", [])
            )
            semaphore = asyncio.Semaphore(5)

            async def fetch(name):
                async with semaphore:
                    pokemon, species = await asyncio.gather(
                        self._get(session, f"/pokemon/{name}"),
                        self._get(session, f"/pokemon-species/{name}"),
                    )
                    return self.parse_api(pokemon, species)

            records = await asyncio.gather(*(fetch(name) for name in names))
        existing = {}
        if self.path.exists():
            try:
                existing = {
                    int(item["id"]): item
                    for item in json.loads(self.path.read_text(encoding="utf-8")).get(
                        "species", []
                    )
                }
            except (OSError, ValueError, TypeError, KeyError):
                existing = {}
        for record in records:
            existing[record.id] = self.to_cached(record)
            SPECIES[record.id] = record
        payload = {"schema": 1, "species": [existing[key] for key in sorted(existing)]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        temporary.replace(self.path)
        return len(records)

    async def _get(self, session, path):
        try:
            async with session.get(f"{API_ROOT}{path}") as response:
                if response.status != 200:
                    raise CatalogError(f"PokéAPI returned HTTP {response.status}.")
                return await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            raise CatalogError("PokéAPI request failed.") from exc

    @staticmethod
    def parse_api(pokemon: dict, species: dict) -> Species:
        species_id = int(pokemon["id"])
        if not 1 <= species_id <= MAX_SPECIES:
            raise CatalogError("PokéAPI returned an unsupported species ID.")
        stats = {
            str(item["stat"]["name"]): int(item["base_stat"])
            for item in pokemon["stats"]
        }
        types = tuple(
            str(item["type"]["name"])
            for item in sorted(pokemon["types"], key=lambda value: value["slot"])
        )
        learned = {}
        for item in pokemon["moves"]:
            key = str(item["move"]["name"]).replace("-", "_")
            if key not in MOVES:
                continue
            for detail in item.get("version_group_details", []):
                if (
                    detail.get("move_learn_method", {}).get("name") == "level-up"
                    and detail.get("version_group", {}).get("name") in {"red-blue", "yellow"}
                ):
                    level = int(detail.get("level_learned_at", 0))
                    learned[key] = min(level, learned.get(key, level))
        learnset = tuple(sorted((level, key) for key, level in learned.items()))
        moves = tuple(key for level, key in learnset if level <= 5)[-4:]
        if not moves:
            moves = ("tackle",)
        abilities = tuple(
            str(item["ability"]["name"]).replace("-", " ").title()
            for item in sorted(pokemon.get("abilities", []), key=lambda value: value["slot"])
            if not item.get("is_hidden")
        )
        display = str(species["name"]).replace("-", " ").title()
        return Species(
            species_id,
            display,
            types,
            stats["hp"],
            stats["attack"],
            stats["defense"],
            stats["speed"],
            int(species["capture_rate"]),
            moves,
            learnset,
            abilities,
            int(species.get("gender_rate", -1)),
            stats["special-attack"],
            stats["special-defense"],
        )

    @staticmethod
    def to_cached(item: Species) -> dict:
        return {
            "id": item.id,
            "name": item.name,
            "types": list(item.types),
            "hp": item.hp,
            "attack": item.attack,
            "defense": item.defense,
            "speed": item.speed,
            "catch_rate": item.catch_rate,
            "moves": list(item.moves),
            "learnset": [list(value) for value in item.learnset],
            "abilities": list(item.abilities),
            "gender_rate": item.gender_rate,
            "special_attack": item.special_attack,
            "special_defense": item.special_defense,
        }

    @staticmethod
    def parse_cached(raw: dict) -> Species:
        item = Species(
            int(raw["id"]),
            str(raw["name"])[:80],
            tuple(str(value) for value in raw["types"]),
            int(raw["hp"]),
            int(raw["attack"]),
            int(raw["defense"]),
            int(raw["speed"]),
            int(raw["catch_rate"]),
            tuple(str(value) for value in raw["moves"]),
            tuple((int(value[0]),str(value[1])) for value in raw.get("learnset",[])),
            tuple(str(value)[:80] for value in raw.get("abilities",[])),
            int(raw.get("gender_rate",-1)),
            int(raw.get("special_attack",raw["attack"])),
            int(raw.get("special_defense",raw["defense"])),
        )
        if not 1 <= item.id <= MAX_SPECIES or not item.types or not item.moves:
            raise CatalogError("Cached species data is out of bounds.")
        if any(move not in MOVES for move in item.moves) or any(move not in MOVES or not 0<=level<=100 for level,move in item.learnset):
            raise CatalogError("Cached species uses an unsupported move.")
        return item
