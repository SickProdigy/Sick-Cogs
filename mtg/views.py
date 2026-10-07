import asyncio
from itertools import permutations
import discord
from .art import HAND_PAGE_SIZE
from .ai import TARGETED_EFFECTS, _target
from .engine import GameError

class HistoryPaginationView(discord.ui.View):
    page_size=15
    def __init__(self,cog,game_id,user_id,page,pages):
        super().__init__(timeout=180); self.cog,self.game_id,self.user_id,self.page,self.pages=cog,game_id,user_id,page,pages
        self.previous.disabled=page<=0; self.next.disabled=page>=pages-1
    async def interaction_check(self,interaction):
        if interaction.user.id==self.user_id: return True
        await interaction.response.send_message("This game history belongs to another player.",ephemeral=True); return False
    @discord.ui.button(label="Newer",style=discord.ButtonStyle.secondary)
    async def previous(self,interaction,button):
        await interaction.response.defer(); await self.cog.send_history(interaction,self.game_id,self.page-1,editing=True)
    @discord.ui.button(label="Older",style=discord.ButtonStyle.secondary)
    async def next(self,interaction,button):
        await interaction.response.defer(); await self.cog.send_history(interaction,self.game_id,self.page+1,editing=True)

class ChallengeDeckSelect(discord.ui.Select):
    def __init__(self,challenge):
        self.challenge=challenge
        options=[discord.SelectOption(label="Red · Aggro and burn",value="red"),discord.SelectOption(label="Green · Stompy and ramp",value="green")]
        super().__init__(placeholder="Choose your deck and accept",min_values=1,max_values=1,options=options)
    async def callback(self,i):
        await i.response.defer()
        try: game=await self.challenge.cog.create_game(self.challenge.challenger,self.challenge.opponent,i.channel_id,{self.challenge.challenger:self.challenge.challenger_deck,self.challenge.opponent:self.values[0]})
        except GameError as e: await i.followup.send(str(e),ephemeral=True); return
        self.challenge.stop(); embed,file=await self.challenge.cog.game_message(game)
        await i.edit_original_response(content=None,embed=embed,attachments=[file] if file else [],view=GameView(self.challenge.cog,game.game_id))
        game.message_id=i.message.id; await self.challenge.cog.save(game)

class ChallengeView(discord.ui.View):
    def __init__(self,cog,challenger,opponent,challenger_deck="red"):
        super().__init__(timeout=120); self.cog=cog; self.challenger=challenger; self.opponent=opponent; self.challenger_deck=challenger_deck; self.add_item(ChallengeDeckSelect(self))
    async def interaction_check(self,i):
        if i.user.id==self.opponent: return True
        await i.response.send_message("Only the challenged player can answer.",ephemeral=True); return False
    @discord.ui.button(label="Decline",style=discord.ButtonStyle.secondary)
    async def decline(self,i,b): self.stop(); await i.response.edit_message(content="Challenge declined.",view=None)

class SacrificeSelect(discord.ui.Select):
    def __init__(self,cog,game_id,game,trigger):
        self.cog,self.game_id=cog,game_id
        choices=game.trigger_sacrifice_choices(trigger)[:25]; subject="land" if trigger.ability_effect=="opponent_land_sacrifice" else "creature"
        options=[discord.SelectOption(label=f"{position}. {game.card(permanent.uid).name}"[:100],description=f"Sacrifice this {subject}"[:100],value=str(position)) for position,permanent in choices]
        super().__init__(placeholder=f"Choose a {subject} to sacrifice",min_values=1,max_values=1,options=options,custom_id=f"mtg:{game_id}:sacrifice")
    async def callback(self,i):
        position=int(self.values[0])
        await self.cog.act(i,self.game_id,lambda g:g.choose_trigger(i.user.id,True,position),"trigger_sacrifice")

class UntapSelect(discord.ui.Select):
    def __init__(self,cog,game_id,game):
        self.cog,self.game_id=cog,game_id
        player=game.player(game.active_user); pending=set(game.untap_pending)
        options=[discord.SelectOption(label=f"{position}. {game.card(permanent.uid).name}"[:100],description="Untap this permanent" if len(game.untap_choices())==1 else "Include in your legal untap choice",value=str(position)) for position,permanent in enumerate(player.battlefield,1) if permanent.uid in pending][:25]
        maximum=max(len(choice) for choice in game.untap_choices())
        super().__init__(placeholder="Choose restricted permanents to untap",min_values=1,max_values=min(maximum,len(options)),options=options,custom_id=f"mtg:{game_id}:untap")
    async def callback(self,i):
        await self.cog.act(i,self.game_id,lambda g:g.choose_untap(i.user.id,[int(value) for value in self.values]),"untap")

class DrainPowerSelect(discord.ui.Select):
    def __init__(self,cog,game_id,game):
        self.cog,self.game_id=cog,game_id
        choice=game.drain_power_choice(game.stack[-1].choice_owner)
        position,permanent,mana=choice
        name=game.card(permanent.uid).name
        options=[discord.SelectOption(label=f"{name}: add {{{symbol}}}"[:100],value=f"{position}:{symbol}") for symbol in mana]
        super().__init__(placeholder=f"Choose {name}'s mana ability",min_values=1,max_values=1,options=options,custom_id=f"mtg:{game_id}:drain_power")
    async def callback(self,i):
        position,symbol=self.values[0].split(":",1)
        await self.cog.act(i,self.game_id,lambda g:g.choose_drain_power(i.user.id,int(position),symbol),"drain_power_choice")

class TimeVaultSelect(discord.ui.Select):
    def __init__(self,cog,game_id,game):
        self.cog,self.game_id=cog,game_id
        options=[discord.SelectOption(label=f"{position}. {game.card(permanent.uid).name}"[:100],description="Skip this turn and untap this Time Vault",value=str(position)) for position,permanent in game.time_vault_choices(game.turn_start_pending_user)][:25]
        super().__init__(placeholder="Choose a Time Vault and skip the turn",min_values=1,max_values=1,options=options,custom_id=f"mtg:{game_id}:vault_skip")
    async def callback(self,i):
        await self.cog.act(i,self.game_id,lambda g:g.choose_time_vault_turn(i.user.id,True,int(self.values[0])),"vault_skip")

class PowerLeakSelect(discord.ui.Select):
    def __init__(self,cog,game_id,game):
        self.cog,self.game_id=cog,game_id
        options=[discord.SelectOption(label=f"Pay {{{amount}}}",description=f"Power Leak deals {max(0,2-amount)} damage",value=str(amount)) for amount in game.power_leak_amounts(game.stack[-1])]
        super().__init__(placeholder="Choose mana to pay for Power Leak",min_values=1,max_values=1,options=options,custom_id=f"mtg:{game_id}:power_leak")
    async def callback(self,i):
        amount=int(self.values[0])
        await self.cog.act(i,self.game_id,lambda g:g.choose_power_leak(i.user.id,amount),"power_leak")

class CopySelect(discord.ui.Select):
    def __init__(self,cog,game_id,game):
        self.cog,self.game_id=cog,game_id
        options=[discord.SelectOption(label="Enter without copying",value="none",description="Use the card's printed characteristics")]
        options.extend(discord.SelectOption(label=f"{owner}:{position}. {game.card(permanent.uid).name}"[:100],description=game.card(permanent.uid).type_line[:100],value=f"{owner}:{position}") for owner,position,permanent in game.copy_choices(game.stack[-1])[:24])
        super().__init__(placeholder="Choose a permanent to copy",min_values=1,max_values=1,options=options,custom_id=f"mtg:{game_id}:copy")
    async def callback(self,i):
        value=self.values[0]
        if value=="none": action=lambda g:g.choose_copy(i.user.id)
        else:
            owner,position=(int(part) for part in value.split(":")); action=lambda g:g.choose_copy(i.user.id,owner,position)
        await self.cog.act(i,self.game_id,action,"copy_choice")

class VesuvanSelect(discord.ui.Select):
    def __init__(self,cog,game_id,game):
        self.cog,self.game_id=cog,game_id; trigger=game.stack[-1]
        if trigger.choice_value==0:
            options=[discord.SelectOption(label=f"{owner}:{position}. {game.card(permanent.uid).name}"[:100],description=game.card(permanent.uid).type_line[:100],value=f"target:{owner}:{position}") for owner,position,permanent in game.vesuvan_choices(trigger)[:25]]
            placeholder="Choose Vesuvan Doppelganger's target"
        else:
            options=[discord.SelectOption(label="Become the copy",value="accept",description="Apply the copy effect"),discord.SelectOption(label="Keep current form",value="decline",description="Decline the optional copy effect")]
            placeholder="Resolve Vesuvan Doppelganger's choice"
        super().__init__(placeholder=placeholder,min_values=1,max_values=1,options=options,custom_id=f"mtg:{game_id}:vesuvan")
    async def callback(self,i):
        value=self.values[0]
        if value.startswith("target:"):
            _,owner,position=value.split(":"); action=lambda g:g.choose_vesuvan_copy(i.user.id,int(owner),int(position))
        else: action=lambda g:g.choose_vesuvan_copy(i.user.id,accept=value=="accept")
        await self.cog.act(i,self.game_id,action,"vesuvan_copy_choice")

class FalseOrdersSelect(discord.ui.Select):
    def __init__(self,cog,game_id,game):
        self.cog,self.game_id=cog,game_id; spell=game.stack[-1]
        options=[discord.SelectOption(label="Do not assign a new blocker",value="decline",description="Only remove the targeted creature from its current combat assignments")]
        options.extend(discord.SelectOption(label=f"A:{position}. {game.card(attacker.uid).name}"[:100],description="Have the targeted creature block this attacker",value=str(position)) for position,attacker in game.false_orders_choices(spell.choice_owner)[:24])
        super().__init__(placeholder="Resolve False Orders",min_values=1,max_values=1,options=options,custom_id=f"mtg:{game_id}:false_orders")
    async def callback(self,i):
        value=self.values[0]; position=None if value=="decline" else int(value)
        await self.cog.act(i,self.game_id,lambda g:g.choose_false_orders(i.user.id,position),"false_orders_choice")

class BalanceSelect(discord.ui.Select):
    def __init__(self,cog,game_id,game):
        self.cog,self.game_id=cog,game_id; spell=game.stack[-1]; choices=game.balance_choices(spell,spell.choice_owner); required=game._balance_required(spell,spell.choice_owner); stage=spell.ability_effect.split("_",1)[1]
        options=[discord.SelectOption(label=f"{position}. {game.card(permanent.uid).name}"[:100],description=f"Keep this {stage[:-1]}",value=str(position)) for position,permanent in choices]
        super().__init__(placeholder=f"Choose exactly {required} {stage} to keep",min_values=required,max_values=required,options=options,custom_id=f"mtg:{game_id}:balance")
    async def callback(self,i):
        await self.cog.act(i,self.game_id,lambda g:g.choose_balance(i.user.id,[int(value) for value in self.values]),"balance_choice")

class KudzuSelect(discord.ui.Select):
    def __init__(self,cog,game_id,game):
        self.cog,self.game_id=cog,game_id; trigger=game.stack[-1]
        options=[discord.SelectOption(label="Decline to reattach",value="decline",description="Let Kudzu go to the graveyard if unattached")]
        options.extend(discord.SelectOption(label=f"{owner}:{position}. {game.card(permanent.uid).name}"[:100],description="Attach Kudzu to this land",value=f"{owner}:{position}") for owner,position,permanent in game.kudzu_choices(trigger)[:24])
        super().__init__(placeholder="Reattach Kudzu or decline",min_values=1,max_values=1,options=options,custom_id=f"mtg:{game_id}:kudzu")
    async def callback(self,i):
        value=self.values[0]
        if value=="decline": action=lambda g:g.choose_kudzu(i.user.id)
        else:
            owner,position=(int(part) for part in value.split(":")); action=lambda g:g.choose_kudzu(i.user.id,owner,position)
        await self.cog.act(i,self.game_id,action,"kudzu_choice")

class WordChangeSelect(discord.ui.Select):
    def __init__(self,cog,game_id,game):
        self.cog,self.game_id=cog,game_id; spell=game.stack[-1]; land=game.card(spell.uid).effect=="text_change_land"; labels={"W":"White","U":"Blue","B":"Black","R":"Red","G":"Green"}
        options=[discord.SelectOption(label=f"{source.title() if land else labels[source]} to {target.title() if land else labels[target]}",value=f"{source}:{target}") for source,target in game.word_change_choices()]
        super().__init__(placeholder="Choose the word replacement",min_values=1,max_values=1,options=options,custom_id=f"mtg:{game_id}:word_change")
    async def callback(self,i):
        source,target=self.values[0].split(":",1)
        await self.cog.act(i,self.game_id,lambda g:g.choose_word_change(i.user.id,source,target),"word_change_choice")

class LichSacrificeSelect(discord.ui.Select):
    def __init__(self,cog,game_id,game):
        self.cog,self.game_id=cog,game_id; trigger=game.stack[-1]; choices=game.lich_sacrifice_choices(trigger); required=trigger.choice_value
        options=[discord.SelectOption(label=f"{position}. {game.card(permanent.uid).name}"[:100],description="Sacrifice this nontoken permanent",value=str(position)) for position,permanent in choices]
        super().__init__(placeholder=f"Choose exactly {required} permanents for Lich",min_values=required,max_values=required,options=options,custom_id=f"mtg:{game_id}:lich_sacrifice")
    async def callback(self,i):
        await self.cog.act(i,self.game_id,lambda g:g.choose_lich_sacrifices(i.user.id,[int(value) for value in self.values]),"lich_sacrifice")

class CamouflageSelect(discord.ui.Select):
    def __init__(self,cog,game_id):
        self.cog,self.game_id=cog,game_id
        super().__init__(placeholder="Camouflage pile choice",options=[discord.SelectOption(label="Use empty piles",value="none",description="Declare no blockers through Camouflage")],custom_id=f"mtg:{game_id}:camouflage")
    async def callback(self,i):
        await self.cog.act(i,self.game_id,lambda g:g.choose_camouflage(i.user.id),"camouflage_no_blocks")

class RagingRiverSelect(discord.ui.Select):
    def __init__(self,cog,game_id,game):
        self.cog,self.game_id=cog,game_id; trigger=game.stack[-1]; choices=game.raging_river_choices(trigger)
        options=[discord.SelectOption(label="Finish; unselected creatures go right",value="none",description="Put every listed creature on the right")]
        kind="attacker" if trigger.ability_effect=="raging_river_attackers" else "nonflying creature"
        options.extend(discord.SelectOption(label=f"{position}. {game.card(permanent.uid).name}"[:100],value=str(position),description=f"Put this {kind} on the left"[:100]) for position,permanent in choices[:24])
        super().__init__(placeholder="Choose the Raging River left pile",min_values=1,max_values=len(options),options=options,custom_id=f"mtg:{game_id}:raging_river")
    async def callback(self,i):
        positions=[int(value) for value in self.values if value!="none"]
        await self.cog.act(i,self.game_id,lambda g:g.choose_raging_river(i.user.id,positions),"raging_river_choice")

class ForkTargetSelect(discord.ui.Select):
    def __init__(self,cog,game_id):
        self.cog,self.game_id=cog,game_id
        super().__init__(placeholder="Fork copy target choice",options=[discord.SelectOption(label="Keep original targets",value="keep",description="Resolve the copy with the copied targets")],custom_id=f"mtg:{game_id}:fork_target")
    async def callback(self,i):
        await self.cog.act(i,self.game_id,lambda g:g.choose_fork_target(i.user.id),"fork_keep_targets")

class AttackSelect(discord.ui.Select):
    def __init__(self,cog,game_id,game,private=False):
        self.cog,self.game_id,self.private=cog,game_id,private
        player=game.player(game.active_user)
        options=[discord.SelectOption(label=f"{position}. {game.card(permanent.uid).name}"[:100],description="Declare as an attacker",value=str(position)) for position,permanent in enumerate(player.battlefield,1) if game.can_attack_permanent(permanent)][:25]
        super().__init__(placeholder="Choose attackers",min_values=1,max_values=len(options),options=options,custom_id=f"mtg:{game_id}:declare_attackers")
    async def callback(self,interaction):
        await (self.cog.combat_hand_interaction(interaction,self.game_id,lambda game:game.declare_attackers(interaction.user.id,[int(value) for value in self.values]),"attack") if self.private else self.cog.act(interaction,self.game_id,lambda game:game.declare_attackers(interaction.user.id,[int(value) for value in self.values]),"attack"))

class NoAttackButton(discord.ui.Button):
    def __init__(self,cog,game_id,private=False):
        super().__init__(label="No attacks",style=discord.ButtonStyle.secondary,custom_id=f"mtg:{game_id}:no_attacks")
        self.cog,self.game_id,self.private=cog,game_id,private
    async def callback(self,interaction):
        action=lambda game:game.declare_attackers(interaction.user.id,[])
        await (self.cog.combat_hand_interaction(interaction,self.game_id,action,"attack_none") if self.private else self.cog.act(interaction,self.game_id,action,"attack_none"))

class BlockSelect(discord.ui.Select):
    def __init__(self,cog,game_id,game,private=False):
        self.cog,self.game_id,self.private=cog,game_id,private
        defender=game.player(game.opponent(game.active_user)); options=[]
        for attacker_position,attacker_uid in enumerate(game.attackers,1):
            attacker=game.find_permanent(attacker_uid)[1]
            if attacker is None: continue
            for blocker_position,blocker in enumerate(defender.battlefield,1):
                if game.can_block(attacker_uid,blocker.uid)[0]:
                    label=f"{game.card(blocker.uid).name} blocks {game.card(attacker.uid).name}"
                    options.append(discord.SelectOption(label=label[:100],description=f"Attacker {attacker_position} ← blocker {blocker_position}",value=f"{attacker_position}:{blocker_position}"))
        super().__init__(placeholder="Choose blocker assignments",min_values=1,max_values=min(25,len(options)),options=options[:25],custom_id=f"mtg:{game_id}:declare_blockers")
    async def callback(self,interaction):
        pairs=[tuple(int(part) for part in value.split(":")) for value in self.values]
        await (self.cog.combat_hand_interaction(interaction,self.game_id,lambda game:game.declare_blockers(interaction.user.id,pairs),"block") if self.private else self.cog.act(interaction,self.game_id,lambda game:game.declare_blockers(interaction.user.id,pairs),"block"))

class NoBlockButton(discord.ui.Button):
    def __init__(self,cog,game_id,private=False):
        super().__init__(label="No blocks",style=discord.ButtonStyle.secondary,custom_id=f"mtg:{game_id}:no_blocks")
        self.cog,self.game_id,self.private=cog,game_id,private
    async def callback(self,interaction):
        action=lambda game:game.declare_blockers(interaction.user.id,[])
        await (self.cog.combat_hand_interaction(interaction,self.game_id,action,"block_none") if self.private else self.cog.act(interaction,self.game_id,action,"block_none"))

class GameView(discord.ui.View):
    def __init__(self,cog,game_id):
        super().__init__(timeout=None); self.cog=cog; self.game_id=game_id
        game=cog.games.get(game_id)
        for item in self.children:
            item.custom_id=f"mtg:{game_id}:{item.custom_id}"
            action=item.custom_id.rsplit(":",1)[-1]
            if game and action in ("keep","mulligan"): item.disabled=game.phase!="opening"
            if game and action=="pass": item.disabled=game.priority_user is None or game.finished or game.phase in ("untap","cleanup_discard","camouflage") or game.turn_start_pending_user is not None or game.sanctuary_draw_pending or bool(game.stack and game.stack[-1].decision_pending)
            if game and action in ("pay","decline_trigger"):
                pending=bool(game.stack and game.stack[-1].decision_pending and not game.stack[-1].fork_retarget and (game.stack[-1].ability_effect or game.card(game.stack[-1].uid).effect=="power_sink"))
                mandatory=bool(pending and game.stack[-1].ability_effect in ("upkeep_sacrifice","opponent_land_sacrifice","tomb_cleanup","power_leak","vesuvan_copy","kudzu_move","balance_lands","balance_hand","balance_creatures","lich_damage","raging_river_split","raging_river_attackers"))
                item.disabled=not pending or mandatory
                if pending and action=="pay": item.label=game.trigger_accept_label(game.stack[-1])
                if pending and action=="decline_trigger" and not game.stack[-1].ability_effect: item.label="Don't pay"
            if game and action=="search":
                item.disabled=not bool(game.stack and game.stack[-1].decision_pending and not game.stack[-1].fork_retarget and not game.stack[-1].ability_effect and game.card(game.stack[-1].uid).effect=="search_library")
            if game and action=="private_hand":
                item.disabled=not bool(game.phase=="cleanup_discard" or (game.stack and game.stack[-1].decision_pending and game.stack[-1].ability_effect in ("discard_choice","look_hand","balance_hand","leng_discard","word_choose","mask_choose")))
            if game and action=="natural_selection":
                item.disabled=not bool(game.stack and game.stack[-1].decision_pending and not game.stack[-1].fork_retarget and not game.stack[-1].ability_effect and game.card(game.stack[-1].uid).effect=="natural_selection")
            if game and action=="vault_take": item.disabled=game.turn_start_pending_user is None
            if game and action in ("sanctuary_draw","sanctuary_skip"): item.disabled=not game.sanctuary_draw_pending or game.active_user is None
            if game and action=="concede": item.disabled=game.finished
        if game and game.stack and game.stack[-1].decision_pending and game.stack[-1].ability_effect=="lich_damage":
            choices=game.lich_sacrifice_choices(game.stack[-1]); required=game.stack[-1].choice_value
            if choices and len(choices)<=25 and 1<=required<=25: self.add_item(LichSacrificeSelect(self.cog,self.game_id,game))
        if game and game.phase=="camouflage": self.add_item(CamouflageSelect(self.cog,self.game_id))
        if game and game.turn_start_pending_user is not None and game.time_vault_choices(game.turn_start_pending_user): self.add_item(TimeVaultSelect(self.cog,self.game_id,game))
        if game and game.stack and game.stack[-1].decision_pending and game.stack[-1].ability_effect in ("upkeep_sacrifice","opponent_land_sacrifice") and game.trigger_sacrifice_choices(game.stack[-1]):
            self.add_item(SacrificeSelect(self.cog,self.game_id,game,game.stack[-1]))
        if game and game.stack and game.stack[-1].decision_pending and not game.stack[-1].fork_retarget and not game.stack[-1].ability_effect and game.card(game.stack[-1].uid).effect=="drain_power":
            self.add_item(DrainPowerSelect(self.cog,self.game_id,game))
        if game and game.stack and game.stack[-1].decision_pending and game.stack[-1].ability_effect=="power_leak":
            self.add_item(PowerLeakSelect(self.cog,self.game_id,game))
        if game and game.stack and game.stack[-1].decision_pending and not game.stack[-1].ability_effect and game.card(game.stack[-1].uid).enters_copy_types:
            self.add_item(CopySelect(self.cog,self.game_id,game))
        if game and game.stack and game.stack[-1].decision_pending and game.stack[-1].ability_effect=="vesuvan_copy":
            self.add_item(VesuvanSelect(self.cog,self.game_id,game))
        if game and game.stack and game.stack[-1].decision_pending and not game.stack[-1].fork_retarget and not game.stack[-1].ability_effect and game.card(game.stack[-1].uid).effect in ("text_change_land","text_change_color"):
            self.add_item(WordChangeSelect(self.cog,self.game_id,game))
        if game and game.stack and game.stack[-1].decision_pending and not game.stack[-1].fork_retarget and not game.stack[-1].ability_effect and game.card(game.stack[-1].uid).effect=="false_orders":
            self.add_item(FalseOrdersSelect(self.cog,self.game_id,game))
        if game and game.stack and game.stack[-1].decision_pending and game.stack[-1].is_copy and game.stack[-1].fork_retarget:
            self.add_item(ForkTargetSelect(self.cog,self.game_id))
        if game and game.stack and game.stack[-1].decision_pending and game.stack[-1].ability_effect in ("raging_river_split","raging_river_attackers") and len(game.raging_river_choices(game.stack[-1]))<=24:
            self.add_item(RagingRiverSelect(self.cog,self.game_id,game))
        if game and game.stack and game.stack[-1].decision_pending and game.stack[-1].ability_effect=="kudzu_move":
            self.add_item(KudzuSelect(self.cog,self.game_id,game))
        if game and game.stack and game.stack[-1].decision_pending and game.stack[-1].ability_effect in ("balance_lands","balance_creatures"):
            choices=game.balance_choices(game.stack[-1],game.stack[-1].choice_owner); required=game._balance_required(game.stack[-1],game.stack[-1].choice_owner)
            if choices and len(choices)<=25 and 1<=required<=25: self.add_item(BalanceSelect(self.cog,self.game_id,game))
        if game and game.phase=="attackers":
            eligible=[permanent for permanent in game.player(game.active_user).battlefield if game.can_attack_permanent(permanent)]
            if eligible: self.add_item(AttackSelect(self.cog,self.game_id,game))
            self.add_item(NoAttackButton(self.cog,self.game_id))
        if game and game.phase=="blockers":
            defender=game.player(game.opponent(game.active_user)); pairs=sum(game.can_block(attacker_uid,blocker.uid)[0] for attacker_uid in game.attackers for blocker in defender.battlefield)
            if 0<pairs<=25: self.add_item(BlockSelect(self.cog,self.game_id,game))
            self.add_item(NoBlockButton(self.cog,self.game_id))
        if game and game.phase=="untap" and game.untap_choices(): self.add_item(UntapSelect(self.cog,self.game_id,game))
    async def interaction_check(self,i):
        game=self.cog.games.get(self.game_id)
        if game and game.finished:
            await i.response.send_message("This match is over.",ephemeral=True); return False
        if game and i.user.id in game.order: return True
        await i.response.send_message("You are not a player in this match.",ephemeral=True); return False
    @discord.ui.button(label="View / play hand",style=discord.ButtonStyle.primary,custom_id="hand")
    async def hand(self,i,b):
        await i.response.defer(ephemeral=True,thinking=True)
        await self.cog.send_hand(i,self.game_id,0,editing=False)
    @discord.ui.button(label="History",style=discord.ButtonStyle.secondary,custom_id="history")
    async def history(self,i,b):
        await i.response.defer(ephemeral=True,thinking=True)
        await self.cog.send_history(i,self.game_id,0,editing=False)
    @discord.ui.button(label="Search library",style=discord.ButtonStyle.secondary,custom_id="search")
    async def search(self,i,b):
        await i.response.defer(ephemeral=True,thinking=True)
        await self.cog.send_library_search(i,self.game_id,0,editing=False)
    @discord.ui.button(label="Private hand choice",style=discord.ButtonStyle.secondary,custom_id="private_hand")
    async def private_hand(self,i,b):
        await i.response.defer(ephemeral=True,thinking=True)
        await self.cog.send_private_hand_decision(i,self.game_id,0,editing=False)
    @discord.ui.button(label="Private library choice",style=discord.ButtonStyle.secondary,custom_id="natural_selection")
    async def natural_selection(self,i,b):
        await i.response.defer(ephemeral=True,thinking=True)
        await self.cog.send_natural_selection(i,self.game_id,editing=False)
    @discord.ui.button(label="Keep hand",style=discord.ButtonStyle.success,custom_id="keep")
    async def keep(self,i,b): await self.cog.act(i,self.game_id,lambda g:g.mulligan(i.user.id,True),"keep")
    @discord.ui.button(label="Mulligan",style=discord.ButtonStyle.secondary,custom_id="mulligan")
    async def mulligan(self,i,b): await self.cog.act(i,self.game_id,lambda g:g.mulligan(i.user.id,False),"mulligan")
    @discord.ui.button(label="Pass priority",style=discord.ButtonStyle.primary,custom_id="pass")
    async def pass_turn(self,i,b): await self.cog.act(i,self.game_id,lambda g:g.pass_priority(i.user.id),"pass")
    @discord.ui.button(label="Pay {1}",style=discord.ButtonStyle.success,custom_id="pay")
    async def pay_trigger(self,i,b): await self.cog.act(i,self.game_id,lambda g:g.choose_trigger(i.user.id,True),"trigger_pay")
    @discord.ui.button(label="Decline trigger",style=discord.ButtonStyle.secondary,custom_id="decline_trigger")
    async def decline_trigger(self,i,b): await self.cog.act(i,self.game_id,lambda g:g.choose_trigger(i.user.id,False),"trigger_decline")
    @discord.ui.button(label="Take turn",style=discord.ButtonStyle.primary,custom_id="vault_take")
    async def vault_take(self,i,b): await self.cog.act(i,self.game_id,lambda g:g.choose_time_vault_turn(i.user.id,False),"vault_take")
    @discord.ui.button(label="Sanctuary: Draw",style=discord.ButtonStyle.secondary,custom_id="sanctuary_draw")
    async def sanctuary_draw(self,i,b): await self.cog.act(i,self.game_id,lambda g:g.choose_sanctuary_draw(i.user.id,False),"sanctuary_draw")
    @discord.ui.button(label="Sanctuary: Skip",style=discord.ButtonStyle.success,custom_id="sanctuary_skip")
    async def sanctuary_skip(self,i,b): await self.cog.act(i,self.game_id,lambda g:g.choose_sanctuary_draw(i.user.id,True),"sanctuary_skip")
    @discord.ui.button(label="Concede",style=discord.ButtonStyle.danger,custom_id="concede")
    async def concede(self,i,b): await self.cog.act(i,self.game_id,lambda g:g.concede(i.user.id),"concede")

def _mana_help(cost):
    names={"W":"white","U":"blue","B":"black","R":"red","G":"green","C":"colorless"}; parts=[]
    for symbol in cost.replace("{","").split("}"):
        if not symbol: continue
        if symbol.isdigit(): parts.append(f"{symbol} mana of any type")
        elif symbol=="X": parts.append("X mana")
        else: parts.append(f"1 {names.get(symbol,symbol)} mana")
    return " + ".join(parts) or "no mana cost"

def _needs_target(card):
    return bool(card.effect in TARGETED_EFFECTS or card.aura_target_types)

def _playable_hand_entries(game,user,page):
    start=page*HAND_PAGE_SIZE; entries=[]
    for offset,card in enumerate(game.hand(user)[start:start+HAND_PAGE_SIZE],start+1):
        if card.land:
            legal=user==game.active_user and game.phase in ("precombat_main","postcombat_main") and not game.stack and game.can_play_land(user)
        else:
            legal=(card.kind=="Instant" or (user==game.active_user and game.phase in ("precombat_main","postcombat_main") and not game.stack)) and game.can_pay(user,card,0)
            if card.effect=="berserk": legal=legal and game.phase in ("upkeep","draw","precombat_main","after_attackers","after_blockers","after_first_strike")
            elif card.effect=="camouflage": legal=legal and game.phase=="after_attackers" and user==game.active_user
            elif card.effect=="blaze_of_glory": legal=legal and game.phase=="after_attackers" and user!=game.active_user
            elif card.effect=="false_orders": legal=legal and game.phase=="after_blockers"
            elif card.effect=="siren_call": legal=legal and user!=game.active_user and game.phase in ("upkeep","draw","precombat_main")
            if legal and _needs_target(card): legal=_target(game,user,card) is not None
        if legal: entries.append((offset,card))
    return entries

class PlayCardModal(discord.ui.Modal):
    def __init__(self,browser,position,card,suggested_target=None):
        super().__init__(title=f"Play {card.name}"[:45]); self.browser,self.position=browser,position; self.target_input=self.x_input=None
        if _needs_target(card):
            self.target_input=discord.ui.TextInput(label="Target",placeholder="A suggested legal target is filled in",default=suggested_target,required=True,max_length=200)
            self.add_item(self.target_input)
        if "{X}" in card.mana_cost:
            self.x_input=discord.ui.TextInput(label="X value",placeholder="Enter zero or a larger whole number",required=True,max_length=8)
            self.add_item(self.x_input)
    async def on_submit(self,interaction):
        target=str(self.target_input).strip() if self.target_input else None; raw_x=str(self.x_input).strip() if self.x_input else ""
        try: x_value=int(raw_x) if raw_x else None
        except ValueError:
            await interaction.response.send_message("X must be a whole number of zero or more.",ephemeral=True); return
        await self.browser.cog.play_hand_interaction(interaction,self.browser.game_id,self.position,target,x_value)

class HandPlaySelect(discord.ui.Select):
    def __init__(self,browser,entries):
        self.browser=browser
        options=[discord.SelectOption(label=f"{position}. {card.name}"[:100],description=f"{card.kind} · {_mana_help(card.mana_cost)}"[:100],value=str(position)) for position,card in entries]
        super().__init__(placeholder="Choose a currently playable card",min_values=1,max_values=1,options=options,row=0)
    async def callback(self,interaction):
        position=int(self.values[0]); game=self.browser.cog.games.get(self.browser.game_id)
        if not game: await interaction.response.send_message("This match is unavailable.",ephemeral=True); return
        hand=game.hand(self.browser.user_id)
        if not 1<=position<=len(hand): await interaction.response.send_message("That hand view is stale; open your hand again.",ephemeral=True); return
        card=hand[position-1]; needs_input=_needs_target(card) or "{X}" in card.mana_cost
        if not needs_input: await self.browser.cog.play_hand_interaction(interaction,self.browser.game_id,position,None,None)
        else: await interaction.response.send_modal(PlayCardModal(self.browser,position,card,_target(game,self.browser.user_id,card) if _needs_target(card) else None))

class OpeningHandButton(discord.ui.Button):
    def __init__(self,browser,keep):
        super().__init__(label="Keep hand" if keep else "Mulligan",style=discord.ButtonStyle.success if keep else discord.ButtonStyle.secondary,row=0)
        self.browser,self.keep=browser,keep
    async def callback(self,interaction):
        await self.browser.cog.opening_hand_interaction(interaction,self.browser.game_id,self.keep)

class HandPassButton(discord.ui.Button):
    def __init__(self,browser):
        super().__init__(label="Pass priority",style=discord.ButtonStyle.primary,row=2)
        self.browser=browser
    async def callback(self,interaction):
        await self.browser.cog.pass_hand_interaction(interaction,self.browser.game_id)

class HandPaginationView(discord.ui.View):
    def __init__(self,cog,game_id,user_id,page,pages):
        super().__init__(timeout=180)
        self.cog,self.game_id,self.user_id,self.page,self.pages=cog,game_id,user_id,page,pages
        game=cog.games.get(game_id); entries=_playable_hand_entries(game,user_id,page) if game and game.priority_user==user_id and game.phase!="opening" else []
        self.playable_count=len(entries)
        if game and game.phase=="opening" and not game.player(user_id).kept:
            self.add_item(OpeningHandButton(self,True)); self.add_item(OpeningHandButton(self,False))
        elif entries: self.add_item(HandPlaySelect(self,entries))
        if game and game.phase=="attackers" and game.active_user==user_id:
            eligible=[permanent for permanent in game.player(user_id).battlefield if game.can_attack_permanent(permanent)]
            if eligible: self.add_item(AttackSelect(cog,game_id,game,True))
            self.add_item(NoAttackButton(cog,game_id,True))
        if game and game.phase=="blockers" and game.opponent(game.active_user)==user_id:
            defender=game.player(user_id); pairs=sum(game.can_block(attacker_uid,blocker.uid)[0] for attacker_uid in game.attackers for blocker in defender.battlefield)
            if 0<pairs<=25: self.add_item(BlockSelect(cog,game_id,game,True))
            self.add_item(NoBlockButton(cog,game_id,True))
        if game and game.phase!="opening" and game.priority_user==user_id and game.phase not in ("untap","cleanup_discard","camouflage") and game.turn_start_pending_user is None and not game.sanctuary_draw_pending and not (game.stack and game.stack[-1].decision_pending):
            self.add_item(HandPassButton(self))
        self.previous.disabled=page<=0
        self.next.disabled=page>=pages-1
    async def interaction_check(self,i):
        if i.user.id==self.user_id: return True
        await i.response.send_message("This private hand belongs to another player.",ephemeral=True); return False
    @discord.ui.button(label="Previous",style=discord.ButtonStyle.secondary,row=1)
    async def previous(self,i,b):
        await i.response.defer()
        await self.cog.send_hand(i,self.game_id,self.page-1,editing=True)
    @discord.ui.button(label="Next",style=discord.ButtonStyle.secondary,row=1)
    async def next(self,i,b):
        await i.response.defer()
        await self.cog.send_hand(i,self.game_id,self.page+1,editing=True)


class LibrarySelect(discord.ui.Select):
    def __init__(self,browser,game):
        self.browser=browser; start=browser.page*browser.page_size
        entries=game.library_search(browser.user_id)[start:start+browser.page_size]
        options=[discord.SelectOption(label=f"{position}. {card.name}"[:100],description=f"{card.kind} - {card.mana_cost or 'no mana cost'}"[:100],value=str(position)) for position,card in entries]
        super().__init__(placeholder="Choose a card for Demonic Tutor",min_values=1,max_values=1,options=options,row=0)
    async def callback(self,interaction):
        await self.browser.cog.choose_library_interaction(interaction,self.browser.game_id,int(self.values[0]))

class LibrarySearchView(discord.ui.View):
    page_size=25
    def __init__(self,cog,game_id,user_id,page,pages,game):
        super().__init__(timeout=300); self.cog,self.game_id,self.user_id,self.page,self.pages=cog,game_id,user_id,page,pages
        self.add_item(LibrarySelect(self,game)); self.previous.disabled=page<=0; self.next.disabled=page>=pages-1
    async def interaction_check(self,interaction):
        if interaction.user.id==self.user_id: return True
        await interaction.response.send_message("This private library search belongs to another player.",ephemeral=True); return False
    @discord.ui.button(label="Previous",style=discord.ButtonStyle.secondary,row=1)
    async def previous(self,interaction,button):
        await interaction.response.defer(); await self.cog.send_library_search(interaction,self.game_id,self.page-1,editing=True)
    @discord.ui.button(label="Next",style=discord.ButtonStyle.secondary,row=1)
    async def next(self,interaction,button):
        await interaction.response.defer(); await self.cog.send_library_search(interaction,self.game_id,self.page+1,editing=True)

class NaturalSelectionSelect(discord.ui.Select):
    def __init__(self,browser,entries):
        options=[]; size=len(entries)
        for order in permutations(range(1,size+1)):
            names=" / ".join(entries[position-1][1].name for position in order)
            options.append(discord.SelectOption(label=("Order "+"-".join(map(str,order)))[:100],description=names[:100],value=",".join(map(str,order))))
        options.append(discord.SelectOption(label="Shuffle the library",description="Use Natural Selection's optional shuffle",value="shuffle"))
        super().__init__(placeholder="Choose the new top-to-bottom order or shuffle",min_values=1,max_values=1,options=options)
        self.browser=browser
    async def callback(self,interaction):
        value=self.values[0]; order=None if value=="shuffle" else tuple(int(item) for item in value.split(","))
        await self.browser.cog.complete_natural_selection(interaction,self.browser.game_id,order,value=="shuffle")

class NaturalSelectionView(discord.ui.View):
    def __init__(self,cog,game_id,user_id,entries):
        super().__init__(timeout=300); self.cog,self.game_id,self.user_id=cog,game_id,user_id; self.add_item(NaturalSelectionSelect(self,entries))
    async def interaction_check(self,interaction):
        if interaction.user.id==self.user_id: return True
        await interaction.response.send_message("This private library choice belongs to another player.",ephemeral=True); return False

class PrivateHandSelect(discord.ui.Select):
    def __init__(self,browser,entries):
        self.browser=browser; start=browser.page*browser.page_size
        visible=entries[start:start+browser.page_size]
        options=[discord.SelectOption(label=f"{position}. {card.name}"[:100],description=f"{card.kind} - {card.mana_cost or 'no mana cost'}"[:100],value=str(position)) for position,card in visible]
        super().__init__(placeholder="Choose an eligible creature" if browser.effect=="mask_choose" else "Choose a card to play" if browser.effect=="word_choose" else "Choose a card to discard",min_values=1,max_values=1,options=options,row=0)
    async def callback(self,interaction):
        await self.browser.cog.complete_private_hand_interaction(interaction,self.browser.game_id,int(self.values[0]))

class DiscardDestinationSelect(discord.ui.Select):
    def __init__(self,browser):
        self.browser=browser
        options=[discord.SelectOption(label="Top of library",description="Use Library of Leng",value="library"),discord.SelectOption(label="Graveyard",description="Decline the replacement",value="graveyard")]
        super().__init__(placeholder="Choose where the discarded card goes",min_values=1,max_values=1,options=options,row=0)
    async def callback(self,interaction):
        await self.browser.cog.complete_discard_destination(interaction,self.browser.game_id,self.values[0]=="library")

class BalanceHandSelect(discord.ui.Select):
    def __init__(self,browser,entries,required):
        self.browser=browser; start=browser.page*browser.page_size; visible=entries[start:start+browser.page_size]
        options=[discord.SelectOption(label=f"{position}. {card.name}"[:100],description=f"{card.kind} - {card.mana_cost or 'no mana cost'}"[:100],value=str(position)) for position,card in visible]
        super().__init__(placeholder=f"Choose exactly {required} cards to discard",min_values=required,max_values=required,options=options,row=0)
    async def callback(self,interaction):
        await self.browser.cog.complete_private_hand_interaction(interaction,self.browser.game_id,[int(value) for value in self.values])

class PrivateHandDecisionView(discord.ui.View):
    page_size=25
    def __init__(self,cog,game_id,user_id,page,pages,effect,entries):
        super().__init__(timeout=300); self.cog,self.game_id,self.user_id,self.page,self.pages,self.effect=cog,game_id,user_id,page,pages,effect
        if effect in ("discard_choice","cleanup_discard","word_choose","mask_choose"): self.add_item(PrivateHandSelect(self,entries))
        if effect=="balance_hand":
            game=cog.games[game_id]; required=game._balance_required(game.stack[-1],user_id)
            if pages==1 and 1<=required<=25: self.add_item(BalanceHandSelect(self,entries,required))
        if effect=="leng_discard":
            self.add_item(DiscardDestinationSelect(self))
        self.previous.disabled=page<=0; self.next.disabled=page>=pages-1
        self.done.disabled=effect not in ("look_hand","mask_choose")
        if effect=="mask_choose": self.done.label="Decline"
    async def interaction_check(self,interaction):
        if interaction.user.id==self.user_id: return True
        await interaction.response.send_message("This private hand decision belongs to another player.",ephemeral=True); return False
    @discord.ui.button(label="Previous",style=discord.ButtonStyle.secondary,row=1)
    async def previous(self,interaction,button):
        await interaction.response.defer(); await self.cog.send_private_hand_decision(interaction,self.game_id,self.page-1,editing=True)
    @discord.ui.button(label="Next",style=discord.ButtonStyle.secondary,row=1)
    async def next(self,interaction,button):
        await interaction.response.defer(); await self.cog.send_private_hand_decision(interaction,self.game_id,self.page+1,editing=True)
    @discord.ui.button(label="Done viewing",style=discord.ButtonStyle.success,row=1)
    async def done(self,interaction,button):
        await self.cog.complete_private_hand_interaction(interaction,self.game_id,None)

class CatalogSelect(discord.ui.Select):
    def __init__(self, browser):
        self.browser=browser
        start=browser.page*browser.page_size
        options=[]
        for index,(source,card) in enumerate(browser.records[start:start+browser.page_size],start):
            if source=="alpha":
                status="Playable" if card.engine_status=="playable" else "Reference only"
                description=f"{card.type_line} · {status}"
            else:
                description=f"{card.kind} · Playable"
            name=f"{card.name} · #{card.collector_number}" if source=="alpha" else card.name
            options.append(discord.SelectOption(label=f"{index+1}. {name}"[:100],description=description[:100],value=str(index)))
        super().__init__(placeholder="Select a card for details",min_values=1,max_values=1,options=options,row=0)

    async def callback(self,interaction):
        await interaction.response.defer()
        await self.browser.cog.show_catalog_detail(interaction,self.browser,int(self.values[0]))


class CatalogView(discord.ui.View):
    page_size=15
    def __init__(self,cog,user_id,records,scope,search,page=0):
        super().__init__(timeout=300)
        self.cog,self.user_id,self.records,self.scope,self.search=cog,user_id,records,scope,search
        self.pages=max(1,(len(records)+self.page_size-1)//self.page_size)
        self.page=max(0,min(page,self.pages-1))
        self.add_item(CatalogSelect(self))
        self.previous_page.disabled=self.page<=0
        self.next_page.disabled=self.page>=self.pages-1

    async def interaction_check(self,interaction):
        if interaction.user.id==self.user_id: return True
        await interaction.response.send_message("This catalog browser belongs to another member.",ephemeral=True); return False

    @discord.ui.button(label="Previous page",emoji="◀️",style=discord.ButtonStyle.secondary,row=1)
    async def previous_page(self,interaction,button):
        await self.cog.show_catalog_page(interaction,self,self.page-1)

    @discord.ui.button(label="Next page",emoji="▶️",style=discord.ButtonStyle.secondary,row=1)
    async def next_page(self,interaction,button):
        await self.cog.show_catalog_page(interaction,self,self.page+1)


class CatalogDetailView(discord.ui.View):
    def __init__(self,browser,index):
        super().__init__(timeout=300)
        self.browser,self.index=browser,index
        self.previous_card.disabled=index<=0
        self.next_card.disabled=index>=len(browser.records)-1

    async def interaction_check(self,interaction):
        if interaction.user.id==self.browser.user_id: return True
        await interaction.response.send_message("This catalog browser belongs to another member.",ephemeral=True); return False

    @discord.ui.button(label="Previous card",emoji="◀️",style=discord.ButtonStyle.secondary)
    async def previous_card(self,interaction,button):
        await interaction.response.defer()
        await self.browser.cog.show_catalog_detail(interaction,self.browser,self.index-1)

    @discord.ui.button(label="Up to catalog",emoji="⬆️",style=discord.ButtonStyle.primary)
    async def up(self,interaction,button):
        await self.browser.cog.show_catalog_page(interaction,self.browser,self.browser.page)

    @discord.ui.button(label="Next card",emoji="▶️",style=discord.ButtonStyle.secondary)
    async def next_card(self,interaction,button):
        await interaction.response.defer()
        await self.browser.cog.show_catalog_detail(interaction,self.browser,self.index+1)
