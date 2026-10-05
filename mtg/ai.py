from .engine import Game, GameError


DIFFICULTIES = ("easy", "normal")


def _target(game, user, card):
    if card.effect in ("damage","damage_any"):
        return str(game.opponent(user))
    if card.effect == "draw_target":
        return str(user)
    if card.effect=="return_creature_hand":
        creatures=[(position,permanent) for position,permanent in enumerate(game.player(game.opponent(user)).battlefield,1) if game.card(permanent.uid).creature]
        if not creatures: return None
        position,_=max(creatures,key=lambda item:sum(game.current_stats(item[1])))
        return f"{game.opponent(user)}:{position}"
    if card.effect in ("return_grave_creature_hand","return_grave_card_hand","reanimate_creature"):
        choices=[(position,uid) for position,uid in enumerate(game.player(user).graveyard,1) if card.effect=="return_grave_card_hand" or game.card(uid).creature]
        if not choices: return None
        position,_=max(choices,key=lambda item:(sum(game.projected_stats(user,game.card(item[1]))),game.card(item[1]).cost))
        return f"G:{position}"
    if card.effect in ("destroy_creature","exile_creature_life"):
        creatures=[]
        for position,permanent in enumerate(game.player(game.opponent(user)).battlefield,1):
            target=game.card(permanent.uid)
            if not target.creature: continue
            if card.target_nonartifact and "Artifact" in target.type_line: continue
            if card.target_nonblack and "B" in target.colors: continue
            creatures.append((position,permanent))
        if not creatures: return None
        position,_=max(creatures,key=lambda item:sum(game.current_stats(item[1])))
        return f"{game.opponent(user)}:{position}"
    if card.effect == "destroy_permanent":
        targets=[(position,permanent) for position,permanent in enumerate(game.player(game.opponent(user)).battlefield,1) if game.card(permanent.uid).kind in card.target_types]
        if not targets: return None
        position,_=max(targets,key=lambda item:(bool(game.card(item[1].uid).produces),game.card(item[1].uid).cost))
        return f"{game.opponent(user)}:{position}"
    if card.effect == "destroy_land":
        lands=[(position,permanent) for position,permanent in enumerate(game.player(game.opponent(user)).battlefield,1) if game.card(permanent.uid).land]
        if not lands: return None
        position,_=max(lands,key=lambda item:len(game.card(item[1].uid).produces))
        return f"{game.opponent(user)}:{position}"
    if card.effect in ("pump","pump_blocking"):
        creatures = [
            (position, permanent)
            for position, permanent in enumerate(game.player(user).battlefield, 1)
            if game.card(permanent.uid).creature
        ]
        if card.effect == "pump_blocking":
            blocking=set(game.blocks.values()); creatures=[item for item in creatures if item[1].uid in blocking]
        if not creatures: return None
        position, _ = max(creatures, key=lambda item: game.current_stats(item[1])[0])
        return f"{user}:{position}"
    return None


def _activate_helpful_mana(game, user):
    player=game.player(user)
    candidates=[]
    for uid in player.hand:
        card=game.card(uid)
        if card.land or game.can_pay(user,card): continue
        if card.kind != "Instant" and (game.active_user != user or game.phase not in ("precombat_main","postcombat_main") or game.stack): continue
        target=_target(game,user,card)
        if card.effect in ("pump","pump_blocking","destroy_land","destroy_permanent","destroy_creature","exile_creature_life","return_creature_hand","return_grave_creature_hand","return_grave_card_hand","reanimate_creature") and target is None: continue
        candidates.append(card)
    for position,permanent in enumerate(list(player.battlefield),1):
        source=game.card(permanent.uid)
        if permanent.tapped or not source.produces or (source.mana_amount==1 and not source.sacrifice_for_mana): continue
        for symbol in source.produces:
            player.mana_pool[symbol]=player.mana_pool.get(symbol,0)+source.mana_amount
            enabled=any(game.can_pay(user,card) for card in candidates)
            player.mana_pool[symbol]-=source.mana_amount
            if not player.mana_pool[symbol]: player.mana_pool.pop(symbol)
            if enabled:
                game.activate_mana(user,position,symbol)
                return "mana"
    return None


def _play_one(game, user, difficulty):
    player = game.player(user)
    if game.active_user == user and game.phase in ("precombat_main", "postcombat_main") and not game.stack and not player.land_played:
        for position, uid in enumerate(player.hand, 1):
            if game.card(uid).land:
                game.play(user, position)
                return "play_land"

    mana_action=_activate_helpful_mana(game,user)
    if mana_action: return mana_action

    candidates = []
    for position, uid in enumerate(player.hand, 1):
        card = game.card(uid)
        if card.land or not game.can_pay(user, card):
            continue
        if card.kind != "Instant" and (game.active_user != user or game.phase not in ("precombat_main", "postcombat_main") or game.stack):
            continue
        target = _target(game, user, card)
        if card.effect in ("pump","pump_blocking","destroy_land","destroy_permanent","destroy_creature","exile_creature_life","return_creature_hand","return_grave_creature_hand","return_grave_card_hand","reanimate_creature") and target is None:
            continue
        score = 0
        if card.creature:
            score = sum(game.projected_stats(user,card))
        elif card.kind == "Artifact" and card.produces:
            score = 5
        elif card.effect in ("damage","damage_any"):
            score = 12 + card.amount - card.self_damage
        elif card.effect in ("draw","draw_target"):
            score = 10 + card.amount
        elif card.effect in ("pump","pump_blocking"):
            score = 7 + card.amount
        elif card.effect == "life":
            score = 4 + card.amount
        elif card.effect == "destroy_land":
            score = 11
        elif card.effect == "destroy_permanent":
            score = 10
        elif card.effect in ("destroy_creature","exile_creature_life"):
            score = 12
        elif card.effect=="return_creature_hand":
            score=9
        elif card.effect in ("return_grave_creature_hand","return_grave_card_hand"):
            score=8
        elif card.effect=="reanimate_creature":
            score=13
        elif card.effect == "destroy_all_creatures":
            enemy=sum(game.card(permanent.uid).creature for permanent in game.player(game.opponent(user)).battlefield)
            own=sum(game.card(permanent.uid).creature for permanent in player.battlefield)
            score=6+3*enemy-2*own
        elif card.effect in ("destroy_all_lands","destroy_land_type"):
            enemy=sum(1 for permanent in game.player(game.opponent(user)).battlefield if game.card(permanent.uid).land and (card.effect=="destroy_all_lands" or game.card(permanent.uid).has_land_type(card.land_type)))
            own=sum(1 for permanent in player.battlefield if game.card(permanent.uid).land and (card.effect=="destroy_all_lands" or game.card(permanent.uid).has_land_type(card.land_type)))
            score=6+2*enemy-2*own
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
        if card.creature and "defender" not in card.keywords and not permanent.tapped and (not permanent.sick or card.haste):
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
        enumerate(game.attackers, 1), key=lambda item: game.current_stats(next(x for x in game.player(game.active_user).battlefield if x.uid==item[1]))[0], reverse=True
    ):
        if not blockers:
            break
        attacker_perm=next(x for x in game.player(game.active_user).battlefield if x.uid==attacker_uid)
        legal = [item for item in blockers if game.can_block(attacker_uid,item[1].uid)[0]]
        if not legal:
            continue
        attacker_power = game.current_stats(attacker_perm)[0]
        survivable = [item for item in legal if game.current_stats(item[1])[1] > attacker_power]
        choice = min(survivable or legal, key=lambda item: sum(game.current_stats(item[1])))
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
