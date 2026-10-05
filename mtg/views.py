import asyncio
import discord
from .engine import GameError

class ChallengeView(discord.ui.View):
    def __init__(self,cog,challenger,opponent):
        super().__init__(timeout=120); self.cog=cog; self.challenger=challenger; self.opponent=opponent
    async def interaction_check(self,i):
        if i.user.id==self.opponent: return True
        await i.response.send_message("Only the challenged player can answer.",ephemeral=True); return False
    @discord.ui.button(label="Accept MTG game",style=discord.ButtonStyle.success)
    async def accept(self,i,b):
        try: game=await self.cog.create_game(self.challenger,self.opponent,i.channel_id)
        except GameError as e: await i.response.send_message(str(e),ephemeral=True); return
        self.stop(); await i.response.edit_message(content=None,embed=self.cog.game_embed(game),view=GameView(self.cog,game.game_id))
        game.message_id=i.message.id; await self.cog.save(game)
    @discord.ui.button(label="Decline",style=discord.ButtonStyle.secondary)
    async def decline(self,i,b): self.stop(); await i.response.edit_message(content="Challenge declined.",view=None)

class GameView(discord.ui.View):
    def __init__(self,cog,game_id):
        super().__init__(timeout=None); self.cog=cog; self.game_id=game_id
        for item in self.children: item.custom_id=f"mtg:{game_id}:{item.custom_id}"
    async def interaction_check(self,i):
        game=self.cog.games.get(self.game_id)
        if game and game.finished:
            await i.response.send_message("This match is over.",ephemeral=True); return False
        if game and i.user.id in game.order: return True
        await i.response.send_message("You are not a player in this match.",ephemeral=True); return False
    @discord.ui.button(label="View hand",emoji="🂠",style=discord.ButtonStyle.primary,custom_id="hand")
    async def hand(self,i,b):
        game=self.cog.games[self.game_id]; cards=game.hand(i.user.id)
        lines=[f"**{n}. {c.name}** — {c.kind}, cost {c.cost}\n{c.text or 'No rules text.'}" for n,c in enumerate(cards,1)]
        await i.response.send_message("\n\n".join(lines) or "Your hand is empty.",ephemeral=True)
    @discord.ui.button(label="Keep hand",style=discord.ButtonStyle.success,custom_id="keep")
    async def keep(self,i,b): await self.cog.act(i,self.game_id,lambda g:g.mulligan(i.user.id,True),"keep")
    @discord.ui.button(label="Mulligan",style=discord.ButtonStyle.secondary,custom_id="mulligan")
    async def mulligan(self,i,b): await self.cog.act(i,self.game_id,lambda g:g.mulligan(i.user.id,False),"mulligan")
    @discord.ui.button(label="Pass / next",style=discord.ButtonStyle.primary,custom_id="pass")
    async def pass_turn(self,i,b): await self.cog.act(i,self.game_id,lambda g:g.pass_priority(i.user.id),"pass")
    @discord.ui.button(label="Concede",style=discord.ButtonStyle.danger,custom_id="concede")
    async def concede(self,i,b): await self.cog.act(i,self.game_id,lambda g:g.concede(i.user.id),"concede")
