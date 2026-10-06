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


class CollectionPokemonSelect(discord.ui.Select):
    def __init__(self,cog,user_id,items):
        options=[]
        for number,item in items:
            species=SPECIES[item["species_id"]];name=item.get("nickname") or species.name
            options.append(discord.SelectOption(label=f"{number}. {name} · Lv.{item['level']}",value=item["instance_id"]))
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
    def __init__(self,cog,user_id,identity,target,label,row):
        super().__init__(label=label,style=discord.ButtonStyle.success if target is None else discord.ButtonStyle.primary,row=row)
        self.cog=cog;self.user_id=user_id;self.identity=identity;self.target=target
    async def callback(self,interaction):
        await self.cog.place_collection_pokemon(interaction,self.identity,self.target)

class PartyPlacementView(discord.ui.View):
    def __init__(self,cog,user_id,identity,party,owned):
        super().__init__(timeout=120);self.user_id=user_id
        if len(party)<6:self.add_item(PartyPlacementButton(cog,user_id,identity,None,"Add to open slot",0))
        for index,target in enumerate(party):
            raw=owned.get(target);name=SPECIES[raw["species_id"]].name if raw else "Empty"
            self.add_item(PartyPlacementButton(cog,user_id,identity,target,f"Replace {index+1}: {name}",1+index//3))
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
        await interaction.response.edit_message(view=BagView(self.cog, self.encounter_id))

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


class BagView(BattleMenu):
    def __init__(self, cog, encounter_id):
        super().__init__(cog, encounter_id)
        self.ball.custom_id = f"pokemon:{encounter_id}:bag:ball"
        self.back.custom_id = f"pokemon:{encounter_id}:bag:back"

    @discord.ui.button(label="Poké Ball", emoji="🔴", style=discord.ButtonStyle.success, custom_id="ball")
    async def ball(self, interaction, button):
        await self.cog.throw_ball(interaction, self.encounter_id)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, custom_id="back")
    async def back(self, interaction, button):
        await interaction.response.edit_message(view=BattleView(self.cog, self.encounter_id))


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
