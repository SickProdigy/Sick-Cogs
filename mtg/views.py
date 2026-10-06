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
        await i.response.defer()
        try: game=await self.cog.create_game(self.challenger,self.opponent,i.channel_id)
        except GameError as e: await i.followup.send(str(e),ephemeral=True); return
        self.stop(); embed,file=await self.cog.game_message(game)
        await i.edit_original_response(content=None,embed=embed,attachments=[file] if file else [],view=GameView(self.cog,game.game_id))
        game.message_id=i.message.id; await self.cog.save(game)
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

class GameView(discord.ui.View):
    def __init__(self,cog,game_id):
        super().__init__(timeout=None); self.cog=cog; self.game_id=game_id
        game=cog.games.get(game_id)
        for item in self.children:
            item.custom_id=f"mtg:{game_id}:{item.custom_id}"
            action=item.custom_id.rsplit(":",1)[-1]
            if game and action in ("keep","mulligan"): item.disabled=game.phase!="opening"
            if game and action=="pass": item.disabled=game.priority_user is None or game.finished or game.phase=="untap" or bool(game.stack and game.stack[-1].decision_pending)
            if game and action in ("pay","decline_trigger"):
                pending=bool(game.stack and game.stack[-1].decision_pending and (game.stack[-1].ability_effect or game.card(game.stack[-1].uid).effect=="power_sink"))
                mandatory=bool(pending and game.stack[-1].ability_effect in ("upkeep_sacrifice","opponent_land_sacrifice","tomb_cleanup"))
                item.disabled=not pending or mandatory
                if pending and action=="pay": item.label=game.trigger_accept_label(game.stack[-1])
                if pending and action=="decline_trigger" and not game.stack[-1].ability_effect: item.label="Don't pay"
            if game and action=="search":
                item.disabled=not bool(game.stack and game.stack[-1].decision_pending and not game.stack[-1].ability_effect and game.card(game.stack[-1].uid).effect=="search_library")
            if game and action=="private_hand":
                item.disabled=not bool(game.stack and game.stack[-1].decision_pending and game.stack[-1].ability_effect in ("discard_choice","look_hand"))
            if game and action=="concede": item.disabled=game.finished
        if game and game.stack and game.stack[-1].decision_pending and game.stack[-1].ability_effect in ("upkeep_sacrifice","opponent_land_sacrifice") and game.trigger_sacrifice_choices(game.stack[-1]):
            self.add_item(SacrificeSelect(self.cog,self.game_id,game,game.stack[-1]))
        if game and game.stack and game.stack[-1].decision_pending and not game.stack[-1].ability_effect and game.card(game.stack[-1].uid).effect=="drain_power":
            self.add_item(DrainPowerSelect(self.cog,self.game_id,game))
        if game and game.phase=="untap" and game.untap_choices(): self.add_item(UntapSelect(self.cog,self.game_id,game))
    async def interaction_check(self,i):
        game=self.cog.games.get(self.game_id)
        if game and game.finished:
            await i.response.send_message("This match is over.",ephemeral=True); return False
        if game and i.user.id in game.order: return True
        await i.response.send_message("You are not a player in this match.",ephemeral=True); return False
    @discord.ui.button(label="View hand",style=discord.ButtonStyle.primary,custom_id="hand")
    async def hand(self,i,b):
        await i.response.defer(ephemeral=True,thinking=True)
        await self.cog.send_hand(i,self.game_id,0,editing=False)
    @discord.ui.button(label="Search library",style=discord.ButtonStyle.secondary,custom_id="search")
    async def search(self,i,b):
        await i.response.defer(ephemeral=True,thinking=True)
        await self.cog.send_library_search(i,self.game_id,0,editing=False)
    @discord.ui.button(label="Private hand choice",style=discord.ButtonStyle.secondary,custom_id="private_hand")
    async def private_hand(self,i,b):
        await i.response.defer(ephemeral=True,thinking=True)
        await self.cog.send_private_hand_decision(i,self.game_id,0,editing=False)
    @discord.ui.button(label="Keep hand",style=discord.ButtonStyle.success,custom_id="keep")
    async def keep(self,i,b): await self.cog.act(i,self.game_id,lambda g:g.mulligan(i.user.id,True),"keep")
    @discord.ui.button(label="Mulligan",style=discord.ButtonStyle.secondary,custom_id="mulligan")
    async def mulligan(self,i,b): await self.cog.act(i,self.game_id,lambda g:g.mulligan(i.user.id,False),"mulligan")
    @discord.ui.button(label="Pass / next",style=discord.ButtonStyle.primary,custom_id="pass")
    async def pass_turn(self,i,b): await self.cog.act(i,self.game_id,lambda g:g.pass_priority(i.user.id),"pass")
    @discord.ui.button(label="Pay {1}",style=discord.ButtonStyle.success,custom_id="pay")
    async def pay_trigger(self,i,b): await self.cog.act(i,self.game_id,lambda g:g.choose_trigger(i.user.id,True),"trigger_pay")
    @discord.ui.button(label="Decline trigger",style=discord.ButtonStyle.secondary,custom_id="decline_trigger")
    async def decline_trigger(self,i,b): await self.cog.act(i,self.game_id,lambda g:g.choose_trigger(i.user.id,False),"trigger_decline")
    @discord.ui.button(label="Concede",style=discord.ButtonStyle.danger,custom_id="concede")
    async def concede(self,i,b): await self.cog.act(i,self.game_id,lambda g:g.concede(i.user.id),"concede")

class HandPaginationView(discord.ui.View):
    def __init__(self,cog,game_id,user_id,page,pages):
        super().__init__(timeout=180)
        self.cog,self.game_id,self.user_id,self.page,self.pages=cog,game_id,user_id,page,pages
        self.previous.disabled=page<=0
        self.next.disabled=page>=pages-1
    async def interaction_check(self,i):
        if i.user.id==self.user_id: return True
        await i.response.send_message("This private hand belongs to another player.",ephemeral=True); return False
    @discord.ui.button(label="Previous",style=discord.ButtonStyle.secondary)
    async def previous(self,i,b):
        await i.response.defer()
        await self.cog.send_hand(i,self.game_id,self.page-1,editing=True)
    @discord.ui.button(label="Next",style=discord.ButtonStyle.secondary)
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

class PrivateHandSelect(discord.ui.Select):
    def __init__(self,browser,entries):
        self.browser=browser; start=browser.page*browser.page_size
        visible=entries[start:start+browser.page_size]
        options=[discord.SelectOption(label=f"{position}. {card.name}"[:100],description=f"{card.kind} - {card.mana_cost or 'no mana cost'}"[:100],value=str(position)) for position,card in visible]
        super().__init__(placeholder="Choose a card to discard",min_values=1,max_values=1,options=options,row=0)
    async def callback(self,interaction):
        await self.browser.cog.complete_private_hand_interaction(interaction,self.browser.game_id,int(self.values[0]))

class PrivateHandDecisionView(discord.ui.View):
    page_size=25
    def __init__(self,cog,game_id,user_id,page,pages,effect,entries):
        super().__init__(timeout=300); self.cog,self.game_id,self.user_id,self.page,self.pages,self.effect=cog,game_id,user_id,page,pages,effect
        if effect=="discard_choice": self.add_item(PrivateHandSelect(self,entries))
        self.previous.disabled=page<=0; self.next.disabled=page>=pages-1
        self.done.disabled=effect!="look_hand"
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
