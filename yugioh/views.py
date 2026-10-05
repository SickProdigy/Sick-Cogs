import discord
from .engine import GameError
class ChallengeView(discord.ui.View):
    def __init__(self,cog,challenger,opponent):super().__init__(timeout=120);self.cog=cog;self.challenger=challenger;self.opponent=opponent
    async def interaction_check(self,i):
        if i.user.id==self.opponent:return True
        await i.response.send_message("Only the challenged duelist can answer.",ephemeral=True);return False
    @discord.ui.button(label="Accept duel",style=discord.ButtonStyle.success)
    async def accept(self,i,b):
        try:g=await self.cog.create_game(self.challenger,self.opponent,i.channel_id)
        except GameError as e:await i.response.send_message(str(e),ephemeral=True);return
        self.stop();await i.response.edit_message(content="Duel accepted.",embed=self.cog.game_embed(g),view=GameView(self.cog,g.game_id,g.state_version));g.message_id=i.message.id;await self.cog.save(g);await self.cog.refresh_message(g)
    @discord.ui.button(label="Decline",style=discord.ButtonStyle.secondary)
    async def decline(self,i,b):self.stop();await i.response.edit_message(content="Challenge declined.",view=None)
class GameView(discord.ui.View):
    def __init__(self,cog,game_id,version):
        super().__init__(timeout=None);self.cog=cog;self.game_id=game_id;self.version=version
        for item in self.children:item.custom_id=f"yugioh:{game_id}:{version}:{item.custom_id}"
    async def interaction_check(self,i):
        g=self.cog.games.get(self.game_id)
        if not g or g.finished:await i.response.send_message("This duel is unavailable or over.",ephemeral=True);return False
        if i.user.id not in g.order:await i.response.send_message("You are not a duelist in this match.",ephemeral=True);return False
        if g.state_version!=self.version:await i.response.send_message("That control is stale; use the newest duel message.",ephemeral=True);return False
        return True
    @discord.ui.button(label="View hand",style=discord.ButtonStyle.primary,custom_id="hand")
    async def hand(self,i,b):await i.response.defer(ephemeral=True,thinking=True);await self.cog.send_hand(i,self.game_id,0)
    @discord.ui.button(label="Next phase",style=discord.ButtonStyle.primary,custom_id="next")
    async def next_phase(self,i,b):await self.cog.act(i,self.game_id,lambda g:g.advance(i.user.id),"advance")
    @discord.ui.button(label="No trap / resolve",style=discord.ButtonStyle.secondary,custom_id="resolve")
    async def resolve(self,i,b):await self.cog.act(i,self.game_id,lambda g:g.respond_attack(i.user.id),"resolve attack")
    @discord.ui.button(label="Concede",style=discord.ButtonStyle.danger,custom_id="concede")
    async def concede(self,i,b):await self.cog.act(i,self.game_id,lambda g:g.concede(i.user.id),"concede")

class HandPaginationView(discord.ui.View):
    def __init__(self,cog,game_id,user_id,page,pages):
        super().__init__(timeout=180);self.cog=cog;self.game_id=game_id;self.user_id=user_id;self.page=page
        self.previous.disabled=page<=0;self.next.disabled=page>=pages-1
    async def interaction_check(self,i):
        if i.user.id==self.user_id:return True
        await i.response.send_message("This private hand belongs to another duelist.",ephemeral=True);return False
    @discord.ui.button(label="Previous",style=discord.ButtonStyle.secondary)
    async def previous(self,i,b):await i.response.defer();await self.cog.send_hand(i,self.game_id,self.page-1,editing=True)
    @discord.ui.button(label="Next",style=discord.ButtonStyle.secondary)
    async def next(self,i,b):await i.response.defer();await self.cog.send_hand(i,self.game_id,self.page+1,editing=True)
