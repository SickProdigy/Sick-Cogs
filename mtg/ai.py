from .engine import Game, GameError


DIFFICULTIES = ("easy", "normal")
def _can_target(game,card,permanent): return not game._protected_from(permanent,card)

TARGETED_EFFECTS = {"set_color","pump","pump_blocking","destroy_land","destroy_permanent","destroy_creature","exile_creature_life","return_creature_hand","return_grave_creature_hand","return_grave_card_hand","reanimate_creature","counter_spell","elemental_blast","draw_target_x","pump_power_x","damage_x_exile","life_target_x","regenerate_target","grant_keyword","tap_or_untap","destroy_wall"}


def _target(game, user, card):
    if card.effect=="set_color":
        for position,spell in enumerate(reversed(game.stack),1):
            if not spell.ability_effect and spell.owner!=user: return f"S:{position}"
        choices=[(game.card(permanent.uid).cost,position) for position,permanent in enumerate(game.player(game.opponent(user)).battlefield,1) if _can_target(game,card,permanent)]
        return f"{game.opponent(user)}:{max(choices)[1]}" if choices else None
    if card.aura_target_types:
        target_user=game.opponent(user) if card.aura_hostile else user
        choices=[]
        for position,permanent in enumerate(game.player(target_user).battlefield,1):
            target=game.card(permanent.uid)
            if game._aura_can_attach(card,permanent): choices.append((sum(game.current_stats(permanent)) if target.creature else target.cost,position))
        return f"{target_user}:{max(choices)[1]}" if choices else None
    if card.effect in ("counter_spell","elemental_blast"):
        for position,spell in enumerate(reversed(game.stack),1):
            target=game.card(spell.uid)
            if not spell.ability_effect and spell.owner!=user and (not card.target_color or card.target_color in game.spell_colors(spell)): return f"S:{position}"
        if card.effect=="elemental_blast":
            targets=[(position,permanent) for position,permanent in enumerate(game.player(game.opponent(user)).battlefield,1) if card.target_color in game.current_colors(permanent) and _can_target(game,card,permanent)]
            if targets:
                position,_=max(targets,key=lambda item:(game.card(item[1].uid).cost,sum(game.current_stats(item[1])) if game.card(item[1].uid).creature else 0))
                return f"{game.opponent(user)}:{position}"
        return None
    if card.effect in ("damage","damage_any","damage_x_exile"):
        return str(game.opponent(user))
    if card.effect in ("draw_target","draw_target_x","life_target_x"):
        return str(user)
    if card.effect=="return_creature_hand":
        creatures=[(position,permanent) for position,permanent in enumerate(game.player(game.opponent(user)).battlefield,1) if game.card(permanent.uid).creature and _can_target(game,card,permanent)]
        if not creatures: return None
        position,_=max(creatures,key=lambda item:sum(game.current_stats(item[1])))
        return f"{game.opponent(user)}:{position}"
    if card.effect in ("return_grave_creature_hand","return_grave_card_hand","reanimate_creature"):
        choices=[(position,uid) for position,uid in enumerate(game.player(user).graveyard,1) if card.effect=="return_grave_card_hand" or game.card(uid).creature]
        if not choices: return None
        position,_=max(choices,key=lambda item:(sum(game.projected_stats(user,game.card(item[1]))),game.card(item[1]).cost))
        return f"G:{position}"
    if card.effect=="regenerate_target":
        choices=[(game.card(permanent.uid).cost,position) for position,permanent in enumerate(game.player(user).battlefield,1) if game.card(permanent.uid).creature and _can_target(game,card,permanent) and not permanent.regeneration_shields and _regeneration_threatened(game,user,permanent)]
        return f"{user}:{max(choices)[1]}" if choices else None
    if card.effect=="grant_keyword":
        if game.active_user!=user or game.phase!="after_attackers": return None
        choices=[(game.current_stats(permanent)[0],position) for position,permanent in enumerate(game.player(user).battlefield,1) if permanent.uid in game.attackers and _can_target(game,card,permanent) and card.temporary_keyword not in game.current_keywords(permanent)]
        return f"{user}:{max(choices)[1]}" if choices else None
    if card.effect=="tap_or_untap":
        if game.phase=="after_attackers" and game.active_user!=user:
            choices=[(sum(game.current_stats(permanent)),position) for position,permanent in enumerate(game.player(user).battlefield,1) if game.card(permanent.uid).creature and permanent.tapped and _can_target(game,card,permanent)]
            return f"untap:{user}:{max(choices)[1]}" if choices else None
        if game.active_user!=user or game.phase not in ("precombat_main","after_attackers"): return None
        choices=[(sum(game.current_stats(permanent)) if game.card(permanent.uid).creature else game.card(permanent.uid).cost,position) for position,permanent in enumerate(game.player(game.opponent(user)).battlefield,1) if not permanent.tapped and any(game.card(permanent.uid).has_type(kind) for kind in card.target_types) and _can_target(game,card,permanent)]
        return f"tap:{game.opponent(user)}:{max(choices)[1]}" if choices else None
    if card.effect=="destroy_wall":
        choices=[(sum(game.current_stats(permanent)),position) for position,permanent in enumerate(game.player(game.opponent(user)).battlefield,1) if "Wall" in game.card(permanent.uid).type_line.split(" — ",1)[-1].split() and _can_target(game,card,permanent)]
        return f"{game.opponent(user)}:{max(choices)[1]}" if choices else None
    if card.effect in ("destroy_creature","exile_creature_life"):
        creatures=[]
        for position,permanent in enumerate(game.player(game.opponent(user)).battlefield,1):
            target=game.card(permanent.uid)
            if not target.creature or not _can_target(game,card,permanent): continue
            if card.target_nonartifact and "Artifact" in target.type_line: continue
            if card.target_nonblack and "B" in game.current_colors(permanent): continue
            creatures.append((position,permanent))
        if not creatures: return None
        position,_=max(creatures,key=lambda item:sum(game.current_stats(item[1])))
        return f"{game.opponent(user)}:{position}"
    if card.effect == "destroy_permanent":
        targets=[(position,permanent) for position,permanent in enumerate(game.player(game.opponent(user)).battlefield,1) if any(game.card(permanent.uid).has_type(kind) for kind in card.target_types) and _can_target(game,card,permanent)]
        if not targets: return None
        position,_=max(targets,key=lambda item:(bool(game.card(item[1].uid).produces),game.card(item[1].uid).cost))
        return f"{game.opponent(user)}:{position}"
    if card.effect == "destroy_land":
        lands=[(position,permanent) for position,permanent in enumerate(game.player(game.opponent(user)).battlefield,1) if game.card(permanent.uid).land and _can_target(game,card,permanent)]
        if not lands: return None
        position,_=max(lands,key=lambda item:len(game.card(item[1].uid).produces))
        return f"{game.opponent(user)}:{position}"
    if card.effect in ("pump","pump_blocking","pump_power_x"):
        creatures = [
            (position, permanent)
            for position, permanent in enumerate(game.player(user).battlefield, 1)
            if game.card(permanent.uid).creature and _can_target(game,card,permanent)
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
        if (card.effect in TARGETED_EFFECTS or card.aura_target_types) and target is None: continue
        candidates.append(card)
    for position,permanent in enumerate(list(player.battlefield),1):
        source=game.card(permanent.uid)
        if permanent.tapped or not source.produces or (source.mana_amount==1 and not source.mana_activation_cost and not source.sacrifice_for_mana): continue
        for symbol in source.produces:
            simulated=Game.from_raw(game.to_raw())
            try: simulated.activate_mana(user,position,symbol)
            except GameError: continue
            if any(simulated.can_pay(user,card) for card in candidates):
                game.activate_mana(user,position,symbol)
                return "mana"
    return None


def _global_enchantment_score(game,user,card):
    value=card.global_power+card.global_toughness
    def eligible(target_user,permanent):
        if not game.card(permanent.uid).creature: return False
        if card.global_controller_only and target_user!=user: return False
        if card.global_buff_color and card.global_buff_color not in game.current_colors(permanent): return False
        if card.global_requires_untapped and permanent.tapped: return False
        if card.global_requires_attacking and not game.can_attack_permanent(permanent): return False
        return True
    own=sum(eligible(user,permanent) for permanent in game.player(user).battlefield)
    enemy=0 if card.global_controller_only else sum(eligible(game.opponent(user),permanent) for permanent in game.player(game.opponent(user)).battlefield)
    return 5+value*(own-enemy)


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
        x_value=game.max_payable_x(user,card) if "{X}" in card.mana_cost else None
        if card.land or (x_value is not None and x_value<1) or not game.can_pay(user,card,x_value or 0):
            continue
        if card.kind != "Instant" and (game.active_user != user or game.phase not in ("precombat_main", "postcombat_main") or game.stack):
            continue
        target = _target(game, user, card)
        if card.effect in TARGETED_EFFECTS and target is None:
            continue
        score = 0
        if card.creature:
            score = sum(game.projected_stats(user,card))
        elif card.kind == "Artifact" and card.produces:
            score = 5
        elif card.global_power or card.global_toughness:
            score=_global_enchantment_score(game,user,card)
        elif card.mana_flare:
            own=sum(game.card(permanent.uid).land and not permanent.tapped for permanent in player.battlefield)
            enemy=sum(game.card(permanent.uid).land and not permanent.tapped for permanent in game.player(game.opponent(user)).battlefield)
            score=5+own-enemy
        elif card.land_tap_damage:
            own=sum(game.card(permanent.uid).land and not permanent.tapped for permanent in player.battlefield)
            enemy=sum(game.card(permanent.uid).land and not permanent.tapped for permanent in game.player(game.opponent(user)).battlefield)
            score=5+enemy-own
        elif card.aura_target_types:
            scaling=sum(game.card(permanent.uid).has_land_type("forest") for permanent in player.battlefield) if card.aura_forest_scaling else 0
            score=10 if card.aura_hostile else 7+card.aura_power+card.aura_toughness+scaling+2*bool(card.aura_keyword or card.aura_attack_override or card.aura_blocked_except_wall)
        elif card.effect in ("damage","damage_any"):
            score = 12 + card.amount - card.self_damage
        elif card.effect=="damage_x_exile":
            score=12+(x_value or 0)
        elif card.effect in ("draw","draw_target"):
            score = 10 + card.amount
        elif card.effect=="draw_target_x":
            score=10+(x_value or 0)
        elif card.effect in ("pump","pump_blocking"):
            score = 7 + card.amount
        elif card.effect=="pump_power_x":
            score=7+(x_value or 0)
        elif card.effect == "life":
            score = 4 + card.amount
        elif card.effect=="life_target_x":
            score=4+(x_value or 0)
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
        elif card.effect in ("counter_spell","elemental_blast"):
            score=15 if target and target.startswith("S:") else 11
        elif card.effect in ("regenerate_target","grant_keyword","tap_or_untap"):
            score=10
        elif card.effect=="set_color":
            score=4
        elif card.effect=="destroy_wall":
            score=11
        elif card.effect=="add_mana":
            score=6
        elif card.effect=="destroy_all_enchantments":
            enemy=sum(game.card(permanent.uid).has_type("Enchantment") for permanent in game.player(game.opponent(user)).battlefield)
            own=sum(game.card(permanent.uid).has_type("Enchantment") for permanent in player.battlefield)
            score=5+3*enemy-2*own
        elif card.effect == "destroy_all_creatures":
            enemy=sum(game.card(permanent.uid).creature for permanent in game.player(game.opponent(user)).battlefield)
            own=sum(game.card(permanent.uid).creature for permanent in player.battlefield)
            score=6+3*enemy-2*own
        elif card.effect in ("earthquake_x","hurricane_x"):
            score=8+(x_value or 0)
        elif card.effect in ("destroy_all_lands","destroy_land_type"):
            enemy=sum(1 for permanent in game.player(game.opponent(user)).battlefield if game.card(permanent.uid).land and (card.effect=="destroy_all_lands" or game.card(permanent.uid).has_land_type(card.land_type)))
            own=sum(1 for permanent in player.battlefield if game.card(permanent.uid).land and (card.effect=="destroy_all_lands" or game.card(permanent.uid).has_land_type(card.land_type)))
            score=6+2*enemy-2*own
        candidates.append((score, -position, position, target, x_value))
    if not candidates:
        return None
    chosen = candidates[0] if difficulty == "easy" else max(candidates)
    _, _, position, target, x_value = chosen
    game.play(user, position, target, x_value)
    return "cast"


def _attack_positions(game, user, difficulty):
    legal = []
    for position, permanent in enumerate(game.player(user).battlefield, 1):
        card = game.card(permanent.uid)
        if game.can_attack_permanent(permanent):
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


def _activation_target(game,user,card,source_uid=None):
    opponent=game.opponent(user)
    if card.activation_effect=="counter_color":
        for position,spell in enumerate(reversed(game.stack),1):
            if not spell.ability_effect and spell.owner!=user and card.target_color in game.spell_colors(spell): return f"S:{position}"
        return None
    if card.activation_effect=="draw_self": return str(user)
    if card.activation_effect=="damage_any":
        if card.activation_self_damage and game.player(user).life<=card.activation_self_damage: return None
        return str(opponent)
    candidates=[]
    for position,permanent in enumerate(game.player(opponent).battlefield,1):
        target=game.card(permanent.uid)
        if not _can_target(game,card,permanent): continue
        if card.activation_effect=="destroy_black_permanent" and "B" in game.current_colors(permanent): candidates.append((target.cost,position))
        elif card.activation_effect=="destroy_tapped_creature" and target.creature and permanent.tapped: candidates.append((sum(game.current_stats(permanent)),position))
        elif card.activation_effect=="destroy_wall" and "Wall" in target.type_line.split(" — ",1)[-1].split(): candidates.append((sum(game.current_stats(permanent)),position))
    if candidates: return f"{opponent}:{max(candidates)[1]}"
    if card.activation_effect=="unblockable" and game.active_user==user and game.phase in ("precombat_main","after_attackers"):
        attackers=set(game.attackers) if game.phase=="after_attackers" else None
        choices=[]
        for position,permanent in enumerate(game.player(user).battlefield,1):
            target=game.card(permanent.uid)
            can_attack=not permanent.tapped and (not permanent.sick or target.haste) and "defender" not in game.current_keywords(permanent)
            if permanent.uid!=source_uid and target.creature and _can_target(game,card,permanent) and can_attack and game.current_stats(permanent)[0]<=2 and "unblockable" not in game.current_keywords(permanent) and (attackers is None or permanent.uid in attackers):
                choices.append((game.current_stats(permanent)[0],position))
        if choices: return f"{user}:{max(choices)[1]}"
    if card.activation_effect=="tap_permanent":
        choices=[]
        for position,permanent in enumerate(game.player(opponent).battlefield,1):
            target=game.card(permanent.uid)
            if not permanent.tapped and any(target.has_type(kind) for kind in ("Artifact","Creature","Land")) and _can_target(game,card,permanent):
                score=(10+sum(game.current_stats(permanent))) if target.creature else target.cost
                choices.append((score,position))
        if choices: return f"{opponent}:{max(choices)[1]}"
    if card.activation_effect=="untap_land":
        for position,permanent in enumerate(game.player(user).battlefield,1):
            if game.card(permanent.uid).land and permanent.tapped and _can_target(game,card,permanent): return f"{user}:{position}"
    return None

def _regeneration_threatened(game,user,permanent):
    stable=f"{user}:{permanent.uid}"; toughness=game.current_stats(permanent)[1]-permanent.damage
    for item in game.stack:
        card=game.card(item.uid)
        if item.target!=stable or game._protected_from(permanent,card,game.ability_source_colors(item) if item.ability_effect else game.spell_colors(item)): continue
        if item.ability_effect in ("destroy_black_permanent","destroy_tapped_creature","destroy_wall"): return True
        if item.ability_effect=="damage_any" and card.activation_amount>=toughness: return True
        if not item.ability_effect and card.effect in ("destroy_permanent","elemental_blast"): return True
        if not item.ability_effect and card.effect in ("damage","damage_any") and card.amount>=toughness: return True
    for item in game.stack:
        card=game.card(item.uid)
        if item.ability_effect or card.effect not in ("earthquake_x","hurricane_x") or item.x_value<toughness or game._protected_from(permanent,card,game.spell_colors(item)): continue
        flying="flying" in game.current_keywords(permanent)
        if (card.effect=="earthquake_x" and not flying) or (card.effect=="hurricane_x" and flying): return True
    if game.phase not in ("after_blockers","after_first_strike"): return False
    if permanent.uid in game.attackers:
        blocker_uid=game.blocks.get(permanent.uid); opponent=game.player(game.opponent(user))
        blocker=next((x for x in opponent.battlefield if x.uid==blocker_uid),None)
        return blocker is not None and game.current_stats(blocker)[0]>=toughness
    for attacker_uid,blocker_uid in game.blocks.items():
        if blocker_uid==permanent.uid:
            attacker=next((x for x in game.player(game.active_user).battlefield if x.uid==attacker_uid),None)
            return attacker is not None and game.current_stats(attacker)[0]>=toughness
    return False

def _activation_beneficiary(game,source):
    card=game.card(source.uid)
    if not card.activation_attached: return source
    _,target=game.find_permanent(source.attached_to)
    return target

def _activate_regeneration(game,user):
    candidates=[]
    for position,permanent in enumerate(game.player(user).battlefield,1):
        card=game.card(permanent.uid); target=_activation_beneficiary(game,permanent)
        _,activation_effect,_,_=game._activation_profile(permanent)
        if activation_effect=="regenerate" and target is not None and not target.regeneration_shields and _regeneration_threatened(game,next(player.user_id for player in game.players.values() if target in player.battlefield),target) and game.can_activate(user,position):
            candidates.append((game.card(target.uid).cost,position))
    if not candidates: return None
    _,position=max(candidates); game.activate_ability(user,position); return "activate"

def _activate_targeted_ability(game,user):
    candidates=[]
    for position,permanent in enumerate(game.player(user).battlefield,1):
        card=game.card(permanent.uid)
        if not card.activation_effect: continue
        target=_activation_target(game,user,card,permanent.uid)
        if target is not None and game.can_activate(user,position,target): candidates.append((card.cost,position,target))
    if not candidates: return None
    _,position,target=max(candidates); game.activate_ability(user,position,target); return "activate"

def _activate_combat_pump(game,user):
    if game.phase not in ("after_attackers","after_blockers","after_first_strike"): return None
    combat=set(game.attackers if game.active_user==user else game.blocks.values())
    candidates=[]
    for position,permanent in enumerate(game.player(user).battlefield,1):
        card=game.card(permanent.uid); target=_activation_beneficiary(game,permanent)
        if target is None: continue
        keyword_helpful=card.activated_keyword and card.activated_keyword not in game.current_keywords(target) and game.active_user==user
        if target.uid in combat and (card.activated_power or card.activated_toughness or keyword_helpful) and game.can_activate(user,position):
            candidates.append((card.activated_power+card.activated_toughness+bool(keyword_helpful),sum(game.current_stats(target)),position))
    if not candidates: return None
    position=max(candidates)[2]; game.activate_ability(user,position); return "activate"

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
        action = _activate_regeneration(game,user) or _activate_targeted_ability(game,user) or _activate_combat_pump(game,user) or _play_one(game, user, difficulty)
        if action:
            game.record(user, f"ai_{action}")
        else:
            game.pass_priority(user)
            game.record(user, "ai_pass")
        changed = True
    raise GameError("Solo opponent exceeded its action limit.")
