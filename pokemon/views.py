import discord

from .data import MOVES, SPECIES


STARTERS = (1,4,7)


class StarterView(discord.ui.View):
    def __init__(self,cog,user_id,encounter_id=None,setup_hint=None):
        super().__init__(timeout=180)
        self.cog=cog;self.user_id=user_id;self.encounter_id=encounter_id;self.setup_hint=setup_hint;self.selected=0
        self._sync_choose_label()

    @property
    def species_id(self):return STARTERS[self.selected]

    def _sync_choose_label(self):self.choose.label=f"Choose {SPECIES[self.species_id].name}"

    def cycle(self,offset):
        self.selected=(self.selected+offset)%len(STARTERS);self._sync_choose_label()
        return self.species_id

    async def interaction_check(self,interaction):
        if interaction.user.id==self.user_id:return True
        await interaction.response.send_message("This starter choice belongs to another trainer.",ephemeral=True)
        return False

    async def refresh(self,interaction):
        embed,files=await self.cog.rendered_starter_choice(interaction.user,self.selected,self.setup_hint)
        await interaction.response.edit_message(embed=embed,attachments=files,view=self)

    @discord.ui.button(label="◀",style=discord.ButtonStyle.secondary,custom_id="pokemon:starter:previous")
    async def previous(self,interaction,button):
        self.cycle(-1);await self.refresh(interaction)

    @discord.ui.button(label="Choose",emoji="✅",style=discord.ButtonStyle.success,custom_id="pokemon:starter:choose")
    async def choose(self,interaction,button):
        await self.cog.choose_starter(interaction,self.species_id,self.encounter_id)

    @discord.ui.button(label="▶",style=discord.ButtonStyle.secondary,custom_id="pokemon:starter:next")
    async def next(self,interaction,button):
        self.cycle(1);await self.refresh(interaction)


class MainMenuButton(discord.ui.Button):
    def __init__(self,cog,user_id,section,label,emoji,row=0,style=discord.ButtonStyle.secondary):
        super().__init__(label=label,emoji=emoji,style=style,row=row,custom_id=f"pokemon:menu:{section}")
        self.cog=cog;self.user_id=user_id;self.section=section

    async def callback(self,interaction):
        await self.cog.open_menu_section(interaction,self.section)


class MainMenuView(discord.ui.View):
    ITEMS=(
        ("party","Party","👥",0),("collection","Collection","📦",0),("pokedex","Pokedex","📕",0),("bag","Bag","🎒",0),("research","Research","📋",0),
        ("profile","Profile","🪪",1),("achievements","Goals","🏆",1),("gym","Gyms","🎖️",1),("mart","Mart","🛒",1),("style","Switch Style","🎨",1),
        ("trade","Trade","🔄",2),
    )
    def __init__(self,cog,user_id):
        super().__init__(timeout=180);self.cog=cog;self.user_id=user_id
        for section,label,emoji,row in self.ITEMS:
            style=discord.ButtonStyle.primary if section=="style" else discord.ButtonStyle.secondary
            self.add_item(MainMenuButton(cog,user_id,section,label,emoji,row,style))

    async def interaction_check(self,interaction):
        if interaction.user.id==self.user_id:return True
        await interaction.response.send_message("This Pokémon menu belongs to another trainer.",ephemeral=True);return False


class GymChallengeView(discord.ui.View):
    def __init__(self,cog,user_id,leader):
        super().__init__(timeout=180);self.cog=cog;self.user_id=user_id;self.challenge.label=f"Challenge {leader}"[:80]

    async def interaction_check(self,interaction):
        if interaction.user.id==self.user_id:return True
        await interaction.response.send_message("This Gym challenge belongs to another trainer.",ephemeral=True);return False

    @discord.ui.button(label="Challenge Gym Leader",emoji="⚔️",style=discord.ButtonStyle.danger)
    async def challenge(self,interaction,button):await self.cog.gym_challenge_interaction(interaction)


class CenterCollectView(discord.ui.View):
    def __init__(self,user_id):
        super().__init__(timeout=180);self.user_id=user_id

    async def interaction_check(self,interaction):
        if interaction.user.id==self.user_id:return True
        await interaction.response.send_message("This healed party belongs to another trainer.",ephemeral=True);return False

    @discord.ui.button(label="Collect Party",emoji="✨",style=discord.ButtonStyle.success,custom_id="pokemon:center:collect")
    async def collect(self,interaction,button):
        name=getattr(interaction.user,"display_name","Trainer")
        await interaction.response.edit_message(content=f"**{name}** collected their fully restored party.",view=None)


class TradeCollectionView(discord.ui.View):
    def __init__(self,cog,user_id,trainer,page,pages):
        super().__init__(timeout=180);self.cog=cog;self.user_id=user_id;self.trainer=trainer;self.page=page;self.pages=pages
        self.previous.disabled=page<=1;self.next.disabled=page>=pages

    async def interaction_check(self,interaction):
        if interaction.user.id==self.user_id:return True
        await interaction.response.send_message("This trade browser belongs to another trainer.",ephemeral=True);return False

    async def show(self,interaction,page):
        embed,files,page,pages,items=await self.cog.rendered_collection(self.trainer,page,manage=False)
        await interaction.response.edit_message(embed=embed,attachments=files,view=TradeCollectionView(self.cog,self.user_id,self.trainer,page,pages))

    @discord.ui.button(label="Previous",emoji="◀️",style=discord.ButtonStyle.secondary)
    async def previous(self,interaction,button):await self.show(interaction,self.page-1)

    @discord.ui.button(label="Next",emoji="▶️",style=discord.ButtonStyle.secondary)
    async def next(self,interaction,button):await self.show(interaction,self.page+1)


class TradeView(discord.ui.View):
    def __init__(self,cog,trade_id,offerer_id,recipient_id):
        super().__init__(timeout=None);self.cog=cog;self.trade_id=trade_id;self.offerer_id=offerer_id;self.recipient_id=recipient_id
        self.accept.custom_id=f"pokemon:trade:{trade_id}:accept";self.decline.custom_id=f"pokemon:trade:{trade_id}:decline"

    async def interaction_check(self,interaction):
        if interaction.user.id in {self.offerer_id,self.recipient_id}:return True
        await interaction.response.send_message("This trade belongs to two other trainers.",ephemeral=True);return False

    @discord.ui.button(label="Accept Trade",emoji="✅",style=discord.ButtonStyle.success,custom_id="pokemon:trade:accept")
    async def accept(self,interaction,button):
        if interaction.user.id!=self.recipient_id:
            await interaction.response.send_message("Only the receiving trainer can accept this trade.",ephemeral=True);return
        await self.cog.accept_trade(interaction,self.trade_id)

    @discord.ui.button(label="Decline / Cancel",emoji="✖️",style=discord.ButtonStyle.danger,custom_id="pokemon:trade:decline")
    async def decline(self,interaction,button):await self.cog.cancel_trade(interaction,self.trade_id)


class CollectionPokemonSelect(discord.ui.Select):
    def __init__(self,cog,user_id,items):
        options=[]
        for number,item in items:
            species=SPECIES[item["species_id"]];name=item.get("nickname") or species.name
            party=f" · P{item['party_slot']}" if item.get("party_slot") else ""
            options.append(discord.SelectOption(label=f"{number}. {name} · Lv.{item['level']}{party}",value=item["instance_id"]))
        super().__init__(placeholder="Select a Pokémon for your party…",options=options,row=0)
        self.cog=cog;self.user_id=user_id
    async def callback(self,interaction):
        await self.cog.collection_party_choice(interaction,self.values[0])

class CollectionBrowserView(discord.ui.View):
    def __init__(self,cog,user_id,page,pages,items):
        super().__init__(timeout=180);self.cog=cog;self.user_id=user_id;self.page=page;self.pages=pages
        if items:self.add_item(CollectionPokemonSelect(cog,user_id,items))
        self.previous.disabled=page<=1;self.next.disabled=page>=pages
    async def interaction_check(self,interaction):
        if interaction.user.id==self.user_id:return True
        await interaction.response.send_message("This collection belongs to another trainer.",ephemeral=True);return False
    async def refresh(self,interaction,page):
        embed,files,page,pages,items=await self.cog.rendered_collection(interaction.user,page)
        await interaction.response.edit_message(embed=embed,attachments=files,view=CollectionBrowserView(self.cog,self.user_id,page,pages,items))
    @discord.ui.button(label="Previous",style=discord.ButtonStyle.secondary,row=1)
    async def previous(self,interaction,button):await self.refresh(interaction,self.page-1)
    @discord.ui.button(label="Next",style=discord.ButtonStyle.secondary,row=1)
    async def next(self,interaction,button):await self.refresh(interaction,self.page+1)

class PartyPlacementButton(discord.ui.Button):
    def __init__(self,cog,user_id,identity,target,label,row,source_message=None):
        super().__init__(label=label,style=discord.ButtonStyle.success if target is None else discord.ButtonStyle.primary,row=row)
        self.cog=cog;self.user_id=user_id;self.identity=identity;self.target=target;self.source_message=source_message
    async def callback(self,interaction):
        await self.cog.place_collection_pokemon(interaction,self.identity,self.target,self.source_message)

class PartyPlacementView(discord.ui.View):
    def __init__(self,cog,user_id,identity,party,owned,source_message=None):
        super().__init__(timeout=120);self.user_id=user_id
        moving=identity in party
        if not moving and len(party)<6:self.add_item(PartyPlacementButton(cog,user_id,identity,None,"Add to open slot",0,source_message))
        for index,target in enumerate(party):
            raw=owned.get(target);name=(raw.get("nickname") or SPECIES[raw["species_id"]].name) if raw else "Empty";level=f" · Lv.{raw['level']}" if raw else ""
            if target==identity:continue
            action="Move to" if moving else "Replace"
            label=f"{action} {index+1}: {name}{level}"
            self.add_item(PartyPlacementButton(cog,user_id,identity,target,label[:80],1+index//3,source_message))
    async def interaction_check(self,interaction):
        if interaction.user.id==self.user_id:return True
        await interaction.response.send_message("This party choice belongs to another trainer.",ephemeral=True);return False


class MoveForgetSelect(discord.ui.Select):
    def __init__(self,cog,user_id,pokemon,new_move):
        options=[discord.SelectOption(label=f"Forget {MOVES[key].name}",value=key,description=f"Replace it with {MOVES[new_move].name}") for key in pokemon.moves]
        super().__init__(placeholder=f"Choose a move to forget for {MOVES[new_move].name}…",options=options,custom_id=f"pokemon:learn:{new_move}:replace")
        self.cog=cog;self.user_id=user_id;self.identity=pokemon.instance_id;self.new_move=new_move
    async def callback(self,interaction):await self.cog.resolve_move_choice(interaction,self.identity,self.new_move,self.values[0])

class MoveLearnView(discord.ui.View):
    def __init__(self,cog,user_id,pokemon,new_move):
        super().__init__(timeout=300);self.cog=cog;self.user_id=user_id;self.identity=pokemon.instance_id;self.new_move=new_move
        self.add_item(MoveForgetSelect(cog,user_id,pokemon,new_move))
        self.give_up.custom_id=f"pokemon:learn:{new_move}:cancel"
    async def interaction_check(self,interaction):
        if interaction.user.id==self.user_id:return True
        await interaction.response.send_message("This move choice belongs to another trainer.",ephemeral=True);return False
    @discord.ui.button(label="Give up learning it",style=discord.ButtonStyle.secondary,row=1)
    async def give_up(self,interaction,button):await self.cog.resolve_move_choice(interaction,self.identity,self.new_move,None)

class EncounterView(discord.ui.View):
    def __init__(self, cog, encounter_id):
        super().__init__(timeout=None)
        self.cog = cog
        self.encounter_id = encounter_id
        self.claim.custom_id = f"pokemon:{encounter_id}:claim"

    @discord.ui.button(label="Encounter", emoji="⚔️", style=discord.ButtonStyle.success, custom_id="claim")
    async def claim(self, interaction, button):
        await self.cog.claim(interaction, self.encounter_id)


class BattleMenu(discord.ui.View):
    def __init__(self, cog, encounter_id):
        super().__init__(timeout=None)
        self.cog = cog
        self.encounter_id = encounter_id

    async def interaction_check(self, interaction):
        battle = self.cog.battles.get(self.encounter_id)
        if battle and battle.user_id == interaction.user.id:
            return True
        await interaction.response.send_message("This is not your encounter.", ephemeral=True)
        return False


class BattleView(BattleMenu):
    def __init__(self, cog, encounter_id):
        super().__init__(cog, encounter_id)
        battle=self.cog.battles.get(encounter_id)
        if battle and battle.battle_kind=="gym":
            self.remove_item(self.bag)
        if battle and battle.needs_switch:
            self.fight.disabled=True
        for item in self.children:
            item.custom_id = f"pokemon:{encounter_id}:root:{item.custom_id}"

    @discord.ui.button(label="Fight", emoji="⚔️", style=discord.ButtonStyle.danger, custom_id="fight")
    async def fight(self, interaction, button):
        await interaction.response.edit_message(view=FightView(self.cog, self.encounter_id))

    @discord.ui.button(label="Pokémon", emoji="🔄", style=discord.ButtonStyle.primary, custom_id="party")
    async def party(self, interaction, button):
        await interaction.response.edit_message(view=PartyView(self.cog, self.encounter_id))

    @discord.ui.button(label="Bag", emoji="🎒", style=discord.ButtonStyle.success, custom_id="bag")
    async def bag(self, interaction, button):
        await self.cog.open_battle_bag(interaction,self.encounter_id)

    @discord.ui.button(label="Run", style=discord.ButtonStyle.secondary, custom_id="run")
    async def run(self, interaction, button):
        await self.cog.battle_action(interaction, self.encounter_id, lambda battle: battle.run())


class MoveButton(discord.ui.Button):
    def __init__(self, cog, encounter_id, index, move_key, disabled):
        move = MOVES[move_key]
        battle = cog.battles[encounter_id]
        pp = battle.player.move_pp.get(move_key, move.pp)
        super().__init__(
            label=f"{move.name} · {pp} PP",
            style=discord.ButtonStyle.danger,
            custom_id=f"pokemon:{encounter_id}:move:{index}",
            row=index // 2,
            disabled=disabled,
        )
        self.cog = cog
        self.encounter_id = encounter_id
        self.index = index

    async def callback(self, interaction):
        await self.cog.battle_action(
            interaction, self.encounter_id, lambda battle: battle.use_move(self.index)
        )


class FightView(BattleMenu):
    def __init__(self, cog, encounter_id):
        super().__init__(cog, encounter_id)
        battle = cog.battles.get(encounter_id)
        if battle:
            for index, move_key in enumerate(battle.player.moves[:4]):
                pp = battle.player.move_pp.get(move_key, MOVES[move_key].pp)
                self.add_item(MoveButton(cog, encounter_id, index, move_key, pp <= 0))
        self.add_item(BackButton(cog, encounter_id, row=2))


class PartyButton(discord.ui.Button):
    def __init__(self, cog, encounter_id, index, pokemon, hp, current):
        super().__init__(
            label=f"{index + 1}. {SPECIES[pokemon.species_id].name} · {hp} HP",
            style=discord.ButtonStyle.primary,
            custom_id=f"pokemon:{encounter_id}:party:{index}",
            row=index // 2,
            disabled=current or hp <= 0,
        )
        self.cog = cog
        self.encounter_id = encounter_id
        self.index = index

    async def callback(self, interaction):
        await self.cog.battle_action(
            interaction, self.encounter_id, lambda battle: battle.switch_to(self.index)
        )


class PartyView(BattleMenu):
    def __init__(self, cog, encounter_id):
        super().__init__(cog, encounter_id)
        battle = cog.battles.get(encounter_id)
        if battle:
            for index, pokemon in enumerate(battle.party[:6]):
                hp = battle.party_hp.get(pokemon.instance_id, 0)
                self.add_item(
                    PartyButton(
                        cog,
                        encounter_id,
                        index,
                        pokemon,
                        hp,
                        pokemon.instance_id == battle.player.instance_id,
                    )
                )
        self.add_item(BackButton(cog, encounter_id, row=3))


class MedicineSelect(discord.ui.Select):
    def __init__(self,cog,encounter_id,item_key,battle):
        options=[]
        for index,pokemon in enumerate(battle.party[:6]):
            hp=int(battle.party_hp.get(pokemon.instance_id,0));maximum=battle.max_hp(pokemon)
            eligible=(item_key=="potion" and 0<hp<maximum) or (item_key=="revive" and hp<=0)
            if eligible:options.append(discord.SelectOption(label=f"{index+1}. {SPECIES[pokemon.species_id].name}",value=str(index),description=f"HP {hp}/{maximum}"))
        available=bool(options)
        if not options:options=[discord.SelectOption(label="No eligible Pokemon",value="none")]
        super().__init__(placeholder=f"Choose a Pokemon for {item_key.title()}...",options=options,custom_id=f"pokemon:{encounter_id}:medicine:{item_key}",disabled=not available)
        self.cog=cog;self.encounter_id=encounter_id;self.item_key=item_key
    async def callback(self,interaction):
        await self.cog.use_battle_item(interaction,self.encounter_id,self.item_key,int(self.values[0]))

class MedicineView(BattleMenu):
    def __init__(self,cog,encounter_id,item_key):
        super().__init__(cog,encounter_id);battle=cog.battles.get(encounter_id)
        if battle:self.add_item(MedicineSelect(cog,encounter_id,item_key,battle))
        self.add_item(BackButton(cog,encounter_id,row=1))

class BagView(BattleMenu):
    def __init__(self,cog,encounter_id,inventory=None):
        super().__init__(cog,encounter_id)
        if inventory is not None:
            counts={"poke_ball":int(inventory.get("balls",0)),"great_ball":int(inventory.get("great_ball",0)),"ultra_ball":int(inventory.get("ultra_ball",0)),"potion":int(inventory.get("potion",0)),"revive":int(inventory.get("revive",0))}
            for button,key in ((self.ball,"poke_ball"),(self.great_ball,"great_ball"),(self.ultra_ball,"ultra_ball"),(self.potion,"potion"),(self.revive,"revive")):
                button.label=f"{button.label} x{counts[key]}";button.disabled=counts[key]<1
        self.ball.custom_id=f"pokemon:{encounter_id}:bag:ball";self.great_ball.custom_id=f"pokemon:{encounter_id}:bag:great";self.ultra_ball.custom_id=f"pokemon:{encounter_id}:bag:ultra";self.potion.custom_id=f"pokemon:{encounter_id}:bag:potion";self.revive.custom_id=f"pokemon:{encounter_id}:bag:revive";self.back.custom_id=f"pokemon:{encounter_id}:bag:back"

    @discord.ui.button(label="Poké Ball",emoji="🔴",style=discord.ButtonStyle.success,custom_id="ball",row=0)
    async def ball(self,interaction,button):await self.cog.throw_ball(interaction,self.encounter_id,"poke_ball")
    @discord.ui.button(label="Great Ball",emoji="🔵",style=discord.ButtonStyle.primary,custom_id="great",row=0)
    async def great_ball(self,interaction,button):await self.cog.throw_ball(interaction,self.encounter_id,"great_ball")
    @discord.ui.button(label="Ultra Ball",emoji="🟡",style=discord.ButtonStyle.primary,custom_id="ultra",row=0)
    async def ultra_ball(self,interaction,button):await self.cog.throw_ball(interaction,self.encounter_id,"ultra_ball")
    @discord.ui.button(label="Potion",emoji="🧪",style=discord.ButtonStyle.success,custom_id="potion",row=1)
    async def potion(self,interaction,button):await self.cog.open_battle_medicine(interaction,self.encounter_id,"potion")
    @discord.ui.button(label="Revive",emoji="✨",style=discord.ButtonStyle.success,custom_id="revive",row=1)
    async def revive(self,interaction,button):await self.cog.open_battle_medicine(interaction,self.encounter_id,"revive")
    @discord.ui.button(label="Back",style=discord.ButtonStyle.secondary,custom_id="back",row=1)
    async def back(self,interaction,button):await interaction.response.edit_message(view=BattleView(self.cog,self.encounter_id))

class BackButton(discord.ui.Button):
    def __init__(self, cog, encounter_id, row):
        super().__init__(
            label="Back",
            style=discord.ButtonStyle.secondary,
            custom_id=f"pokemon:{encounter_id}:back:{row}",
            row=row,
        )
        self.cog = cog
        self.encounter_id = encounter_id

    async def callback(self, interaction):
        await interaction.response.edit_message(view=BattleView(self.cog, self.encounter_id))
