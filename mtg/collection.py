from collections import Counter


def card_identity(card):
    """Stable functional identity shared by alternate printings."""
    return card.oracle_id or card.key


def resolve_deck(printing_counts, collection, cards):
    """Resolve preferred printings against ownership, substituting identical cards."""
    requested=Counter({str(key):int(count) for key,count in printing_counts.items() if int(count)>0})
    owned=Counter({str(key):int(count) for key,count in collection.items() if int(count)>0 and key in cards})
    requirements=Counter()
    for key,count in requested.items():
        if key not in cards: return [],[f"Unknown printing: {key}"]
        requirements[card_identity(cards[key])]+=count
    resolved=[]; errors=[]
    for identity,needed in requirements.items():
        preferred=[key for key in requested if key in cards and card_identity(cards[key])==identity]
        alternatives=sorted((key for key in owned if card_identity(cards[key])==identity and key not in preferred),key=lambda key:(cards[key].set_code!="lea",key))
        remaining=needed
        for key in preferred+alternatives:
            take=min(remaining,owned.get(key,0)); resolved.extend([key]*take); remaining-=take
            if not remaining: break
        if remaining:
            name=cards[preferred[0]].name if preferred else identity
            errors.append(f"{name}: need {needed}, own {needed-remaining}")
    if len(resolved)<60: errors.append(f"Deck has {len(resolved)} of the required 60 cards")
    for identity,count in requirements.items():
        sample=next(cards[key] for key in requested if card_identity(cards[key])==identity)
        if not sample.land and count>4: errors.append(f"{sample.name}: maximum 4 copies")
    return resolved,errors
