#!/usr/bin/env python3
"""Refresh the bundled Limited Edition Alpha reference catalog from Scryfall."""

import argparse
import datetime as dt
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

API_HOST = "api.scryfall.com"
QUERY = "https://api.scryfall.com/cards/search?q=set%3Alea&unique=prints&order=set"
SET_URI = "https://api.scryfall.com/sets/lea"
HEADERS = {
    "User-Agent": "Sick-Cogs-MTG/0.7 (+https://gitea.rcs1.top/sickprodigy/Sick-Cogs)",
    "Accept": "application/json;q=0.9,*/*;q=0.8",
}


def fetch(uri):
    parsed = urllib.parse.urlparse(uri)
    if parsed.scheme != "https" or parsed.hostname != API_HOST:
        raise RuntimeError("Refusing a non-Scryfall catalog URL.")
    request = urllib.request.Request(uri, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def build():
    set_data = fetch(SET_URI)
    time.sleep(0.12)
    page = fetch(QUERY)
    cards = list(page["data"])
    while page.get("has_more"):
        time.sleep(0.12)
        page = fetch(page["next_page"])
        cards.extend(page["data"])
    if set_data.get("code") != "lea" or len(cards) != set_data.get("card_count"):
        raise RuntimeError("Scryfall Alpha set metadata did not match the fetched records.")
    if len({card["id"] for card in cards}) != len(cards):
        raise RuntimeError("Scryfall returned duplicate Alpha printing identifiers.")
    records = []
    ante_cards={"Contract from Below","Darkpact","Demonic Attorney"}
    for card in cards:
        text=card.get("oracle_text") or ""
        type_line=card["type_line"]
        vanilla=type_line.startswith(("Creature", "Artifact Creature")) and not text
        if card["name"] in ante_cards: support_family="excluded_ante"
        elif card["name"]=="Chaos Orb": support_family="digital_adaptation_required"
        elif vanilla: support_family="vanilla_creature"
        elif type_line.startswith(("Basic Land","Land")): support_family="land"
        elif type_line.startswith(("Creature","Artifact Creature")): support_family="creature_ability"
        elif type_line.startswith("Artifact"): support_family="artifact"
        elif type_line.startswith("Enchantment"): support_family="enchantment"
        else: support_family="spell"
        records.append({
            "key": "lea:" + card["collector_number"],
            "name": card["name"],
            "scryfall_id": card["id"],
            "oracle_id": card["oracle_id"],
            "collector_number": card["collector_number"],
            "mana_cost": card.get("mana_cost") or "",
            "mana_value": card.get("cmc", 0),
            "type_line": card["type_line"],
            "colors": card.get("colors") or [],
            "color_identity": card.get("color_identity") or [],
            "rarity": card["rarity"],
            "oracle_text": card.get("oracle_text") or "",
            "power": card.get("power"),
            "toughness": card.get("toughness"),
            "layout": card["layout"],
            "support_family": support_family,
            "engine_status": (
                "playable"
                if card["type_line"].startswith(("Creature", "Artifact Creature"))
                and not (card.get("oracle_text") or "")
                else "reference_only"
            ),
        })
    return {
        "schema": 1,
        "source": "Scryfall card search API",
        "source_uri": QUERY,
        "retrieved": dt.date.today().isoformat(),
        "set": {
            "code": "lea",
            "name": set_data["name"],
            "released_at": set_data["released_at"],
            "printing_count": len(records),
            "distinct_card_count": len({record["oracle_id"] for record in records}),
        },
        "cards": records,
    }


def main():
    default = Path(__file__).parents[1] / "data" / "alpha.json"
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=default)
    args = parser.parse_args()
    payload = build()
    args.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Wrote {payload['set']['printing_count']} Alpha printings to {args.output}")


if __name__ == "__main__":
    main()
