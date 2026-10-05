from .engine import Game, GameError


DIFFICULTIES = ("easy", "normal")


def _available_lands(game, user):
    return sum(1 for permanent in game.player(user).battlefield if game.card(permanent.uid).land and not permanent.tapped)


def _target(game, user, card):
    if card.effect == "damage":
        return str(game.opponent(user))
    if card.effect == "pump":
        creatures = [
            (position, permanent)
            for position, permanent in enumerate(game.player(user).battlefield, 1)
            if game.card(permanent.uid).creature
        ]
        if not creatures:
            return None
        position, _ = max(creatures, key=lambda item: game.card(item[1].uid).power + item[1].bonus)
        return f"{user}:{position}"
    return None


def _play_one(game, user, difficulty):
    player = game.player(user)
    if game.active_user == user and game.phase in ("precombat_main", "postcombat_main") and not game.stack and not player.land_played:
        for position, uid in enumerate(player.hand, 1):
            if game.card(uid).land:
                game.play(user, position)
                return "play_land"

    mana = _available_lands(game, user)
    candidates = []
    for position, uid in enumerate(player.hand, 1):
        card = game.card(uid)
        if card.land or card.cost > mana:
            continue
        if card.kind != "Instant" and (game.active_user != user or game.phase not in ("precombat_main", "postcombat_main") or game.stack):
            continue
        target = _target(game, user, card)
        if card.effect == "pump" and target is None:
            continue
        score = 0
        if card.creature:
            score = card.power + card.toughness
        elif card.effect == "damage":
            score = 12 + card.amount
        elif card.effect == "draw":
            score = 10 + card.amount
        elif card.effect == "pump":
            score = 7 + card.amount
        elif card.effect == "life":
            score = 4 + card.amount
        candidates.append((score, -position, position, target))
    if not candidates:
        return None
    chosen = candidates[0] if difficulty == "easy" else max(candidates)
    _, _, position, target = chosen
    game.play(user, position, target)
    return "cast"


def _attack_positions(game, user, difficulty):
    legal = []
    for position, permanent in enumerate(game.player(user).battlefield, 1):
        card = game.card(permanent.uid)
        if card.creature and not permanent.tapped and (not permanent.sick or card.haste):
            legal.append(position)
    if difficulty == "easy":
        return legal[::2]
    return legal


def _blocks(game, user, difficulty):
    blockers = [
        (position, permanent)
        for position, permanent in enumerate(game.player(user).battlefield, 1)
        if game.card(permanent.uid).creature and not permanent.tapped
    ]
    if difficulty == "easy":
        blockers = blockers[::2]
    assignments = {}
    for attacker_position, attacker_uid in sorted(
        enumerate(game.attackers, 1), key=lambda item: game.card(item[1]).power, reverse=True
    ):
        if not blockers:
            break
        attacker_power = game.card(attacker_uid).power
        survivable = [item for item in blockers if game.card(item[1].uid).toughness + item[1].bonus > attacker_power]
        choice = min(survivable or blockers, key=lambda item: game.card(item[1].uid).power + game.card(item[1].uid).toughness)
        assignments[attacker_position] = choice[0]
        blockers.remove(choice)
    return assignments


def advance_solo(game: Game):
    """Advance a persisted solo match until the human must act."""
    user = getattr(game, "ai_user", None)
    if user is None or game.finished:
        return False
    difficulty = getattr(game, "ai_difficulty", "easy")
    if difficulty not in DIFFICULTIES:
        difficulty = "easy"
    changed = False
    for _ in range(100):
        if game.finished:
            return changed
        if game.phase == "opening":
            if not game.player(user).kept:
                game.mulligan(user, True)
                game.record(user, "ai_keep")
                changed = True
                continue
            return changed
        if game.phase == "attackers":
            if game.active_user != user:
                return changed
            game.declare_attackers(user, _attack_positions(game, user, difficulty))
            game.record(user, "ai_attack")
            changed = True
            continue
        if game.phase == "blockers":
            if game.opponent(game.active_user) != user:
                return changed
            game.declare_blockers(user, _blocks(game, user, difficulty))
            game.record(user, "ai_block")
            changed = True
            continue
        if game.priority_user != user:
            return changed
        action = None
        if not game.stack:
            action = _play_one(game, user, difficulty)
        if action:
            game.record(user, f"ai_{action}")
        else:
            game.pass_priority(user)
            game.record(user, "ai_pass")
        changed = True
    raise GameError("Solo opponent exceeded its action limit.")
