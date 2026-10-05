import discord

class EncounterView(discord.ui.View):
    def __init__(self,cog,encounter_id):
        super().__init__(timeout=None);self.cog=cog;self.encounter_id=encounter_id
        self.claim.custom_id=f"pokemon:{encounter_id}:claim"
    @discord.ui.button(label="Encounter",emoji="⚔️",style=discord.ButtonStyle.success,custom_id="claim")
    async def claim(self,i,b):await self.cog.claim(i,self.encounter_id)

class BattleView(discord.ui.View):
    def __init__(self,cog,encounter_id):
        super().__init__(timeout=None);self.cog=cog;self.encounter_id=encounter_id
        for item in self.children:item.custom_id=f"pokemon:{encounter_id}:{item.custom_id}"
        battle=cog.battles.get(encounter_id)
        if battle:
            moves=battle.player.moves
            if moves:self.move1.label=cog.move_label(moves[0])
            if len(moves)>1:self.move2.label=cog.move_label(moves[1])
            else:self.move2.disabled=True
    async def interaction_check(self,i):
        battle=self.cog.battles.get(self.encounter_id)
        if battle and battle.user_id==i.user.id:return True
        await i.response.send_message("This is not your encounter.",ephemeral=True);return False
    @discord.ui.button(label="Move 1",style=discord.ButtonStyle.primary,custom_id="move1")
    async def move1(self,i,b):await self.cog.battle_action(i,self.encounter_id,lambda x:x.use_move(0))
    @discord.ui.button(label="Move 2",style=discord.ButtonStyle.primary,custom_id="move2")
    async def move2(self,i,b):await self.cog.battle_action(i,self.encounter_id,lambda x:x.use_move(1))
    @discord.ui.button(label="Poké Ball",emoji="🔴",style=discord.ButtonStyle.success,custom_id="ball")
    async def ball(self,i,b):await self.cog.throw_ball(i,self.encounter_id)
    @discord.ui.button(label="Switch",emoji="🔄",style=discord.ButtonStyle.secondary,custom_id="switch")
    async def switch(self,i,b):await self.cog.battle_action(i,self.encounter_id,lambda x:x.switch_next())
    @discord.ui.button(label="Run",style=discord.ButtonStyle.secondary,custom_id="run")
    async def run(self,i,b):await self.cog.battle_action(i,self.encounter_id,lambda x:x.run())
