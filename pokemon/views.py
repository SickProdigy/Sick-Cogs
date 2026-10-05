import discord

from .data import MOVES, SPECIES


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
