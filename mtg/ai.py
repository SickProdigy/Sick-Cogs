from .engine import Game, GameError


DIFFICULTIES = ("easy", "normal")
def _can_target(game,card,permanent): return not game._protected_from(permanent,card)

TARGETED_EFFECTS = {"sacrifice_mana","simulacrum","guardian_angel","reverse_damage","healing_salve","mana_short","set_color","text_change_land","text_change_color","pump","pump_blocking","berserk","destroy_land","destroy_permanent","destroy_creature","exile_creature_life","return_creature_hand","return_grave_creature_hand","return_grave_card_hand","reanimate_creature","counter_spell","counter_mana_value_x","power_sink","elemental_blast","draw_target_x","discard_random_x","pump_power_x","damage_x_exile","drain_life_x","life_target_x","regenerate_target","grant_keyword","tap_or_untap","destroy_wall","blaze_of_glory","false_orders","fireball","volcanic_eruption"}


def _target(game, user, card):
    if card.effect=="blaze_of_glory":
        if game.phase!="after_attackers" or user==game.active_user: return None
        choices=[(sum(game.current_stats(permanent)),position) for position,permanent in enumerate(game.player(user).battlefield,1) if game.is_creature(permanent) and not permanent.tapped and _can_target(game,card,permanent)]
        return f"{user}:{max(choices)[1]}" if choices else None
    if card.effect=="false_orders":
        if game.phase!="after_blockers": return None
        defender=game.opponent(game.active_user); choices=[]
        for position,permanent in enumerate(game.player(defender).battlefield,1):
            if not game.is_creature(permanent) or not _can_target(game,card,permanent): continue
            blocked=game.attackers_for(permanent.uid)
            if user==game.active_user and not blocked: continue
            threat=max((game.current_stats(game.find_permanent(uid)[1])[0] for uid in blocked),default=0)
            choices.append((threat+sum(game.current_stats(permanent)),position))
        return f"{defender}:{max(choices)[1]}" if choices else None
    if card.aura_reanimate:
        choices=[]
        for target_user in (user,game.opponent(user)):
            for position,uid in enumerate(game.player(target_user).graveyard,1):
                if game.card(uid).creature: choices.append((sum(game.projected_stats(user,game.card(uid)))+game.card(uid).cost,target_user,position))
        if not choices: return None
        _,target_user,position=max(choices); return f"G:{position}" if target_user==user else f"{target_user}:G:{position}"
    if card.effect=="guardian_angel":
        if not game.player(user).damage_prevention and _player_damage_threatened(game,user): return str(user)
        choices=[(game.card(permanent.uid).cost+sum(game.current_stats(permanent)),position) for position,permanent in enumerate(game.player(user).battlefield,1) if game.is_creature(permanent) and not permanent.damage_prevention and _permanent_damage_threatened(game,user,permanent)]
        return f"{user}:{max(choices)[1]}" if choices else None
    if card.effect=="reverse_damage":
        damaging={"damage","damage_any","damage_x_exile","drain_life_x","earthquake_x","hurricane_x"}
        for position,item in enumerate(reversed(game.stack),1):
            source=game.card(item.uid); player_target=(item.target or "")==str(user) or source.effect in ("earthquake_x","hurricane_x")
            if not item.ability_effect and source.effect in damaging and player_target: return f"S:{position}"
        if game.active_user!=user and game.phase in ("after_blockers","after_first_strike"):
            attacker_player=game.player(game.active_user)
            for uid in game.attackers:
                permanent=game.find_permanent(uid)[1]
                if uid not in game.blocks and permanent is not None: return f"{game.active_user}:{attacker_player.battlefield.index(permanent)+1}"
        return None
    if card.effect=="healing_salve":
        if not game.player(user).damage_prevention and _player_damage_threatened(game,user): return f"prevent:{user}"
        choices=[(game.card(permanent.uid).cost+sum(game.current_stats(permanent)),position) for position,permanent in enumerate(game.player(user).battlefield,1) if game.is_creature(permanent) and not permanent.damage_prevention and _permanent_damage_threatened(game,user,permanent)]
        if choices: return f"prevent:{user}:{max(choices)[1]}"
        return f"life:{user}" if game.player(user).life<20 else None
    if card.effect in ("set_color","text_change_land","text_change_color"):
        for position,spell in enumerate(reversed(game.stack),1):
            if not spell.ability_effect and spell.owner!=user: return f"S:{position}"
        words=("plains","island","swamp","mountain","forest") if card.effect=="text_change_land" else ("white","blue","black","red","green")
        choices=[(game.card(permanent.uid).cost,position) for position,permanent in enumerate(game.player(game.opponent(user)).battlefield,1) if _can_target(game,card,permanent) and (card.effect=="set_color" or any(word in game.card(permanent.uid).text.casefold() for word in words))]
        return f"{game.opponent(user)}:{max(choices)[1]}" if choices else None
    if card.aura_target_types:
        target_user=game.opponent(user) if card.aura_hostile else user
        choices=[]
        for position,permanent in enumerate(game.player(target_user).battlefield,1):
            target=game.card(permanent.uid)
            if game._aura_can_attach(card,permanent) and (not card.aura_animate_mana_value or not game.is_creature(permanent)) and (not card.aura_enter_flying_damage or "flying" in game.current_keywords(permanent)): choices.append((sum(game.current_stats(permanent)) if game.is_creature(permanent) else target.cost,position))
        if not choices: return None
        stable=f"{target_user}:{max(choices)[1]}"
        return f"island:{stable}" if card.aura_choose_land_type else stable
    if card.effect in ("counter_spell","counter_mana_value_x","power_sink","elemental_blast"):
        for position,spell in enumerate(reversed(game.stack),1):
            target=game.card(spell.uid)
            if not spell.ability_effect and spell.owner!=user and (not card.target_color or card.target_color in game.spell_colors(spell)) and (card.effect!="counter_mana_value_x" or game.can_pay(user,card,game.spell_mana_value(spell))): return f"S:{position}"
        if card.effect=="elemental_blast":
            targets=[(position,permanent) for position,permanent in enumerate(game.player(game.opponent(user)).battlefield,1) if card.target_color in game.current_colors(permanent) and _can_target(game,card,permanent)]
            if targets:
                position,_=max(targets,key=lambda item:(game.card(item[1].uid).cost,sum(game.current_stats(item[1])) if game.is_creature(item[1]) else 0))
                return f"{game.opponent(user)}:{position}"
        return None
    if card.effect=="fireball": return str(game.opponent(user))
    if card.effect in ("damage","damage_any","damage_x_exile","drain_life_x"):
        return str(game.opponent(user))
    if card.effect in ("draw_target","draw_target_x","life_target_x"):
        return str(user)
    if card.effect=="discard_random_x":
        opponent=game.player(game.opponent(user)); return str(opponent.user_id) if opponent.hand else None
    if card.effect=="sacrifice_mana":
        choices=[(game.card(permanent.uid).cost+sum(game.current_stats(permanent)),position) for position,permanent in enumerate(game.player(user).battlefield,1) if game.is_creature(permanent)]
        return f"sacrifice:{min(choices)[1]}" if choices else None
    if card.effect=="simulacrum":
        amount=game.player(user).damage_taken_this_turn
        choices=[(game.current_stats(permanent)[1]-permanent.damage,position) for position,permanent in enumerate(game.player(user).battlefield,1) if game.is_creature(permanent)]
        safe=[item for item in choices if item[0]>amount]
        return f"{user}:{max(safe)[1]}" if amount and safe else None
    if card.effect in ("mana_short","drain_power"):
        opponent=game.player(game.opponent(user))
        return str(opponent.user_id) if opponent.mana_pool or any(game.card(permanent.uid).land and not permanent.tapped for permanent in opponent.battlefield) else None
    if card.effect=="return_creature_hand":
        creatures=[(position,permanent) for position,permanent in enumerate(game.player(game.opponent(user)).battlefield,1) if game.is_creature(permanent) and _can_target(game,card,permanent)]
        if not creatures: return None
        position,_=max(creatures,key=lambda item:sum(game.current_stats(item[1])))
        return f"{game.opponent(user)}:{position}"
    if card.effect in ("return_grave_creature_hand","return_grave_card_hand","reanimate_creature"):
        choices=[(position,uid) for position,uid in enumerate(game.player(user).graveyard,1) if card.effect=="return_grave_card_hand" or game.card(uid).creature]
        if not choices: return None
        position,_=max(choices,key=lambda item:(sum(game.projected_stats(user,game.card(item[1]))),game.card(item[1]).cost))
        return f"G:{position}"
    if card.effect=="regenerate_target":
        choices=[(game.card(permanent.uid).cost,position) for position,permanent in enumerate(game.player(user).battlefield,1) if game.is_creature(permanent) and _can_target(game,card,permanent) and not permanent.regeneration_shields and _regeneration_threatened(game,user,permanent)]
        return f"{user}:{max(choices)[1]}" if choices else None
    if card.effect=="grant_keyword":
        if game.active_user!=user or game.phase!="after_attackers": return None
        choices=[(game.current_stats(permanent)[0],position) for position,permanent in enumerate(game.player(user).battlefield,1) if permanent.uid in game.attackers and _can_target(game,card,permanent) and card.temporary_keyword not in game.current_keywords(permanent)]
        return f"{user}:{max(choices)[1]}" if choices else None
    if card.effect=="tap_or_untap":
        if game.phase=="after_attackers" and game.active_user!=user:
            choices=[(sum(game.current_stats(permanent)),position) for position,permanent in enumerate(game.player(user).battlefield,1) if game.is_creature(permanent) and permanent.tapped and _can_target(game,card,permanent)]
            return f"untap:{user}:{max(choices)[1]}" if choices else None
        if game.active_user!=user or game.phase not in ("precombat_main","after_attackers"): return None
        choices=[(sum(game.current_stats(permanent)) if game.is_creature(permanent) else game.card(permanent.uid).cost,position) for position,permanent in enumerate(game.player(game.opponent(user)).battlefield,1) if not permanent.tapped and any(game.has_current_type(permanent,kind) for kind in card.target_types) and _can_target(game,card,permanent)]
        return f"tap:{game.opponent(user)}:{max(choices)[1]}" if choices else None
    if card.effect=="destroy_wall":
        choices=[(sum(game.current_stats(permanent)),position) for position,permanent in enumerate(game.player(game.opponent(user)).battlefield,1) if "Wall" in game.card(permanent.uid).type_line.split(" — ",1)[-1].split() and _can_target(game,card,permanent)]
        return f"{game.opponent(user)}:{max(choices)[1]}" if choices else None
    if card.effect in ("destroy_creature","exile_creature_life"):
        creatures=[]
        for position,permanent in enumerate(game.player(game.opponent(user)).battlefield,1):
            target=game.card(permanent.uid)
            if not game.is_creature(permanent) or not _can_target(game,card,permanent): continue
            if card.target_nonartifact and "Artifact" in target.type_line: continue
            if card.target_nonblack and "B" in game.current_colors(permanent): continue
            creatures.append((position,permanent))
        if not creatures: return None
        position,_=max(creatures,key=lambda item:sum(game.current_stats(item[1])))
        return f"{game.opponent(user)}:{position}"
    if card.effect == "destroy_permanent":
        targets=[(position,permanent) for position,permanent in enumerate(game.player(game.opponent(user)).battlefield,1) if any(game.has_current_type(permanent,kind) for kind in card.target_types) and _can_target(game,card,permanent)]
        if not targets: return None
        position,_=max(targets,key=lambda item:(bool(game.current_mana_choices(item[1])),game.card(item[1].uid).cost))
        return f"{game.opponent(user)}:{position}"
    if card.effect == "destroy_land":
        lands=[(position,permanent) for position,permanent in enumerate(game.player(game.opponent(user)).battlefield,1) if game.card(permanent.uid).land and _can_target(game,card,permanent)]
        if not lands: return None
        position,_=max(lands,key=lambda item:len(game.current_mana_choices(item[1])))
        return f"{game.opponent(user)}:{position}"
    if card.effect in ("pump","pump_blocking","pump_power_x","berserk"):
        creatures = [
            (position, permanent)
            for position, permanent in enumerate(game.player(user).battlefield, 1)
            if game.is_creature(permanent) and _can_target(game,card,permanent)
        ]
        if card.effect == "pump_blocking":
            blocking=game.all_blocker_uids(); creatures=[item for item in creatures if item[1].uid in blocking]
        elif card.effect=="berserk":
            if game.active_user!=user or game.phase not in ("after_attackers","after_blockers","after_first_strike"): return None
            creatures=[item for item in creatures if item[1].uid in game.attackers and not any(spell.key==card.key and spell.target==f"{user}:{item[1].uid}" for spell in game.stack)]
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
    if player.channel_active:
        for amount in range(1,player.life):
            simulated=Game.from_raw(game.to_raw())
            simulated.activate_channel(user,amount)
            if any(simulated.can_pay(user,card) for card in candidates):
                game.activate_channel(user,amount)
                return "channel"
    return None


def _global_enchantment_score(game,user,card):
    value=card.global_power+card.global_toughness
    def eligible(target_user,permanent):
        if not game.is_creature(permanent): return False
        if card.global_controller_only and target_user!=user: return False
        if card.global_buff_color and card.global_buff_color not in game.current_colors(permanent): return False
        if card.global_requires_untapped and permanent.tapped: return False
        if card.global_requires_attacking and not game.can_attack_permanent(permanent): return False
        return True
    own=sum(eligible(user,permanent) for permanent in game.player(user).battlefield)
    enemy=0 if card.global_controller_only else sum(eligible(game.opponent(user),permanent) for permanent in game.player(game.opponent(user)).battlefield)
    mountain_value=0
    if card.mountain_extra_red:
        mountain_value=sum(game.has_current_land_type(x,"mountain") and not x.tapped for x in game.player(user).battlefield)-sum(game.has_current_land_type(x,"mountain") and not x.tapped for x in game.player(game.opponent(user)).battlefield)
    return 5+value*(own-enemy)+mountain_value


def _fog_useful(game,user):
    if game.active_user==user or game.phase not in ("after_attackers","after_blockers","after_first_strike") or not game.attackers or game.prevent_combat_damage: return False
    if game.phase!="after_first_strike": return True
    combatants=set(game.attackers)|game.all_blocker_uids()
    return any("first_strike" not in game.current_keywords(permanent) for player in game.players.values() for permanent in player.battlefield if permanent.uid in combatants)

def _activate_guardian_angel(game,user):
    player=game.player(user)
    if not player.guardian_angel_active: return None
    target=None
    if not player.damage_prevention and _player_damage_threatened(game,user): target=str(user)
    if target is None:
        choices=[(game.card(permanent.uid).cost+sum(game.current_stats(permanent)),position) for position,permanent in enumerate(player.battlefield,1) if game.is_creature(permanent) and not permanent.damage_prevention and _permanent_damage_threatened(game,user,permanent)]
        if choices: target=f"{user}:{max(choices)[1]}"
    if target is None: return None
    try: game.activate_guardian_angel(user,target)
    except GameError: return None
    return "guardian_angel"


def _play_one(game, user, difficulty):
    player = game.player(user)
    if game.active_user == user and game.phase in ("precombat_main", "postcombat_main") and not game.stack and game.can_play_land(user):
        extra_sources=game.extra_land_sources(user) if player.land_played else []
        if not extra_sources or player.life>sum(game.card(source.uid).extra_land_damage for source in extra_sources):
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
        if card.effect=="discard_random_x" and x_value is not None:
            x_value=min(x_value,len(game.player(game.opponent(user)).hand))
        volcanic_target=None
        if card.effect=="volcanic_eruption" and x_value is not None:
            mountains=[f"{target_user}:{position}" for target_user in (game.opponent(user),user) for position,permanent in enumerate(game.player(target_user).battlefield,1) if game.has_current_land_type(permanent,"mountain") and _can_target(game,card,permanent)]
            x_value=min(x_value,len(mountains)); volcanic_target=",".join(mountains[:x_value]) if x_value else None
        forced_target=None
        if card.effect=="counter_mana_value_x":
            forced_target=_target(game,user,card)
            if forced_target:
                target_position=int(forced_target.split(":",1)[1]); target_spell=list(reversed(game.stack))[target_position-1]
                x_value=game.spell_mana_value(target_spell)
        if card.land or (x_value is not None and x_value<1 and card.effect!="counter_mana_value_x") or not game.can_pay(user,card,x_value or 0):
            continue
        if card.sacrifice_without_land_type and not any(game.has_current_land_type(permanent,card.sacrifice_without_land_type) for permanent in player.battlefield):
            continue
        if card.characteristic_pt and min(game.projected_stats(user,card))<=0:
            continue
        if card.effect=="prevent_combat_damage" and not _fog_useful(game,user):
            continue
        if card.effect=="simulacrum" and not player.damage_taken_this_turn:
            continue
        if card.kind != "Instant" and (game.active_user != user or game.phase not in ("precombat_main", "postcombat_main") or game.stack):
            continue
        target = forced_target if card.effect=="counter_mana_value_x" else (volcanic_target if card.effect=="volcanic_eruption" else _target(game,user,card))
        if card.effect in TARGETED_EFFECTS and target is None:
            continue
        score = 0
        if card.creature:
            score = sum(game.projected_stats(user,card))
        elif card.kind == "Artifact" and card.produces:
            score = 5
        elif card.effect=="simulacrum":
            score=5+2*player.damage_taken_this_turn
        elif card.global_power or card.global_toughness:
            score=_global_enchantment_score(game,user,card)
        elif card.tax_white_spells:
            own=sum(game.card(x.uid).has_type("Enchantment") and "W" in game.current_colors(x) and bool(game.card(x.uid).activation_cost or game.card(x.uid).activation_effect) for x in player.battlefield)
            enemy=sum(game.card(x.uid).has_type("Enchantment") and "W" in game.current_colors(x) and bool(game.card(x.uid).activation_cost or game.card(x.uid).activation_effect) for x in game.player(game.opponent(user)).battlefield)
            if own>enemy: continue
            score=4+2*(enemy-own)
        elif card.upkeep_turn_start_untapped_damage:
            own=sum(game.card(x.uid).land and not x.tapped for x in player.battlefield)
            enemy=sum(game.card(x.uid).land and not x.tapped for x in game.player(game.opponent(user)).battlefield)
            if enemy<=own or player.life<=own: continue
            score=4+enemy-own
        elif card.activation_effect=="damage_all":
            enemy=sum(game.is_creature(x) for x in game.player(game.opponent(user)).battlefield)
            if not enemy: continue
            score=5+enemy
        elif card.global_land_from_type:
            own=sum(game.has_current_land_type(permanent,card.global_land_from_type) for permanent in player.battlefield)
            enemy=sum(game.has_current_land_type(permanent,card.global_land_from_type) for permanent in game.player(game.opponent(user)).battlefield)
            if own>=enemy: continue
            score=4+enemy-own
        elif card.animate_land_type:
            own=sum(game.has_current_land_type(permanent,card.animate_land_type) for permanent in player.battlefield)
            enemy=sum(game.has_current_land_type(permanent,card.animate_land_type) for permanent in game.player(game.opponent(user)).battlefield)
            if own<=enemy: continue
            score=4+2*(own-enemy)
        elif card.mana_flare:
            own=sum(game.card(permanent.uid).land and not permanent.tapped for permanent in player.battlefield)
            enemy=sum(game.card(permanent.uid).land and not permanent.tapped for permanent in game.player(game.opponent(user)).battlefield)
            score=5+own-enemy
        elif card.extra_land_damage:
            score=5+sum(game.card(hand_uid).land for hand_uid in player.hand if hand_uid!=uid)
        elif card.land_enter_damage or card.land_grave_damage:
            own=sum(game.card(permanent.uid).land for permanent in player.battlefield)
            enemy=sum(game.card(permanent.uid).land for permanent in game.player(game.opponent(user)).battlefield)
            score=5+enemy-own
        elif card.upkeep_opponent_hand_damage:
            score=5+max(0,len(game.player(game.opponent(user)).hand)-4)
        elif card.upkeep_each_damage:
            if player.life<=card.upkeep_each_damage: continue
            score=4+player.life-game.player(game.opponent(user)).life
        elif card.upkeep_land_type_damage:
            own=sum(game.has_current_land_type(permanent,card.upkeep_land_type_damage) for permanent in player.battlefield)
            enemy=sum(game.has_current_land_type(permanent,card.upkeep_land_type_damage) for permanent in game.player(game.opponent(user)).battlefield)
            if own>=player.life or enemy<=own: continue
            score=5+enemy-own
        elif card.draw_step_extra:
            score=5+card.draw_step_extra
        elif card.skip_all_untap:
            own=sum(permanent.tapped for permanent in player.battlefield); enemy=sum(permanent.tapped for permanent in game.player(game.opponent(user)).battlefield)
            if own>=enemy: continue
            score=5+enemy-own
        elif card.untap_creature_limit:
            own=sum(game.is_creature(permanent) and permanent.tapped for permanent in player.battlefield); enemy=sum(game.is_creature(permanent) and permanent.tapped for permanent in game.player(game.opponent(user)).battlefield)
            if own>enemy: continue
            score=5+enemy-own
        elif card.untap_land_limit:
            own=sum(game.card(permanent.uid).land and permanent.tapped for permanent in player.battlefield); enemy=sum(game.card(permanent.uid).land and permanent.tapped for permanent in game.player(game.opponent(user)).battlefield)
            if own>enemy: continue
            score=5+enemy-own
        elif card.untap_power_limit:
            own=sum(game.is_creature(permanent) and game.current_stats(permanent)[0]>=card.untap_power_limit for permanent in player.battlefield)
            enemy=sum(game.is_creature(permanent) and game.current_stats(permanent)[0]>=card.untap_power_limit for permanent in game.player(game.opponent(user)).battlefield)
            if own>enemy: continue
            score=5+2*(enemy-own)
        elif card.white_as_red:
            white=sum((permanent.uid!=uid and "W" in game.current_mana_choices(permanent) and not permanent.tapped) for permanent in player.battlefield)+player.mana_pool.get("W",0)
            red_cards=sum("{R}" in game.card(hand_uid).mana_cost for hand_uid in player.hand if hand_uid!=uid)
            score=4+min(white,red_cards)
        elif card.opponent_forest_tap_life:
            score=5+sum(game.has_current_land_type(permanent,"forest") and not permanent.tapped for permanent in game.player(game.opponent(user)).battlefield)
        elif card.land_tap_damage:
            own=sum(game.card(permanent.uid).land and not permanent.tapped for permanent in player.battlefield)
            enemy=sum(game.card(permanent.uid).land and not permanent.tapped for permanent in game.player(game.opponent(user)).battlefield)
            score=5+enemy-own
        elif card.aura_target_types:
            scaling=sum(game.has_current_land_type(permanent,"forest") for permanent in player.battlefield) if card.aura_forest_scaling else 0
            score=10 if card.aura_hostile else 7+card.aura_power+card.aura_toughness+scaling+2*bool(card.aura_keyword or card.aura_attack_override or card.aura_blocked_except_wall)+3*bool(card.aura_animate_mana_value or card.aura_indestructible)+2*bool(card.aura_controller_upkeep_life)
        elif card.effect in ("damage","damage_any"):
            score = 12 + card.amount - card.self_damage
        elif card.effect in ("damage_x_exile","fireball"):
            score=12+(x_value or 0)
        elif card.effect=="volcanic_eruption":
            own=sum(game.is_creature(permanent) for permanent in player.battlefield); enemy=sum(game.is_creature(permanent) for permanent in game.player(game.opponent(user)).battlefield)
            if player.life<=x_value or own>enemy: continue
            score=10+2*(x_value or 0)+enemy-own
        elif card.effect=="drain_life_x":
            score=14+2*(x_value or 0)
        elif card.effect=="sacrifice_mana":
            sacrifice_position=int(target.split(":",1)[1]); sacrificed=player.battlefield[sacrifice_position-1]
            score=4+game.card(sacrificed.uid).cost-sum(game.current_stats(sacrificed))
        elif card.effect=="search_library":
            score=13 if player.library else 0
        elif card.effect in ("draw","draw_target"):
            score = 10 + card.amount
        elif card.effect=="draw_target_x":
            score=10+(x_value or 0)
        elif card.effect=="discard_random_x":
            score=12+2*(x_value or 0)
        elif card.effect in ("wheel_seven","timetwister"):
            own_after=max(0,len(player.hand)-1); enemy=len(game.player(game.opponent(user)).hand)
            advantage=enemy-own_after
            if card.effect=="timetwister":
                advantage+=max(0,len(player.graveyard)-len(game.player(game.opponent(user)).graveyard))//2
            if advantage<=0: continue
            score=12+advantage
        elif card.effect in ("pump","pump_blocking"):
            score = 7 + card.amount
        elif card.effect=="pump_power_x":
            score=7+(x_value or 0)
        elif card.effect=="berserk":
            score=12
        elif card.effect=="channel":
            if player.life<=2: continue
            score=7
        elif card.effect == "life":
            score = 4 + card.amount
        elif card.effect=="healing_salve":
            score=14 if target and target.startswith("prevent:") else 7
        elif card.effect=="life_target_x":
            score=4+(x_value or 0)
        elif card.effect=="extra_turn":
            score=24
        elif card.effect in ("mana_short","drain_power"):
            opponent=game.player(game.opponent(user)); score=8+sum(opponent.mana_pool.values())+sum(game.card(permanent.uid).land and not permanent.tapped for permanent in opponent.battlefield)
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
        elif card.effect in ("counter_spell","counter_mana_value_x","power_sink","elemental_blast"):
            score=15 if target and target.startswith("S:") else 11
        elif card.effect in ("regenerate_target","grant_keyword","tap_or_untap"):
            score=10
        elif card.effect=="set_color":
            score=4
        elif card.effect=="destroy_wall":
            score=11
        elif card.effect=="add_mana":
            score=6
        elif card.effect=="prevent_combat_damage":
            score=20
        elif card.effect=="siren_call":
            eligible=sum(game.is_creature(permanent) and not game._has_subtype(game.card(permanent.uid),"Wall") and not permanent.sick for permanent in game.player(game.active_user).battlefield)
            if user==game.active_user or game.phase not in ("upkeep","draw","precombat_main") or not eligible: continue
            score=10+3*eligible
        elif card.effect=="destroy_all_enchantments":
            enemy=sum(game.card(permanent.uid).has_type("Enchantment") for permanent in game.player(game.opponent(user)).battlefield)
            own=sum(game.card(permanent.uid).has_type("Enchantment") for permanent in player.battlefield)
            score=5+3*enemy-2*own
        elif card.effect == "destroy_all_creatures":
            enemy=sum(game.is_creature(permanent) for permanent in game.player(game.opponent(user)).battlefield)
            own=sum(game.is_creature(permanent) for permanent in player.battlefield)
            score=6+3*enemy-2*own
        elif card.effect in ("earthquake_x","hurricane_x"):
            score=8+(x_value or 0)
        elif card.effect in ("destroy_all_lands","destroy_land_type"):
            enemy=sum(1 for permanent in game.player(game.opponent(user)).battlefield if game.card(permanent.uid).land and (card.effect=="destroy_all_lands" or game.has_current_land_type(permanent,card.land_type)))
            own=sum(1 for permanent in player.battlefield if game.card(permanent.uid).land and (card.effect=="destroy_all_lands" or game.has_current_land_type(permanent,card.land_type)))
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
        required=[position for position in legal if game.card(game.player(user).battlefield[position-1].uid).attacks_each_combat or game.player(user).battlefield[position-1].uid in game.forced_attackers]
        optional=[position for position in legal if position not in required]
        return required+optional[::2]
    return legal


def _blocks(game, user, difficulty):
    all_blockers=[(position,permanent) for position,permanent in enumerate(game.player(user).battlefield,1) if game.is_creature(permanent) and not permanent.tapped]
    counts={}; assignments=[]
    lured=[(position,uid) for position,uid in enumerate(game.attackers,1) if any(game.card(aura.uid).aura_lure for aura in game.attached_auras(game.find_permanent(uid)[1]))]
    for blocker_position,blocker in all_blockers:
        legal=[item for item in lured if game.can_block(item[1],blocker.uid)[0]]; capacity=blocker.temporary_max_blocks or game.card(blocker.uid).max_blocks
        for attacker_position,_ in legal[:capacity]: assignments.append((attacker_position,blocker_position)); counts[blocker.uid]=counts.get(blocker.uid,0)+1
    optional=all_blockers if difficulty!="easy" else all_blockers[::2]
    for attacker_position,attacker_uid in sorted(enumerate(game.attackers,1),key=lambda item:game.current_stats(game.find_permanent(item[1])[1])[0],reverse=True):
        if any(position==attacker_position for position,_ in assignments): continue
        legal=[]
        for item in optional:
            capacity=item[1].temporary_max_blocks or game.card(item[1].uid).max_blocks
            if counts.get(item[1].uid,0)<capacity and game.can_block(attacker_uid,item[1].uid)[0]: legal.append(item)
        if not legal: continue
        attacker=game.find_permanent(attacker_uid)[1]; attacker_card=game.card(attacker_uid); attacker_power=game.current_stats(attacker)[0]
        def score(item):
            blocker=item[1]; blocker_card=game.card(blocker.uid); delayed_loss=attacker_card.combat_destroy_nonwall and not game._has_subtype(blocker_card,"Wall"); delayed_kill=blocker_card.combat_destroy_nonwall and not game._has_subtype(attacker_card,"Wall"); survives=game.current_stats(blocker)[1]>attacker_power
            return (2*delayed_kill-2*delayed_loss+survives,-sum(game.current_stats(blocker)))
        choice=max(legal,key=score); assignments.append((attacker_position,choice[0])); counts[choice[1].uid]=counts.get(choice[1].uid,0)+1
    return assignments

def _assign_attacker_damage(game,user):
    if user not in game.order or game.phase not in ("after_blockers","after_first_strike") or game.stack: return False
    first_step=game._combat_has_first_strike() and game.phase=="after_blockers"
    for attacker_uid in game.attackers:
        attacker=game.find_permanent(attacker_uid)[1]; blockers=[uid for uid in game.blockers_for(attacker_uid) if game.find_permanent(uid)[1] is not None]
        if attacker is None or len(blockers)<2 or user!=game.attacker_damage_owner(attacker_uid) or (("first_strike" in game.current_keywords(attacker))!=first_step): continue
        existing=game.attacker_damage_assignments.get(attacker_uid,[]); power=max(0,game.current_stats(attacker)[0]); trample="trample" in game.current_keywords(attacker)
        if {item.get("blocker") for item in existing}==set(blockers) and (sum(item.get("damage",0) for item in existing)==power or trample): continue
        remaining=power; ordered=sorted(blockers,key=lambda uid:(game.current_stats(game.find_permanent(uid)[1])[1]-game.find_permanent(uid)[1].damage,uid)); assignments=[]
        for uid in ordered:
            blocker=game.find_permanent(uid)[1]; amount=min(remaining,max(0,game.current_stats(blocker)[1]-blocker.damage)); assignments.append((game.player(game.opponent(game.active_user)).battlefield.index(blocker)+1,amount)); remaining-=amount
        if remaining and not trample: assignments[-1]=(assignments[-1][0],assignments[-1][1]+remaining)
        game.assign_attacker_damage(user,game.player(game.active_user).battlefield.index(attacker)+1,assignments); return True
    return False

def _assign_blocker_damage(game,user):
    if user not in game.order or game.phase not in ("after_blockers","after_first_strike") or game.stack: return False
    first_step=game._combat_has_first_strike() and game.phase=="after_blockers"
    for blocker_uid in game.all_blocker_uids():
        blocker=game.find_permanent(blocker_uid)[1]; blocked=[uid for uid in game.attackers_for(blocker_uid) if game.find_permanent(uid)[1] is not None]
        if blocker is None or len(blocked)<2 or user!=game.blocker_damage_owner(blocker_uid) or (("first_strike" in game.current_keywords(blocker))!=first_step): continue
        existing=game.blocker_damage_assignments.get(blocker_uid,[])
        if {item.get("attacker") for item in existing}==set(blocked) and sum(item.get("damage",0) for item in existing)==max(0,game.current_stats(blocker)[0]): continue
        power=max(0,game.current_stats(blocker)[0]); remaining=power; ordered=sorted(blocked,key=lambda uid:(game.current_stats(game.find_permanent(uid)[1])[1]-game.find_permanent(uid)[1].damage,uid)); assignments=[]
        for uid in ordered:
            attacker=game.find_permanent(uid)[1]; amount=min(remaining,max(0,game.current_stats(attacker)[1]-attacker.damage)); assignments.append((game.attackers.index(uid)+1,amount)); remaining-=amount
        if remaining: assignments[-1]=(assignments[-1][0],assignments[-1][1]+remaining)
        game.assign_blocker_damage(user,game.player(game.opponent(game.active_user)).battlefield.index(blocker)+1,assignments); return True
    return False


def _player_damage_threatened(game,user):
    for item in game.stack:
        card=game.card(item.uid)
        if item.ability_effect in ("tap_damage","land_event_damage","upkeep_damage","upkeep_land_type_damage","aura_upkeep_damage","upkeep_hand_damage","draw_tapped_damage","damage_any") and item.target==str(user): return True
        if not item.ability_effect and card.effect in ("earthquake_x","hurricane_x"): return item.x_value>0
        if not item.ability_effect and card.effect in ("damage","damage_any","damage_x_exile") and (item.target or str(game.opponent(item.owner)))==str(user): return True
        if item.ability_effect=="damage_any" and item.owner==user and card.activation_self_damage: return True
    if game.opponent(game.active_user)==user and game.phase in ("after_attackers","after_blockers","after_first_strike"):
        return any(uid not in game.blocks or "trample" in game.current_keywords(next(x for x in game.player(game.active_user).battlefield if x.uid==uid)) for uid in game.attackers)
    return False

def _permanent_damage_threatened(game,user,permanent):
    stable=f"{user}:{permanent.uid}"
    for item in game.stack:
        card=game.card(item.uid)
        if item.target==stable:
            if item.ability_effect=="damage_any" and card.activation_amount>0 and not game._protected_from(permanent,card,game.ability_source_colors(item)): return True
            if not item.ability_effect and card.effect in ("damage","damage_any","damage_x_exile") and (item.x_value if card.effect=="damage_x_exile" else card.amount)>0 and not game._protected_from(permanent,card,game.spell_colors(item)): return True
        if not item.ability_effect and card.effect in ("earthquake_x","hurricane_x") and item.x_value>0:
            flying="flying" in game.current_keywords(permanent)
            if ((card.effect=="earthquake_x" and not flying) or (card.effect=="hurricane_x" and flying)) and not game._protected_from(permanent,card,game.spell_colors(item)): return True
    if game.phase not in ("after_blockers","after_first_strike"): return False
    if permanent.uid in game.attackers:
        blockers=[game.find_permanent(uid)[1] for uid in game.blockers_for(permanent.uid)]
        return any(blocker is not None and game.current_stats(blocker)[0]>0 and not game._protected_from(permanent,game.card(blocker.uid),game.current_colors(blocker)) for blocker in blockers)
    attacker_uid=next(iter(game.attackers_for(permanent.uid)),None)
    attacker=game.find_permanent(attacker_uid)[1] if attacker_uid is not None else None
    return attacker is not None and game.current_stats(attacker)[0]>0 and not game._protected_from(permanent,game.card(attacker.uid),game.current_colors(attacker))

def _activation_target(game,user,card,source_uid=None):
    opponent=game.opponent(user)
    if card.activation_effect=="prevent_source_damage":
        protected=set(game.player(user).source_damage_prevention)
        damaging={"damage","damage_any","damage_x_exile","drain_life_x","earthquake_x","hurricane_x"}
        for position,item in enumerate(reversed(game.stack),1):
            source=game.card(item.uid)
            identity=item.source_uid if item.ability_effect and item.source_uid is not None else item.uid
            colors=game.ability_source_colors(item) if item.ability_effect else game.spell_colors(item)
            player_target=(item.target or "")==str(user) or source.effect in ("earthquake_x","hurricane_x")
            ability_damage=item.ability_effect in {"tap_damage","land_event_damage","upkeep_damage","upkeep_land_type_damage","aura_upkeep_damage","upkeep_sacrifice","upkeep_hand_damage","draw_tapped_damage"}
            if identity not in protected and card.prevent_source_color in colors and ((not item.ability_effect and source.effect in damaging and player_target) or ability_damage):
                if item.ability_effect:
                    controller,permanent=game.find_permanent(identity)
                    if permanent is not None: return f"{controller.user_id}:{controller.battlefield.index(permanent)+1}"
                else: return f"S:{position}"
        if game.active_user!=user and game.phase in ("after_blockers","after_first_strike"):
            attacker_player=game.player(game.active_user)
            for uid in game.attackers:
                permanent=game.find_permanent(uid)[1]
                if uid not in protected and uid not in game.blocks and permanent is not None and card.prevent_source_color in game.current_colors(permanent): return f"{game.active_user}:{attacker_player.battlefield.index(permanent)+1}"
        return None
    if card.activation_effect=="force_attack":
        if game.active_user==user or game.phase not in ("upkeep","draw","precombat_main"): return None
        choices=[(sum(game.current_stats(permanent)),position) for position,permanent in enumerate(game.player(game.active_user).battlefield,1) if game.is_creature(permanent) and not game._has_subtype(game.card(permanent.uid),"Wall") and not permanent.sick and _can_target(game,card,permanent)]
        return f"{game.active_user}:{max(choices)[1]}" if choices else None
    if card.activation_effect=="cap_unblocked_damage":
        if game.phase not in ("after_blockers","after_first_strike") or game.active_user==user: return None
        protected=game.player(user).source_damage_caps
        attackers=game.player(game.active_user)
        choices=[(game.current_stats(permanent)[0],position) for position,permanent in enumerate(attackers.battlefield,1) if permanent.uid in game.attackers and permanent.uid not in game.blocks and permanent.uid not in protected]
        return f"{game.active_user}:{max(choices)[1]}" if choices else None
    if card.activation_effect=="redirect_one_to_owner":
        _,source=game.find_permanent(source_uid)
        return f"{user}:{game.player(user).battlefield.index(source)+1}" if source is not None and source.owner==user and not source.redirect_damage_to_owner and _permanent_damage_threatened(game,user,source) else None
    if card.activation_effect=="redirect_source_to_creature":
        for target_position,permanent in enumerate(game.player(user).battlefield,1):
            stable=f"{user}:{permanent.uid}"
            if not game.is_creature(permanent) or permanent.redirect_source_damage_to_player: continue
            for stack_position,item in enumerate(reversed(game.stack),1):
                source=game.card(item.uid); amount=source.activation_amount if item.ability_effect=="damage_any" else (item.x_value if source.effect=="damage_x_exile" else source.amount)
                if item.target==stable and amount>0: return f"S:{stack_position}>{user}:{target_position}"
            attacker_uid=next(iter(game.attackers_for(permanent.uid)),None)
            if attacker_uid is not None:
                attacker_player=game.player(game.active_user); attacker=game.find_permanent(attacker_uid)[1]
                if attacker is not None and game.current_stats(attacker)[0]>0: return f"{game.active_user}:{attacker_player.battlefield.index(attacker)+1}>{user}:{target_position}"
        return None
    if card.activation_effect=="counter_color":
        for position,spell in enumerate(reversed(game.stack),1):
            if not spell.ability_effect and spell.owner!=user and card.target_color in game.spell_colors(spell): return f"S:{position}"
        return None
    if card.activation_effect in ("draw_self","create_token","take_extra_turn"): return str(user)
    if card.activation_effect in ("discard_choice","look_hand"):
        return str(opponent) if game.player(opponent).hand else None
    if card.activation_effect=="damage_all":
        if game.player(user).life<=card.activation_amount: return None
        own=sum(game.is_creature(x) and game.current_stats(x)[1]-x.damage<=card.activation_amount for x in game.player(user).battlefield)
        enemy=sum(game.is_creature(x) and game.current_stats(x)[1]-x.damage<=card.activation_amount for x in game.player(game.opponent(user)).battlefield)
        return str(user) if enemy>own else None
    if card.activation_effect=="prevent_player_damage": return str(user) if not game.player(user).damage_prevention and _player_damage_threatened(game,user) else None
    if card.activation_effect=="prevent_any_damage":
        if not game.player(user).damage_prevention and _player_damage_threatened(game,user): return str(user)
        choices=[(game.card(permanent.uid).cost+sum(game.current_stats(permanent)),position) for position,permanent in enumerate(game.player(user).battlefield,1) if game.is_creature(permanent) and not permanent.damage_prevention and _permanent_damage_threatened(game,user,permanent)]
        return f"{user}:{max(choices)[1]}" if choices else None
    if card.activation_effect=="animate_self":
        _,source=game.find_permanent(source_uid)
        pending=any(item.ability_effect=="animate_self" and item.source_uid==source_uid for item in game.stack)
        return str(user) if source is not None and not source.animated_until_end_combat and not pending and game.active_user!=user and game.phase=="after_attackers" and game.attackers else None
    if card.activation_effect=="destroy_all_nonland":
        def board_value(player):
            return sum(
                1+max(target.cost,sum(game.current_stats(permanent)) if game.is_creature(permanent) else 0)
                for permanent in player.battlefield
                for target in (game.card(permanent.uid),)
                if any(game.has_current_type(permanent,kind) for kind in ("Artifact","Creature","Enchantment"))
            )
        return str(user) if board_value(game.player(opponent))>board_value(game.player(user)) else None
    if card.activation_effect=="damage_any":
        if card.activation_self_damage and game.player(user).life<=card.activation_self_damage: return None
        return str(opponent)
    if card.activation_effect in ("set_land_forest","add_mire_counter"):
        wanted=game.changed_land_word(source_uid,"forest" if card.activation_effect=="set_land_forest" else "swamp")
        for target_user in (opponent,user):
            choices=[]
            for position,permanent in enumerate(game.player(target_user).battlefield,1):
                if game.card(permanent.uid).land and not game.has_current_land_type(permanent,wanted) and _can_target(game,card,permanent):
                    choices.append((len(game.current_mana_choices(permanent)),position))
            if choices: return f"{target_user}:{max(choices)[1]}"
        return None
    candidates=[]
    for position,permanent in enumerate(game.player(opponent).battlefield,1):
        target=game.card(permanent.uid)
        if not _can_target(game,card,permanent): continue
        if card.activation_effect=="destroy_black_permanent" and "B" in game.current_colors(permanent): candidates.append((target.cost,position))
        elif card.activation_effect=="destroy_tapped_creature" and game.is_creature(permanent) and permanent.tapped: candidates.append((sum(game.current_stats(permanent)),position))
        elif card.activation_effect=="destroy_wall" and "Wall" in target.type_line.split(" — ",1)[-1].split(): candidates.append((sum(game.current_stats(permanent)),position))
        elif card.activation_effect=="destroy_land" and target.land: candidates.append((1+len(target.produces),position))
    if candidates: return f"{opponent}:{max(candidates)[1]}"
    if card.activation_effect=="grant_banding" and game.active_user==user and game.phase=="precombat_main":
        choices=[(game.current_stats(permanent)[0],position) for position,permanent in enumerate(game.player(user).battlefield,1) if permanent.uid!=source_uid and game.is_creature(permanent) and game.can_attack_permanent(permanent) and "banding" not in game.current_keywords(permanent) and _can_target(game,card,permanent)]
        return f"{user}:{max(choices)[1]}" if choices else None
    if card.activation_effect=="unblockable" and game.active_user==user and game.phase in ("precombat_main","after_attackers"):
        attackers=set(game.attackers) if game.phase=="after_attackers" else None
        choices=[]
        for position,permanent in enumerate(game.player(user).battlefield,1):
            target=game.card(permanent.uid)
            can_attack=game.can_attack_permanent(permanent)
            if permanent.uid!=source_uid and game.is_creature(permanent) and _can_target(game,card,permanent) and can_attack and game.current_stats(permanent)[0]<=2 and "unblockable" not in game.current_keywords(permanent) and (attackers is None or permanent.uid in attackers):
                choices.append((game.current_stats(permanent)[0],position))
        if choices: return f"{user}:{max(choices)[1]}"
    if card.activation_effect=="grant_flying_delayed_destroy":
        _,source=game.find_permanent(source_uid)
        if source is None or game.active_user!=user or game.phase not in ("precombat_main","after_attackers"): return None
        pending={item.target for item in game.stack if item.ability_effect=="grant_flying_delayed_destroy"}
        delayed={item.target for item in game.end_step_destroys}
        choices=[]
        for position,permanent in enumerate(game.player(user).battlefield,1):
            stable=f"{user}:{permanent.uid}"
            if permanent.uid!=source_uid and game.is_creature(permanent) and stable not in pending and stable not in delayed and "flying" not in game.current_keywords(permanent) and game.current_stats(permanent)[1]<game.current_stats(source)[0] and _can_target(game,card,permanent):
                choices.append((game.current_stats(permanent)[0],position))
        return f"{user}:{max(choices)[1]}" if choices else None
    if card.activation_effect=="tap_permanent":
        choices=[]
        for position,permanent in enumerate(game.player(opponent).battlefield,1):
            target=game.card(permanent.uid)
            if not permanent.tapped and any(game.has_current_type(permanent,kind) for kind in ("Artifact","Creature","Land")) and _can_target(game,card,permanent):
                score=(10+sum(game.current_stats(permanent))) if game.is_creature(permanent) else target.cost
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
        blockers=[game.find_permanent(uid)[1] for uid in game.blockers_for(permanent.uid)]
        return any(blocker is not None and game.current_stats(blocker)[0]>=toughness for blocker in blockers)
    for attacker_uid in game.attackers_for(permanent.uid):
        attacker=next((x for x in game.player(game.active_user).battlefield if x.uid==attacker_uid),None)
        if attacker is not None and game.current_stats(attacker)[0]>=toughness: return True
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
        if activation_effect in ("regenerate","corpse_regenerate") and target is not None and not target.regeneration_shields and _regeneration_threatened(game,next(player.user_id for player in game.players.values() if target in player.battlefield),target) and game.can_activate(user,position):
            candidates.append((game.card(target.uid).cost,position))
    if not candidates: return None
    _,position=max(candidates); game.activate_ability(user,position); return "activate"

def _activate_clockwork(game,user):
    if game.phase!="upkeep" or game.active_user!=user: return None
    for position,permanent in enumerate(game.player(user).battlefield,1):
        card=game.card(permanent.uid)
        if not card.activation_x_choice or permanent.tapped or permanent.power_counters>=7: continue
        capacity=7-permanent.power_counters; x_value=0
        while x_value<capacity and game.can_activate(user,position,x_value=x_value+1,choice_value=x_value+1): x_value+=1
        if x_value:
            game.activate_ability(user,position,x_value=x_value,choice_value=x_value); return "activate"
    return None

def _activate_untap_aura(game,user):
    for position,source in enumerate(game.player(user).battlefield,1):
        card=game.card(source.uid)
        if card.activation_effect!="untap_attached": continue
        _,target=game.find_permanent(source.attached_to)
        if target is not None and target.tapped and game.can_activate(user,position):
            game.activate_ability(user,position); return "activate"
    return None

def _choose_bodyguard(game,user):
    if game.active_user==user or game.phase not in ("after_blockers","after_first_strike"): return None
    player=game.player(user); choices=[(game.current_stats(permanent)[1]-permanent.damage,position,permanent) for position,permanent in enumerate(player.battlefield,1) if game.card(permanent.uid).redirects_unblocked_combat_damage and not permanent.tapped]
    if not choices: return None
    _,position,chosen=max(choices)
    if player.bodyguard_choice==chosen.uid: return None
    game.choose_bodyguard(user,position); return "choose_bodyguard"


def _activate_hydra(game,user):
    player=game.player(user)
    for position,permanent in enumerate(player.battlefield,1):
        card=game.card(permanent.uid)
        if not card.hydra_damage_replacement: continue
        if _permanent_damage_threatened(game,user,permanent) and not permanent.damage_prevention and game._mana_payment(player,card,mana_cost="{R}") is not None:
            game.activate_hydra(user,position,"prevent"); return "hydra_prevent"
        if game.active_user==user and game.phase=="upkeep" and game._mana_payment(player,card,mana_cost="{R}{R}{R}") is not None:
            game.activate_hydra(user,position,"counter"); return "hydra_counter"
    return None

def _activate_owned_incarnation(game,user):
    for controller in game.players.values():
        if controller.user_id==user: continue
        for position,permanent in enumerate(controller.battlefield,1):
            if game.card(permanent.uid).key=="lea:31" and permanent.owner==user and not permanent.redirect_damage_to_owner and _permanent_damage_threatened(game,controller.user_id,permanent):
                game.activate_personal_incarnation(user,controller.user_id,position); return "activate_owned_incarnation"
    return None


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
    combat=set(game.attackers) if game.active_user==user else game.all_blocker_uids()
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
        if game.turn_start_pending_user is not None:
            if game.turn_start_pending_user!=user: return changed
            choices=game.time_vault_choices(user); skip=not game.turn_start_pending_extra
            game.choose_time_vault_turn(user,skip,choices[0][0] if skip else None); game.record(user,"ai_vault_skip" if skip else "ai_vault_take"); changed=True; continue
        if game.sanctuary_draw_pending:
            if game.active_user!=user: return changed
            opponent=game.player(game.opponent(user)); threatened=not game.player(user).island_sanctuary_active and any(game.is_creature(permanent) and "flying" not in game.current_keywords(permanent) and "islandwalk" not in game.current_keywords(permanent) for permanent in opponent.battlefield)
            game.choose_sanctuary_draw(user,threatened); game.record(user,"ai_sanctuary_skip" if threatened else "ai_sanctuary_draw"); changed=True; continue
        if game.phase == "untap":
            choices=game.untap_choices()
            def untap_value(choice):
                player=game.player(user)
                return sum((3 if game.card(player.battlefield[position-1].uid).produces else 0)+sum(game.current_stats(player.battlefield[position-1])) for position in choice)
            game.choose_untap(user,max(choices,key=untap_value)); game.record(user,"ai_untap"); changed=True; continue
        if game.phase == "attackers":
            if game.active_user != user:
                return changed
            positions=_attack_positions(game,user,difficulty)
            banding=[position for position in positions if "banding" in game.current_keywords(game.player(user).battlefield[position-1])]
            others=[position for position in positions if position not in banding]
            bands=[banding+[others[0]]] if banding and others else ([banding] if len(banding)>1 else [])
            game.declare_attackers(user,positions,bands)
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
        if _assign_attacker_damage(game,user): game.record(user,"ai_attacker_damage"); changed=True; continue
        if _assign_blocker_damage(game,user): game.record(user,"ai_blocker_damage"); changed=True; continue
        if game.stack and game.stack[-1].decision_pending:
            trigger=game.stack[-1]
            if trigger.ability_effect in ("discard_choice","look_hand"):
                if trigger.ability_effect=="discard_choice":
                    _,choices=game.private_hand_decision(user); position,_=min(choices,key=lambda item:(item[1].cost+item[1].power+item[1].toughness,item[0]))
                else: position=None
                game.choose_private_hand(user,position); game.record(user,"ai_private_discard" if position is not None else "ai_private_hand_view"); changed=True; continue
            if not trigger.ability_effect and game.card(trigger.uid).effect=="drain_power":
                position,permanent,mana=game.drain_power_choice(user)
                game.choose_drain_power(user,position,mana[0]); game.record(user,"ai_drain_power_choice"); changed=True; continue
            if not trigger.ability_effect and game.card(trigger.uid).effect=="power_sink":
                cost=f"{{{trigger.x_value}}}"; pay=game._mana_payment(game.player(user),game.card(trigger.uid),mana_cost=cost) is not None
                game.choose_power_sink(user,pay); game.record(user,"ai_power_sink_pay" if pay else "ai_power_sink_decline"); changed=True; continue
            if not trigger.ability_effect and game.card(trigger.uid).effect=="search_library":
                choices=game.library_search(user)
                position,_=max(choices,key=lambda item:(item[1].cost+item[1].power+item[1].toughness+2*len(item[1].keywords),-item[0]))
                game.choose_library(user,position); game.record(user,"ai_search_library"); changed=True; continue
            if not trigger.ability_effect and game.card(trigger.uid).effect=="natural_selection":
                _,target,entries=game.natural_selection_decision(user)
                valued=sorted(entries,key=lambda item:(item[1].cost+item[1].power+item[1].toughness+2*len(item[1].keywords),-item[0]),reverse=target.user_id==user)
                game.choose_natural_selection(user,tuple(position for position,_ in valued)); game.record(user,"ai_natural_selection"); changed=True; continue
            if not trigger.ability_effect and game.card(trigger.uid).effect in ("text_change_land","text_change_color"):
                effect=game.card(trigger.uid).effect; target=game._word_change_target(trigger.target); text=game.card(target.uid).text.casefold() if target is not None else ""
                names=("plains","island","swamp","mountain","forest") if effect=="text_change_land" else ("white","blue","black","red","green"); symbols=names if effect=="text_change_land" else ("W","U","B","R","G")
                source=next((symbols[index] for index,name in enumerate(names) if name in text),symbols[0]); replacement=next(value for value in symbols if value!=source)
                game.choose_word_change(user,source,replacement); game.record(user,"ai_word_change"); changed=True; continue
            if not trigger.ability_effect and game.card(trigger.uid).effect=="false_orders":
                choices=game.false_orders_choices(user); position=max(choices,key=lambda item:game.current_stats(item[1])[0])[0] if choices and user!=game.active_user else None
                game.choose_false_orders(user,position); game.record(user,"ai_false_orders_block" if position is not None else "ai_false_orders_decline"); changed=True; continue
            if trigger.ability_effect.startswith("balance_"):
                choices=game.balance_choices(trigger,user); required=game._balance_required(trigger,user); stage=trigger.ability_effect.split("_",1)[1]
                if stage=="hand": ranked=sorted(choices,key=lambda item:(item[1].cost+item[1].power+item[1].toughness,item[0])); positions=[position for position,_ in ranked[:required]]
                elif stage=="creatures": ranked=sorted(choices,key=lambda item:(sum(game.current_stats(item[1]))+game.card(item[1].uid).cost,item[0]),reverse=True); positions=[position for position,_ in ranked[:required]]
                else: ranked=sorted(choices,key=lambda item:(len(game.current_mana_choices(item[1])),item[0]),reverse=True); positions=[position for position,_ in ranked[:required]]
                game.choose_balance(user,positions); game.record(user,f"ai_balance_{stage}"); changed=True; continue
            if trigger.ability_effect=="kudzu_move":
                choices=game.kudzu_choices(trigger)
                opponents=[choice for choice in choices if choice[0]!=user]; pool=opponents or choices
                if pool:
                    owner,position,_=max(pool,key=lambda item:(len(game.current_mana_choices(item[2])),game.card(item[2].uid).cost,-item[1])); game.choose_kudzu(user,owner,position); action="ai_kudzu_attach"
                else: game.choose_kudzu(user); action="ai_kudzu_decline"
                game.record(user,action); changed=True; continue
            if trigger.ability_effect=="vesuvan_copy":
                if trigger.choice_value==0:
                    owner,position,_=max(game.vesuvan_choices(trigger),key=lambda item:(sum(game.current_stats(item[2])),item[0]==user,-item[1])); game.choose_vesuvan_copy(user,owner,position); action="ai_vesuvan_target"
                else:
                    game.choose_vesuvan_copy(user,accept=True); action="ai_vesuvan_copy"
                game.record(user,action); changed=True; continue
            if trigger.ability_effect=="power_leak":
                amount=max(game.power_leak_amounts(trigger,2))
                game.choose_power_leak(user,amount); game.record(user,"ai_power_leak"); changed=True; continue
            if not trigger.ability_effect and game.card(trigger.uid).enters_copy_types:
                choices=game.copy_choices(trigger)
                if choices:
                    owner,position,_=max(choices,key=lambda item:(sum(game.current_stats(item[2])) if game.is_creature(item[2]) else game.card(item[2].uid).cost,item[0]==user,-item[1]))
                    game.choose_copy(user,owner,position)
                else: game.choose_copy(user)
                game.record(user,"ai_copy_choice"); changed=True; continue
            source=game.find_permanent(trigger.source_uid)[1]
            if trigger.ability_effect=="tomb_cleanup":
                choices=game.tomb_cleanup_choices(trigger.source_uid,trigger.choice_value)
                controller,position,_=max(choices,key=lambda item:(item[0]==user,len(game.current_mana_choices(item[2])),-item[1]))
                game.choose_tomb_cleanup(user,controller,position); game.record(user,"ai_tomb_cleanup"); changed=True; continue
            if trigger.ability_effect in ("upkeep_sacrifice","opponent_land_sacrifice"):
                choices=game.trigger_sacrifice_choices(trigger)
                if trigger.ability_effect=="upkeep_sacrifice": position,_=min(choices,key=lambda item:(game.card(item[1].uid).cost+sum(game.current_stats(item[1])),item[0]))
                else: position,_=min(choices,key=lambda item:(len(game.card(item[1].uid).produces),item[0]))
                game.choose_trigger(user,True,position); game.record(user,"ai_trigger_sacrifice"); changed=True; continue
            if trigger.ability_effect=="upkeep_untap":
                useful=source is not None and source.tapped
            elif trigger.ability_effect=="aura_upkeep_untap":
                target=game._stable_target_permanent(trigger.target); useful=target is not None and target.tapped
            elif trigger.ability_effect=="upkeep_cost":
                useful=source is not None or game.card(trigger.uid).upkeep_unpaid_effect=="damage"
            elif trigger.ability_effect=="aura_upkeep_life":
                useful=game.player(user).life<20
            else:
                useful=True
            cost=game.trigger_cost(trigger)
            pay=useful and (not cost or game._mana_payment(game.player(user),game.card(trigger.uid),mana_cost=cost) is not None)
            game.choose_trigger(user,pay); game.record(user,("ai_trigger_accept" if not cost else "ai_trigger_pay") if pay else "ai_trigger_decline"); changed=True; continue
        action = _activate_regeneration(game,user) or _choose_bodyguard(game,user) or _activate_hydra(game,user) or _activate_owned_incarnation(game,user) or _activate_guardian_angel(game,user) or _activate_clockwork(game,user) or _activate_untap_aura(game,user) or _activate_targeted_ability(game,user) or _activate_combat_pump(game,user) or _play_one(game, user, difficulty)
        if action:
            game.record(user, f"ai_{action}")
        else:
            game.pass_priority(user)
            game.record(user, "ai_pass")
        changed = True
    raise GameError("Solo opponent exceeded its action limit.")
