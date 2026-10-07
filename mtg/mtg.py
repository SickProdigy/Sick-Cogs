import asyncio
import logging
import secrets
import math
import copy
from collections import Counter
import aiohttp
from typing import Dict
import discord
from redbot.core import Config, commands
from redbot.core.data_manager import cog_data_path
from .ai import DIFFICULTIES, advance_solo
from .art import HAND_PAGE_SIZE, ArtError, ScryfallArtCache, match_result, render_battlefield, render_hand
from .cards import BASE_CARDS, CARDS, starter
from .catalog import ALPHA_CARDS, ALPHA_SET, search_alpha
from .collection import build_pack, resolve_deck
from .engine import Game, GameError
from .views import CatalogDetailView, CatalogView, ChallengeView, CollectionDetailView, CollectionView, GameView, HandPaginationView, HistoryPaginationView, LibrarySearchView, NaturalSelectionView, PrivateHandDecisionView, TradeView

log=logging.getLogger("red.sick-cogs.MTG")
CONFIG_IDENTIFIER=813604927115
DEFAULTS={"schema":2,"next_game_id":1,"games":{},"next_trade_id":1,"trades":{},"trade_ledger":[]}
DEFAULT_PROFILE={"schema":2,"starter":"","collection":{},"decks":{},"active_deck":"","unopened_packs":0,"rewarded_solo_games":[],"pack_history":[]}
MATCH_TIMEOUT_SECONDS=7*24*60*60

class MTG(commands.Cog):
    """Play a deliberately bounded solo or two-player Magic rules prototype."""
    __author__="SickProdigy"
    __version__="0.125.0"
    def __init__(self,bot):
        self.bot=bot; self.config=Config.get_conf(self,identifier=CONFIG_IDENTIFIER,force_registration=True)
        self.config.register_global(**DEFAULTS); self.config.register_user(**DEFAULT_PROFILE)
        self.games:Dict[int,Game]={}; self.locks={}; self.channels={}
        self.storage_lock=asyncio.Lock(); self.cleanup_task=None; self.session=None; self.art_cache=None
    def advance_automatic(self,game):
        changed=False
        for _ in range(100):
            ai_changed=advance_solo(game)
            pass_changed=game.auto_pass_empty_priority()
            changed=changed or ai_changed or pass_changed
            if not ai_changed and not pass_changed: break
        return changed

    async def cog_load(self):
        self.session=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        self.art_cache=ScryfallArtCache(cog_data_path(self)/"art_cache",self.session)
        raw=await self.config.games()
        for key,value in raw.items():
            try:
                game=Game.from_raw(value); game.message_id=int(value.get("message_id",0))
                self.games[game.game_id]=game; self.channels[game.game_id]=int(value.get("channel_id",0))
            except Exception: log.exception("Could not restore MTG game %s",key)
        await self.cleanup_expired()
        resumed=await self.resume_solo_games()
        for game in self.games.values():
            if not game.finished: self.bot.add_view(GameView(self,game.game_id),message_id=game.message_id or None)
        for key,trade in (await self.config.trades()).items():
            if trade.get("message_id"): self.bot.add_view(TradeView(self,int(key)),message_id=int(trade["message_id"]))
        for game in resumed: asyncio.create_task(self.refresh_message(game))
        self.cleanup_task=asyncio.create_task(self._cleanup_loop())
    def cog_unload(self):
        if self.cleanup_task: self.cleanup_task.cancel()
        if self.session and not self.session.closed: asyncio.create_task(self.session.close())
    def lock(self,gid): return self.locks.setdefault(gid,asyncio.Lock())
    def human_players(self,game):
        return [user for user in game.order if user != getattr(game,"ai_user",None)]
    async def player_profile(self,user_id):
        profile=await self.config.user_from_id(int(user_id)).all()
        for key,value in DEFAULT_PROFILE.items(): profile.setdefault(key,dict(value) if isinstance(value,dict) else list(value) if isinstance(value,list) else value)
        return profile
    async def claim_starter(self,user_id,color):
        color=color.casefold()
        if color not in ("red","green"): raise GameError("Choose the red or green starter.")
        async with self.storage_lock:
            scope=self.config.user_from_id(int(user_id)); profile=await scope.all()
            if profile.get("starter"): raise GameError(f"You already chose the {profile['starter'].title()} starter.")
            owned=dict(Counter(starter(color))); deck_id=f"starter-{color}"
            profile={**DEFAULT_PROFILE,"starter":color,"collection":owned,"decks":{deck_id:{"name":f"{color.title()} Starter","cards":dict(owned),"starter":True}},"active_deck":deck_id}
            await scope.set(profile)
        return profile
    def collection_card(self,key):
        return CARDS[key]
    def resolve_saved_deck(self,profile,deck_id=None):
        deck_id=deck_id or profile.get("active_deck",""); deck=profile.get("decks",{}).get(deck_id)
        if not deck: raise GameError("That saved deck does not exist.")
        resolved,errors=resolve_deck(deck.get("cards",{}),profile.get("collection",{}),CARDS)
        if errors: raise GameError("Deck is invalid: "+"; ".join(errors))
        return resolved
    def find_saved_deck(self,profile,query=None):
        decks=profile.get("decks",{}); query=(query or profile.get("active_deck","")).strip()
        if query in decks: return query,decks[query]
        matches=[(key,deck) for key,deck in decks.items() if deck.get("name","").casefold()==query.casefold()]
        if len(matches)==1: return matches[0]
        raise GameError("No saved deck matched that name or ID.")
    def deck_embed(self,profile,deck_id):
        deck=profile.get("decks",{}).get(deck_id); resolved,errors=resolve_deck(deck.get("cards",{}),profile.get("collection",{}),CARDS)
        counts=Counter(CARDS[key].name for key in deck.get("cards",{}) for _ in range(int(deck["cards"][key])))
        lines=[f"{name} ×{count}" for name,count in sorted(counts.items())]
        embed=discord.Embed(title=deck.get("name",deck_id),description="\n".join(lines) or "Empty deck",color=discord.Color.dark_green() if not errors else discord.Color.dark_red())
        embed.add_field(name="Status",value="Valid · 60+ owned cards" if not errors else "Invalid · "+"; ".join(errors),inline=False)
        embed.set_footer(text=f"Deck ID: {deck_id} · Preferred printings resolve automatically")
        return embed
    async def create_custom_deck(self,user_id,name):
        name=name.strip()[:60]
        if not name: raise GameError("Give the deck a name.")
        async with self.storage_lock:
            scope=self.config.user_from_id(int(user_id)); profile=await scope.all()
            if not profile.get("starter"): raise GameError("Choose a starter before creating decks.")
            if any(deck.get("name","").casefold()==name.casefold() for deck in profile.get("decks",{}).values()): raise GameError("You already have a deck with that name.")
            source_id,source=self.find_saved_deck(profile); number=1
            while f"deck-{number}" in profile["decks"]: number+=1
            deck_id=f"deck-{number}"; profile["decks"][deck_id]={"name":name,"cards":dict(source.get("cards",{})),"starter":False}
            await scope.set(profile); return deck_id,profile
    async def edit_saved_deck(self,user_id,deck_query,printing,count,adding):
        if printing not in CARDS: raise GameError("Use an exact owned printing key from your collection.")
        count=int(count)
        if count<1: raise GameError("Quantity must be at least 1.")
        async with self.storage_lock:
            scope=self.config.user_from_id(int(user_id)); profile=await scope.all(); deck_id,deck=self.find_saved_deck(profile,deck_query)
            if deck.get("starter"): raise GameError("Clone the starter deck before editing it.")
            cards=dict(deck.get("cards",{})); current=int(cards.get(printing,0))
            if adding:
                owned=Counter()
                for key,amount in profile.get("collection",{}).items():
                    if key in CARDS: owned[CARDS[key].oracle_id or key]+=int(amount)
                desired=Counter()
                for key,amount in cards.items(): desired[CARDS[key].oracle_id or key]+=int(amount)
                identity=CARDS[printing].oracle_id or printing
                if desired[identity]+count>owned[identity]: raise GameError(f"You own only {owned[identity]} total {CARDS[printing].name} copies across all printings.")
                if not CARDS[printing].land and desired[identity]+count>4: raise GameError("Nonland cards are limited to four copies per deck.")
                cards[printing]=current+count
            else:
                if current<count: raise GameError(f"That deck prefers only {current} of that printing.")
                if current==count: cards.pop(printing,None)
                else: cards[printing]=current-count
            deck["cards"]=cards; profile["decks"][deck_id]=deck; await scope.set(profile); return deck_id,profile
    async def select_saved_deck(self,user_id,query):
        async with self.storage_lock:
            scope=self.config.user_from_id(int(user_id)); profile=await scope.all(); deck_id,_=self.find_saved_deck(profile,query)
            self.resolve_saved_deck(profile,deck_id); profile["active_deck"]=deck_id; await scope.set(profile); return deck_id,profile

    async def _award_solo_pack_unlocked(self,game):
        if not game.finished or game.ai_user is None or game.winner is None or game.winner==game.ai_user or game.finished_reason=="inactivity timeout": return False
        user=game.winner; scope=self.config.user_from_id(user); profile=await scope.all()
        rewarded=[int(item) for item in profile.get("rewarded_solo_games",[])]
        if game.game_id in rewarded: return False
        profile.setdefault("collection",{}); profile.setdefault("decks",{}); profile.setdefault("pack_history",[])
        profile["unopened_packs"]=int(profile.get("unopened_packs",0))+1; rewarded.append(game.game_id); profile["rewarded_solo_games"]=rewarded; profile["schema"]=2
        await scope.set(profile); game.record(user,"pack_reward","Earned 1 unopened pack for a solo victory."); return True
    async def open_pack(self,user_id,rng=None):
        async with self.storage_lock:
            scope=self.config.user_from_id(int(user_id)); profile=await scope.all()
            if not profile.get("starter"): raise GameError("Choose a starter before opening packs.")
            if int(profile.get("unopened_packs",0))<1: raise GameError("You do not have an unopened pack.")
            cards=build_pack(CARDS,rng or secrets.SystemRandom()); collection=dict(profile.get("collection",{}))
            for key in cards: collection[key]=int(collection.get(key,0))+1
            profile["collection"]=collection; profile["unopened_packs"]=int(profile.get("unopened_packs",0))-1
            history=list(profile.get("pack_history",[])); history.append({"at":int(__import__("time").time()),"cards":cards}); profile["pack_history"]=history[-100:]; profile["schema"]=2
            await scope.set(profile); return cards,profile

    async def get_trade(self,trade_id):
        trade=(await self.config.trades()).get(str(int(trade_id)))
        if not trade: raise GameError("That pending trade does not exist.")
        return trade
    def trade_impacts(self,profile,outgoing,incoming=None):
        remaining={key:int(count) for key,count in profile.get("collection",{}).items()}
        for key,count in outgoing.items(): remaining[key]=remaining.get(key,0)-int(count)
        for key,count in (incoming or {}).items(): remaining[key]=remaining.get(key,0)+int(count)
        impacts=[]
        for deck_id,deck in profile.get("decks",{}).items():
            resolved,errors=resolve_deck(deck.get("cards",{}),remaining,CARDS)
            if errors: impacts.append(f"{deck.get('name',deck_id)} becomes invalid")
            elif any(int(deck.get("cards",{}).get(key,0))>max(0,remaining.get(key,0)) for key in outgoing): impacts.append(f"{deck.get('name',deck_id)} substitutes another printing")
        return impacts
    async def create_trade(self,first,second):
        if int(first)==int(second): raise GameError("Trade with another player.")
        async with self.storage_lock:
            for user in (first,second):
                if not (await self.config.user_from_id(int(user)).all()).get("starter"): raise GameError("Both players must choose a starter collection first.")
            trades=await self.config.trades()
            if any(set(map(int,item["users"]))=={int(first),int(second)} for item in trades.values()): raise GameError("You already have a pending trade together.")
            trade_id=int(await self.config.next_trade_id()); await self.config.next_trade_id.set(trade_id+1); now=int(__import__("time").time())
            trade={"trade_id":trade_id,"users":[int(first),int(second)],"offers":{str(first):{},str(second):{}},"confirmations":[],"created_at":now,"updated_at":now,"channel_id":0,"message_id":0}
            trades[str(trade_id)]=trade; await self.config.trades.set(trades); return trade
    async def bind_trade_message(self,trade_id,channel_id,message_id):
        async with self.storage_lock:
            trades=await self.config.trades(); trade=trades.get(str(int(trade_id)))
            if trade: trade["channel_id"]=int(channel_id); trade["message_id"]=int(message_id); trades[str(trade_id)]=trade; await self.config.trades.set(trades)
    async def modify_trade(self,trade_id,user,key,count,adding):
        if key not in CARDS: raise GameError("Use an exact printing key from your collection.")
        count=int(count)
        if count<1: raise GameError("Quantity must be at least 1.")
        async with self.storage_lock:
            trades=await self.config.trades(); trade=trades.get(str(int(trade_id)))
            if not trade: raise GameError("That pending trade does not exist.")
            if int(user) not in map(int,trade["users"]): raise GameError("You are not part of that trade.")
            profile=await self.config.user_from_id(int(user)).all(); offer=dict(trade["offers"].get(str(user),{})); current=int(offer.get(key,0))
            if adding:
                owned=int(profile.get("collection",{}).get(key,0))
                if current+count>owned: raise GameError(f"You own only {owned} of that exact printing.")
                offer[key]=current+count
            else:
                if count>current: raise GameError(f"Your offer contains only {current} of that printing.")
                if count==current: offer.pop(key,None)
                else: offer[key]=current-count
            trade["offers"][str(user)]=offer; trade["confirmations"]=[]; trade["updated_at"]=int(__import__("time").time()); trades[str(trade_id)]=trade; await self.config.trades.set(trades); return trade
    async def confirm_trade(self,trade_id,user):
        async with self.storage_lock:
            trades=await self.config.trades(); key=str(int(trade_id)); trade=trades.get(key)
            if not trade: raise GameError("That pending trade does not exist.")
            users=[int(item) for item in trade["users"]]
            if int(user) not in users: raise GameError("You are not part of that trade.")
            if any(not trade["offers"].get(str(participant)) for participant in users): raise GameError("Both players must offer at least one card.")
            confirmations={int(item) for item in trade.get("confirmations",[])}; confirmations.add(int(user)); trade["confirmations"]=sorted(confirmations); trade["updated_at"]=int(__import__("time").time())
            if confirmations!=set(users): trades[key]=trade; await self.config.trades.set(trades); return trade,False
            scopes={participant:self.config.user_from_id(participant) for participant in users}; profiles={participant:await scopes[participant].all() for participant in users}; originals=copy.deepcopy(profiles)
            for participant in users:
                for printing,amount in trade["offers"][str(participant)].items():
                    if int(profiles[participant].get("collection",{}).get(printing,0))<int(amount): raise GameError(f"Player {participant} no longer owns the full offered quantity of {CARDS[printing].name}.")
            for participant in users:
                receiver=users[1] if participant==users[0] else users[0]
                for printing,amount in trade["offers"][str(participant)].items():
                    source=profiles[participant].setdefault("collection",{}); source[printing]=int(source.get(printing,0))-int(amount)
                    if source[printing]<=0: source.pop(printing,None)
                    target=profiles[receiver].setdefault("collection",{}); target[printing]=int(target.get(printing,0))+int(amount)
            original_ledger=await self.config.trade_ledger(); completed={**trade,"status":"completed","completed_at":int(__import__("time").time()),"confirmations":users}; updated_ledger=[*original_ledger,completed]; updated_trades=dict(trades); updated_trades.pop(key,None)
            try:
                for participant in users: await scopes[participant].set(profiles[participant])
                await self.config.trade_ledger.set(updated_ledger); await self.config.trades.set(updated_trades)
            except Exception:
                for participant in users: await scopes[participant].set(originals[participant])
                await self.config.trade_ledger.set(original_ledger); await self.config.trades.set(trades)
                raise
            return completed,True
    async def cancel_trade(self,trade_id,user):
        async with self.storage_lock:
            trades=await self.config.trades(); key=str(int(trade_id)); trade=trades.get(key)
            if not trade: raise GameError("That pending trade does not exist.")
            if int(user) not in map(int,trade["users"]): raise GameError("You are not part of that trade.")
            trade={**trade,"status":"cancelled","cancelled_by":int(user),"cancelled_at":int(__import__("time").time())}; ledger=await self.config.trade_ledger(); ledger.append(trade); await self.config.trade_ledger.set(ledger); trades.pop(key,None); await self.config.trades.set(trades); return trade
    async def trade_embed(self,trade):
        users=[int(item) for item in trade["users"]]; names={user:str(self.bot.get_user(user).display_name if self.bot.get_user(user) else user) for user in users}; embed=discord.Embed(title=f"MTG trade #{trade['trade_id']}",color=discord.Color.dark_green())
        for user in users:
            offer=trade.get("offers",{}).get(str(user),{}); lines=[f"{CARDS[key].name} ×{count} · `{key}`" for key,count in offer.items()] or ["Nothing offered"]
            if trade.get("status") not in ("completed","cancelled"):
                profile=await self.player_profile(user); other=users[1] if user==users[0] else users[0]; incoming=trade.get("offers",{}).get(str(other),{}); impacts=self.trade_impacts(profile,offer,incoming)
                if impacts: lines.append("**Deck impact:** "+"; ".join(impacts))
            confirmed=" · Confirmed" if user in [int(item) for item in trade.get("confirmations",[])] else " · Not confirmed"
            embed.add_field(name=names[user]+confirmed,value="\n".join(lines),inline=False)
        embed.set_footer(text="Any offer edit clears both confirmations. Transfers occur only after both players confirm." if not trade.get("status") else trade["status"].title())
        return embed
    async def refresh_trade(self,trade):
        try:
            channel=self.bot.get_channel(int(trade.get("channel_id",0))); message=await channel.fetch_message(int(trade.get("message_id",0))); await message.edit(embed=await self.trade_embed(trade),view=None if trade.get("status") else TradeView(self,trade["trade_id"]))
        except (discord.HTTPException,AttributeError): pass
    async def trade_interaction(self,interaction,trade_id,confirming):
        try:
            if confirming: trade,done=await self.confirm_trade(trade_id,interaction.user.id)
            else: trade=await self.cancel_trade(trade_id,interaction.user.id); done=True
        except GameError as error: await interaction.response.send_message(str(error),ephemeral=True); return
        await interaction.response.edit_message(embed=await self.trade_embed(trade),view=None if done else TradeView(self,trade_id))

    async def collection_records(self,user_id):
        profile=await self.player_profile(user_id)
        records=[(key,int(count)) for key,count in profile.get("collection",{}).items() if key in CARDS and int(count)>0]
        return sorted(records,key=lambda item:(CARDS[item[0]].name.casefold(),item[0]))
    def collection_embed(self,profile,records,page):
        start=page*CollectionView.page_size; visible=records[start:start+CollectionView.page_size]
        lines=[]
        for key,count in visible:
            card=CARDS[key]; lines.append(f"**{card.name}** ×{count}\n{card.type_line} · {card.mana_cost or 'No mana cost'} · `{key}`")
        pages=max(1,math.ceil(len(records)/CollectionView.page_size))
        embed=discord.Embed(title=f"{profile['starter'].title()} starter collection",description="\n\n".join(lines),color=discord.Color.dark_green())
        embed.set_footer(text=f"Page {page+1}/{pages} · {sum(count for _,count in records)} cards · {len(records)} distinct printings")
        return embed
    async def show_collection_page(self,interaction,user_id,page):
        profile=await self.player_profile(user_id); records=await self.collection_records(user_id); pages=max(1,math.ceil(len(records)/CollectionView.page_size)); page=max(0,min(page,pages-1))
        view=CollectionView(self,user_id,records,page)
        await interaction.edit_original_response(embed=self.collection_embed(profile,records,page),attachments=[],view=view)
    async def show_collection_detail(self,interaction,browser,index):
        key,count=browser.records[index]; card=CARDS[key]; source="alpha" if card.set_code=="lea" else "playable"
        embed,file=await self.card_presentation(source,card); embed.add_field(name="Owned",value=str(count),inline=False)
        embed.set_footer(text=f"Owned printing {index+1}/{len(browser.records)} · Collection returns to page {browser.page+1}")
        await interaction.edit_original_response(embed=embed,attachments=[file] if file else [],view=CollectionDetailView(browser,index))

    def ensure_players_available(self,*users):
        for game in self.games.values():
            if not game.finished and any(user in self.human_players(game) for user in users):
                raise GameError("One player already has an active game.")
    async def create_game(self,a,b,channel,decks=None):
        async with self.storage_lock:
            self.ensure_players_available(a,b)
            gid=await self.config.next_game_id(); await self.config.next_game_id.set(gid+1)
            users=[a,b]; secrets.SystemRandom().shuffle(users)
            game=Game(gid,users,decks=decks); game.message_id=0; self.games[gid]=game; self.channels[gid]=channel
            await self._save_unlocked(game)
        return game
    async def create_collection_game(self,a,b,channel,a_deck_id,b_deck_id):
        profiles={a:await self.player_profile(a),b:await self.player_profile(b)}
        chosen={}; lists={}
        for user,deck_id in ((a,a_deck_id),(b,b_deck_id)):
            resolved=self.resolve_saved_deck(profiles[user],deck_id); deck=profiles[user]["decks"][deck_id]
            chosen[user]=deck.get("name",deck_id); lists[user]=resolved
        async with self.storage_lock:
            self.ensure_players_available(a,b); gid=await self.config.next_game_id(); await self.config.next_game_id.set(gid+1)
            users=[a,b]; secrets.SystemRandom().shuffle(users)
            game=Game(gid,users,decks=chosen,decklists=lists); game.message_id=0; self.games[gid]=game; self.channels[gid]=channel; await self._save_unlocked(game)
        return game
    async def create_solo_game(self,human,ai,channel,deck,difficulty,deck_cards=None,starter_color=None):
        async with self.storage_lock:
            self.ensure_players_available(human)
            gid=await self.config.next_game_id(); await self.config.next_game_id.set(gid+1)
            users=[human,ai]; secrets.SystemRandom().shuffle(users)
            base_color=starter_color or (deck if deck in ("red","green") else "red")
            other="green" if base_color=="red" else "red"
            lists={human:deck_cards} if deck_cards is not None else None
            game=Game(gid,users,decks={human:deck,ai:other},ai_user=ai,ai_difficulty=difficulty,decklists=lists)
            game.message_id=0; self.games[gid]=game; self.channels[gid]=channel
            self.advance_automatic(game)
            await self._save_unlocked(game)
        return game
    async def _save_unlocked(self,game):
        await self._award_solo_pack_unlocked(game)
        raw=game.to_raw(); raw["channel_id"]=self.channels.get(game.game_id,0); raw["message_id"]=getattr(game,"message_id",0)
        games=await self.config.games(); games[str(game.game_id)]=raw; await self.config.games.set(games)
    async def save(self,game):
        async with self.storage_lock: await self._save_unlocked(game)
    async def resume_solo_games(self):
        resumed=[]
        for game in list(self.games.values()):
            async with self.lock(game.game_id):
                if self.advance_automatic(game):
                    await self.save(game); resumed.append(game)
        return resumed
    async def cleanup_expired(self):
        expired=[]; now=int(__import__("time").time())
        for game in list(self.games.values()):
            async with self.lock(game.game_id):
                if game.is_expired(now,MATCH_TIMEOUT_SECONDS):
                    game.expire(); await self.save(game); expired.append(game)
        for game in expired: await self.refresh_message(game)
        return len(expired)
    async def _cleanup_loop(self):
        try:
            while True:
                await asyncio.sleep(3600); await self.cleanup_expired()
        except asyncio.CancelledError: return
    def find(self,user):
        found=[g for g in self.games.values() if user in g.order and not g.finished]
        if not found: raise GameError("You do not have an active MTG game.")
        return found[0]
    def game_embed(self,g):
        names={u:(self.bot.get_user(u).display_name if self.bot.get_user(u) else str(u)) for u in g.order}
        if g.ai_user is not None:
            names[g.ai_user]=f"{names[g.ai_user]} ({(g.ai_difficulty or 'easy').title()} AI)"
        e=discord.Embed(title=f"MTG prototype · Game {g.game_id}",color=discord.Color.dark_green())
        if g.phase=="opening":
            e.description=f"Opening hands - **{names[g.active_user]}** will play first."
        else:
            e.description=f"Turn **{g.turn}** - **{g.phase.replace('_',' ').title()}**\nActive: **{names[g.active_user]}**"
            if g.phase=="camouflage": e.description+=f"\n**{names[g.opponent(g.active_user)]}** must divide chosen creatures into {len(g.attackers)} Camouflage piles; use battlefield positions with `camouflage`."
            elif g.priority_user: e.description+=f"\nPriority: **{names[g.priority_user]}**"
            elif g.phase=="attackers": e.description+=f"\nWaiting for **{names[g.active_user]}** to declare attackers."
            elif g.phase=="blockers": e.description+=f"\nWaiting for **{names[g.opponent(g.active_user)]}** to declare blockers."
        for user in g.order:
            p=g.players[user]; field=[]
            for n,x in enumerate(p.battlefield,1):
                c=g.card(x.uid); state=" ↷" if x.tapped else ""
                if x.owner in g.players and x.owner!=user: state+=f" - owned by {names[x.owner]}"
                stats=(lambda value:f" {value[0]}/{value[1]}")(g.current_stats(x)) if g.is_creature(x) else ""
                current_keywords=g.current_keywords(x); active=sorted(current_keywords-set(c.keywords)); suppressed=sorted(set(c.keywords)-current_keywords)
                active_protection=sorted(g.current_protections(x)-set(c.protection_colors))
                ability_parts=[c.ability_text] if c.ability_text else []
                if active: ability_parts.append("Active: "+", ".join(word.title() for word in active))
                if suppressed: ability_parts.append("Suppressed: "+", ".join(word.title() for word in suppressed))
                if active_protection: ability_parts.append("Active protection: "+"/".join(active_protection))
                current_colors=g.current_colors(x)
                if current_colors!=c.colors: ability_parts.append("Color: "+"/".join({"W":"White","U":"Blue","B":"Black","R":"Red","G":"Green"}[color] for color in current_colors))
                live_land_types=g.current_land_types(x)
                printed_land_types={kind for kind in ("plains","island","swamp","mountain","forest") if c.has_land_type(kind)}
                if live_land_types!=printed_land_types: ability_parts.append("Land type: "+"/".join(kind.title() for kind in sorted(live_land_types)))
                live_mana=g.current_mana_choices(x)
                if c.land and live_mana!=c.produces: ability_parts.append("Mana now: "+"/".join(live_mana))
                if x.animated_until_end_combat: ability_parts.append("Animated: 3/6 Golem artifact creature until end of combat")
                granted_regeneration=g.granted_regeneration_cost(x)
                if granted_regeneration: ability_parts.append(f"Granted: {granted_regeneration}: Regenerate this creature")
                if x.regeneration_shields: ability_parts.append(f"Regeneration shield ×{x.regeneration_shields}")
                if x.damage_prevention: ability_parts.append(f"Damage prevention remaining: {x.damage_prevention}")
                if c.hydra_damage_replacement: ability_parts.append("Hydra replacement order: "+("counters first" if x.hydra_counters_first else "shields/redirection first"))
                if x.redirect_damage_to_owner: ability_parts.append(f"Next damage redirected to owner: {x.redirect_damage_to_owner}")
                if x.redirect_source_damage_to_player:
                    sources=[g.card(uid).name if uid in g.cards else f"source {uid}" for uid in x.redirect_source_damage_to_player]
                    ability_parts.append("Jade Monolith redirects: "+", ".join(sources))
                if x.plus_one_counters: ability_parts.append(f"+1/+1 counters: {x.plus_one_counters}")
                if x.power_counters: ability_parts.append(f"+1/+0 counters: {x.power_counters}")
                if x.corpse_counters: ability_parts.append(f"Corpse counters: {x.corpse_counters}")
                if x.vitality_counters: ability_parts.append(f"Vitality counters: {x.vitality_counters}")
                mire_counters=sum(effect.get("kind")=="mire" for effect in x.land_type_effects)
                if mire_counters: ability_parts.append(f"Mire counters: {mire_counters}")
                if x.attached_to:
                    _,target=g.find_permanent(x.attached_to)
                    ability_parts.append("Attached to "+(g.card(target.uid).name if target else "missing permanent"))
                abilities=" ["+" / ".join(ability_parts)+"]" if ability_parts else ""
                field.append(f"{n}. {c.name}{stats}{abilities}{state}")
            pool=" ".join(f"{{{symbol}}}×{count}" for symbol,count in sorted(p.mana_pool.items()))
            value="\n".join(field) or "No permanents"
            value+=f"\nGraveyard: {len(p.graveyard)} · Exile: {len(p.exile)}"
            if pool: value+=f"\nMana pool: {pool}"
            if p.damage_prevention: value+=f"\nDamage prevention remaining: {p.damage_prevention}"
            if p.damage_taken_this_turn: value+=f"\nDamage taken this turn: {p.damage_taken_this_turn}"
            if p.channel_active: value+="\nChannel: pay life for {C} until end of turn"
            if p.guardian_angel_active: value+="\nGuardian Angel: pay {1} to prevent the next 1 damage to any target this turn"
            if p.source_damage_prevention:
                sources=[g.card(uid).name if uid in g.cards else f"source {uid}" for uid in p.source_damage_prevention]
                value+="\nChosen-source prevention: "+", ".join(sources)
            if p.source_damage_lifegain:
                sources=[g.card(uid).name if uid in g.cards else f"source {uid}" for uid in p.source_damage_lifegain]
                value+="\nReverse Damage awaiting: "+", ".join(sources)
            if p.source_damage_caps:
                sources=[g.card(uid).name if uid in g.cards else f"source {uid}" for uid in p.source_damage_caps]
                value+="\nForcefield awaiting: "+", ".join(sources)
            if p.bodyguard_choice:
                value+="\nVeteran Bodyguard choice: "+(g.card(p.bodyguard_choice).name if p.bodyguard_choice in g.cards else f"source {p.bodyguard_choice}")
            e.add_field(name=f"{names[user]} · {p.life} life · {len(p.hand)} cards",value=value,inline=False)
        if g.phase=="untap" and g.untap_pending:
            pending=set(g.untap_pending); player=g.player(g.active_user)
            choices=[f"{position}. {g.card(permanent.uid).name}" for position,permanent in enumerate(player.battlefield,1) if permanent.uid in pending]
            e.add_field(name="Restricted untap choice",value="Choose a maximal legal set: "+", ".join(choices),inline=False)
        if g.extra_turns:
            queued=" → ".join(names[user] for user in g.extra_turns)
            e.add_field(name="Extra turns queued",value=queued,inline=False)
        if g.turn_start_pending_user is not None:
            kind="extra turn" if g.turn_start_pending_extra else "turn"
            e.add_field(name="Time Vault turn choice",value=f"{names[g.turn_start_pending_user]} must take their {kind} or choose a tapped Time Vault and skip it.",inline=False)
        if g.sanctuary_draw_pending:
            e.add_field(name="Island Sanctuary draw choice",value=f"{names[g.active_user]} must choose Draw or Skip ({g.sanctuary_pending_draws} draw{'s' if g.sanctuary_pending_draws!=1 else ''} remaining).",inline=False)
        if g.phase=="cleanup_discard":
            e.add_field(name="Cleanup discard",value=f"{names[g.active_user]} must privately discard {max(0,len(g.player(g.active_user).hand)-7)} card(s) to reach seven.",inline=False)
        protected=[names[user] for user in g.order if g.player(user).island_sanctuary_active]
        if protected: e.add_field(name="Island Sanctuary protection",value=", ".join(protected)+" can be attacked only by creatures with flying or islandwalk.",inline=False)
        if g.prevent_combat_damage:
            e.add_field(name="Turn effect",value="All combat damage is prevented this turn.",inline=False)
        if g.forced_attackers:
            forced=[g.card(uid).name for uid in g.forced_attackers if g.find_permanent(uid)[1] is not None]
            if forced: e.add_field(name="Must attack this combat if able",value=", ".join(forced),inline=False)
        if g.attack_bands:
            bands=[" + ".join(g.card(uid).name for uid in band if g.find_permanent(uid)[1] is not None) for band in g.attack_bands]
            bands=[band for band in bands if band]
            if bands: e.add_field(name="Attacking bands",value="\n".join(bands),inline=False)
        if g.raging_river_rules:
            lines=[]
            for index,rule in enumerate(g.raging_river_rules,1):
                attackers=", ".join(f"{g.card(uid).name}: {side}" for uid,side in rule["attackers"].items() if uid in g.cards) or "No attackers"
                blockers=", ".join(f"{g.card(uid).name}: {side}" for uid,side in rule["blockers"].items() if uid in g.cards) or "No nonflying blockers"
                lines.append(f"River {index} — attackers: {attackers}; defenders: {blockers}")
            e.add_field(name="Raging River divisions",value="\n".join(lines),inline=False)
        if g.trample_assignments:
            choices=[]
            for uid,amount in g.trample_assignments.items():
                _,attacker=g.find_permanent(uid)
                if attacker is not None: choices.append(f"{g.card(uid).name}: {amount} to blocker")
            if choices: e.add_field(name="Trample assignments",value="\n".join(choices),inline=False)
        multi=[]
        for blocker_uid in g.all_blocker_uids():
            _,blocker=g.find_permanent(blocker_uid); attackers=[uid for uid in g.attackers_for(blocker_uid) if g.find_permanent(uid)[1] is not None]
            if blocker is None or len(attackers)<2: continue
            assignment=g.blocker_damage_assignments.get(blocker_uid,[])
            if assignment:
                detail=", ".join(f"{g.card(item['attacker']).name}: {item['damage']}" for item in assignment)
            else: detail="damage assignment required before its damage step"
            multi.append(f"{g.card(blocker_uid).name} blocks {len(attackers)} attackers · {detail}")
        for attacker_uid in g.attackers:
            blockers=[uid for uid in g.blockers_for(attacker_uid) if g.find_permanent(uid)[1] is not None]
            if len(blockers)<2: continue
            assignment=g.attacker_damage_assignments.get(attacker_uid,[])
            detail=", ".join(f"{g.card(item['blocker']).name}: {item['damage']}" for item in assignment) if assignment else "damage assignment required before its damage step"
            multi.append(f"{g.card(attacker_uid).name} is blocked by {len(blockers)} creatures · {detail}")
        if multi: e.add_field(name="Multiple blocking",value="\n".join(multi),inline=False)
        if g.stack:
            stack_lines=[]
            for position,item in enumerate(reversed(g.stack),1):
                label=("Face-down creature spell" if item.face_down else g.card(item.uid).name+(" ability" if item.ability_effect else ""))+(" copy" if item.is_copy else "")
                if item.color_override: label+=f" [{item.color_override}]"
                if (not item.ability_effect and "{X}" in g.card(item.uid).mana_cost) or g.card(item.uid).activation_x_choice: label+=f" (X={item.x_value})"
                if item.ability_effect=="add_power_counters": label+=f" (add {item.choice_value})"
                if not item.ability_effect and g.card(item.uid).effect in ("fireball","volcanic_eruption"):
                    targets=[]
                    for stable in (item.target or "").split(",") if item.target else []:
                        if ":" in stable:
                            _,target=g.find_permanent(int(stable.split(":")[1])); targets.append(g.card(target.uid).name if target is not None else "departed permanent")
                        else: targets.append(names.get(int(stable),stable))
                    label+=" (targets: "+(", ".join(targets) if targets else "none")+")"
                if not item.ability_effect and g.card(item.uid).effect=="sacrifice_mana": label+=f" (adds {{{g.card(item.uid).sacrifice_mana_color}}}×{item.choice_value})"
                if item.ability_effect=="upkeep_untapped_land_damage": label+=f" ({item.choice_value} damage snapshot)"
                if item.ability_effect=="prevent_source_damage" and item.target:
                    source_uid=int(item.target.split(":")[1]); label+=f" (source: {g.card(source_uid).name if source_uid in g.cards else source_uid})"
                if item.decision_pending:
                    if item.ability_effect=="mask_choose": label+=" (controller is privately choosing an eligible creature or declining)"
                    elif item.ability_effect=="word_choose": label+=f" ({item.choice_owner} is privately choosing a card from the targeted hand)"
                    elif item.ability_effect=="lich_damage": label+=f" ({item.choice_owner} must sacrifice {item.choice_value} nontoken permanents)"
                    elif item.ability_effect=="raging_river_split": label+=f" ({item.choice_owner} must divide nonflying defenders left/right)"
                    elif item.ability_effect=="raging_river_attackers": label+=f" ({item.choice_owner} must divide attackers left/right)"
                    elif item.is_copy and item.fork_retarget: label+=f" ({item.choice_owner} must choose new targets or keep the originals)"
                    elif not item.ability_effect and g.card(item.uid).effect=="search_library": label+=" (controller is searching their library)"
                    elif not item.ability_effect and g.card(item.uid).effect=="natural_selection": label+=" (controller is privately arranging the targeted library)"
                    elif not item.ability_effect and g.card(item.uid).effect in ("text_change_land","text_change_color"): label+=" (controller must choose the word replacement)"
                    elif not item.ability_effect and g.card(item.uid).effect=="false_orders": label+=" (controller must choose its new blocking assignment or decline)"
                    elif not item.ability_effect and g.card(item.uid).enters_copy_types: label+=" (controller is choosing a permanent to copy)"
                    elif not item.ability_effect and g.card(item.uid).effect=="drain_power": label+=" (target player is choosing land mana)"
                    elif not item.ability_effect and g.card(item.uid).effect=="power_sink": label+=f" (targeted spell's controller may {g.trigger_accept_label(item)} or decline)"
                    elif item.ability_effect=="discard_choice": label+=" (target player is choosing a card privately)"
                    elif item.ability_effect=="look_hand": label+=" (controller is viewing the targeted hand privately)"
                    elif item.ability_effect=="leng_discard": label+=f" ({item.choice_owner} is privately choosing a discard destination with Library of Leng)"
                    elif item.ability_effect=="power_leak": label+=" (enchanted enchantment's controller must choose how much mana to pay)"
                    elif item.ability_effect=="vesuvan_copy": label+=(" (controller must choose its creature target before responses)" if item.choice_value==0 else " (controller must choose whether to become the targeted copy)")
                    elif item.ability_effect=="kudzu_move": label+=" (the controller of the destroyed land may reattach Kudzu or decline)"
                    elif item.ability_effect.startswith("balance_"): label+=f" ({item.choice_owner} must complete the {item.ability_effect.split('_',1)[1]} choice)"
                    else: label+=(f" (chooser must {g.trigger_accept_label(item)})" if item.ability_effect in ("upkeep_sacrifice","opponent_land_sacrifice","tomb_cleanup") else f" (controller may {g.trigger_accept_label(item)} or Decline)")
                stack_lines.append(f"S:{position}. {label}")
            e.add_field(name="Stack · spells targetable with S:POSITION",value="\n".join(stack_lines),inline=False)
        if g.end_combat_destroys:
            pending=[]
            for item in g.end_combat_destroys:
                _,target=g.find_permanent(int(item.target.split(":")[1]))
                action="remove a +1/+0 counter from" if item.ability_effect=="end_combat_remove_power_counter" else "destroy"
                pending.append(f"{g.card(item.uid).name}: {action} "+(g.card(target.uid).name if target else "departed creature"))
            e.add_field(name="Pending end-of-combat triggers",value="\n".join(pending),inline=False)
        if g.end_step_sacrifices:
            e.add_field(name="Pending end-step trigger",value="Sacrifice "+", ".join(g.card(uid).name for uid in g.end_step_sacrifices)+" · players may respond",inline=False)
        if g.end_step_destroys:
            pending_names=[]
            for trigger in g.end_step_destroys:
                _,target=g.find_permanent(int(trigger.target.split(":",1)[1])) if trigger.target and ":" in trigger.target else (None,None)
                pending_names.append(g.card(target.uid).name if target is not None else "departed target")
            e.add_field(name="Next end-step destruction",value=", ".join(pending_names),inline=False)
        if g.finished:
            result, winner, defeated, detail=match_result(g,names)
            e.description=f"**{result}: {winner}**\n{defeated}\nReason: {detail}."
            if any(event.get("action")=="pack_reward" for event in g.history): e.description+="\nReward: **1 unopened pack**"
        e.set_footer(text="Experimental supported-card subset · hands are private")
        return e
    async def game_message(self,game):
        embed=self.game_embed(game); paths={}
        public_cards=[game.card(permanent.uid) for player in game.players.values() for permanent in player.battlefield]
        public_cards.extend(game.card(spell.uid) for spell in game.stack)
        for card in {card.key:card for card in public_cards}.values():
            try: paths[card.key]=await self.art_cache.get(card)
            except (ArtError,aiohttp.ClientError,asyncio.TimeoutError,OSError):
                log.warning("Could not cache public art for %s",card.key,exc_info=True)
        names={user:str(self.bot.get_user(user).display_name if self.bot.get_user(user) else user).replace("\n"," ")[:32] for user in game.order}
        if game.ai_user is not None:
            names[game.ai_user]=f"{names[game.ai_user]} ({(game.ai_difficulty or 'easy').title()} AI)"
        try:
            background=__import__("pathlib").Path(__file__).with_name("assets")/"default_playmat.png"
            image=await asyncio.to_thread(render_battlefield,game,names,paths,background)
            file=discord.File(image,filename=f"mtg-table-{game.game_id}.png")
            embed.set_image(url=f"attachment://mtg-table-{game.game_id}.png")
            return embed,file
        except (ArtError,OSError):
            log.warning("Could not render public battlefield",exc_info=True)
            return embed,None
    async def refresh_message(self,game):
        try:
            channel=self.bot.get_channel(self.channels.get(game.game_id)); message=await channel.fetch_message(game.message_id)
            embed,file=await self.game_message(game)
            await message.edit(embed=embed,attachments=[file] if file else [],view=None if game.finished else GameView(self,game.game_id))
        except (discord.HTTPException,AttributeError): pass
    async def send_history(self,interaction,game_id,page,editing=False):
        game=self.games.get(game_id)
        if not game or interaction.user.id not in game.order:
            content="This game history is unavailable."
            if editing: await interaction.edit_original_response(content=content,view=None)
            else: await interaction.followup.send(content,ephemeral=True)
            return
        events=list(reversed(game.history)); pages=max(1,math.ceil(len(events)/HistoryPaginationView.page_size)); page=max(0,min(page,pages-1))
        start=page*HistoryPaginationView.page_size; visible=events[start:start+HistoryPaginationView.page_size]
        names={user:str(self.bot.get_user(user).display_name if self.bot.get_user(user) else user).replace("\n"," ")[:32] for user in game.order}
        labels={"game_created":"Game created","keep":"Kept hand","mulligan":"Mulligan","play":"Played or cast a card","pass":"Passed priority","auto_pass":"Auto-passed empty priority","attack":"Declared attackers","ai_attack":"Declared attackers","attack_none":"Declared no attacks","block":"Declared blockers","ai_block":"Declared blockers","auto_no_blocks":"Declared no blocks","block_none":"Declared no blocks","combat_damage":"Combat resolved","concede":"Conceded","pack_reward":"Pack earned"}
        lines=[]
        for event in visible:
            actor=names.get(event.get("user"),"Game")
            turn=event.get("turn","?"); phase=str(event.get("phase","unknown")).replace("_"," ").title(); action=labels.get(event.get("action"),str(event.get("action","Action")).replace("_"," ").title())
            detail=str(event.get("detail","")).replace("\n"," ")[:180]
            lines.append(f"**#{event.get('seq', '?')} · Turn {turn} · {phase}** — {actor}: {action}"+(f" — {detail}" if detail else ""))
        content=f"**Game {game_id} history · newest first · page {page+1}/{pages}**\n"+("\n".join(lines) if lines else "No actions have been recorded yet.")
        view=HistoryPaginationView(self,game_id,interaction.user.id,page,pages)
        if editing: await interaction.edit_original_response(content=content,view=view)
        else: await interaction.followup.send(content,view=view,ephemeral=True)

    async def send_hand(self,interaction,game_id,page,editing=False):
        game=self.games.get(game_id)
        if not game or interaction.user.id not in game.order:
            content="This private hand is unavailable."
            if editing: await interaction.edit_original_response(content=content,attachments=[],view=None)
            else: await interaction.followup.send(content,ephemeral=True)
            return
        cards=game.hand(interaction.user.id); pages=max(1,math.ceil(len(cards)/HAND_PAGE_SIZE))
        page=max(0,min(page,pages-1)); start=page*HAND_PAGE_SIZE; visible=cards[start:start+HAND_PAGE_SIZE]
        visible_paths=[]
        for card in visible:
            try: visible_paths.append(await self.art_cache.get(card))
            except (ArtError,aiohttp.ClientError,asyncio.TimeoutError,OSError):
                log.warning("Could not cache art for %s",card.key,exc_info=True); visible_paths.append(None)
        paths=[None]*start+visible_paths
        fallback_text="\n".join(f"**{start+n}. {card.name}** - {card.kind}, {card.mana_cost or 'no mana cost'}" for n,card in enumerate(visible,1)) or "Your hand is empty."
        view=HandPaginationView(self,game_id,interaction.user.id,page,pages)
        if game.phase=="opening":
            player=game.player(interaction.user.id)
            if player.kept: guidance="You kept this hand. Waiting for the other player."
            elif player.mulligans:
                kept=max(0,len(player.hand)-player.mulligans); guidance=f"To keep **{kept} cards**, select exactly **{player.mulligans}** card{'s' if player.mulligans!=1 else ''} to put on the bottom of your library. Or choose **Mulligan** to redraw."
            else: guidance="Choose **Keep all 7 cards** or **Mulligan** below."
        elif game.phase=="attackers" and game.active_user==interaction.user.id: guidance="**Your turn — choose attackers below**, or choose **No attacks**."
        elif game.phase=="blockers" and game.opponent(game.active_user)==interaction.user.id: guidance="**You are defending — choose blockers below**, or choose **No blocks**."
        elif game.phase in ("attackers","blockers"): guidance="Waiting for the other player to complete the combat declaration."
        elif game.priority_user!=interaction.user.id: guidance="You do not currently have priority. Return to the public game table for the required action."
        elif view.playable_count: guidance="Use the private menu below to play or cast a currently legal card."
        else: guidance="You have no cards you can legally play right now. Return to the public table and use **Pass priority** or the required combat control."
        text=guidance+"\n**Gold** = playable now · **Purple** = not currently playable."
        try:
            image=await asyncio.to_thread(render_hand,cards,paths,page,view.playable_positions)
            file=discord.File(image,filename=f"mtg-hand-{game_id}-{page+1}.png")
            if editing: await interaction.edit_original_response(content=text,attachments=[file],view=view)
            else:
                kwargs={"file":file,"ephemeral":True}
                if view is not None: kwargs["view"]=view
                await interaction.followup.send(text,**kwargs)
        except (ArtError,OSError):
            log.warning("Could not render private hand",exc_info=True)
            fallback=f"{guidance}\n\n{fallback_text}"
            if editing: await interaction.edit_original_response(content=fallback,attachments=[],view=view)
            else:
                kwargs={"ephemeral":True}
                if view is not None: kwargs["view"]=view
                await interaction.followup.send(fallback,**kwargs)
    async def opening_hand_interaction(self,interaction,game_id,keep,bottom_positions=None):
        game=self.games.get(game_id)
        if not game or interaction.user.id not in game.order:
            await interaction.response.send_message("This private hand is unavailable.",ephemeral=True); return
        async with self.lock(game.game_id):
            try:
                player=game.player(interaction.user.id); mulligans=player.mulligans
                game.mulligan(interaction.user.id,keep,bottom_positions)
                detail=(f"Kept {len(player.hand)} cards; put {mulligans} on the bottom of the library." if keep and mulligans else "Kept all 7 cards." if keep else f"Redrew seven cards; keeping {max(0,6-mulligans)} if this hand is kept.")
                game.record(interaction.user.id,"keep" if keep else "mulligan",detail); self.advance_automatic(game); await self.save(game)
            except (GameError,IndexError,ValueError) as error:
                await interaction.response.send_message(str(error),ephemeral=True); return
        await interaction.response.defer()
        await self.send_hand(interaction,game_id,0,editing=True)
        await self.refresh_message(game)

    async def pass_hand_interaction(self,interaction,game_id):
        game=self.games.get(game_id)
        if not game or interaction.user.id not in game.order:
            await interaction.response.send_message("This private hand is unavailable.",ephemeral=True); return
        async with self.lock(game.game_id):
            try:
                game.pass_priority(interaction.user.id); game.record(interaction.user.id,"pass"); self.advance_automatic(game); await self.save(game)
            except (GameError,IndexError,ValueError) as error:
                await interaction.response.send_message(str(error),ephemeral=True); return
        await interaction.response.defer()
        await interaction.delete_original_response()
        await self.refresh_message(game)

    async def combat_hand_interaction(self,interaction,game_id,action,label):
        game=self.games.get(game_id)
        if not game or interaction.user.id not in game.order:
            await interaction.response.send_message("This private hand is unavailable.",ephemeral=True); return
        async with self.lock(game.game_id):
            try:
                action(game); game.record(interaction.user.id,label); self.advance_automatic(game); await self.save(game)
            except (GameError,IndexError,ValueError) as error:
                await interaction.response.send_message(str(error),ephemeral=True); return
        await interaction.response.defer(); await interaction.delete_original_response(); await self.refresh_message(game)

    async def play_hand_interaction(self,interaction,game_id,position,target=None,x_value=None):
        game=self.games.get(game_id)
        if not game or interaction.user.id not in game.order:
            await interaction.response.send_message("This private hand is unavailable.",ephemeral=True); return
        async with self.lock(game.game_id):
            try:
                card=game.hand(interaction.user.id)[position-1]
                game.play(interaction.user.id,position,target,x_value); game.record(interaction.user.id,"play"); self.advance_automatic(game); await self.save(game)
            except (GameError,IndexError,ValueError) as error:
                await interaction.response.send_message(str(error),ephemeral=True); return
        await interaction.response.defer()
        await interaction.delete_original_response()
        await self.refresh_message(game)

    async def send_library_search(self,interaction,game_id,page,editing=False):
        game=self.games.get(game_id); user=interaction.user.id
        pending=bool(game and user in game.order and game.stack and game.stack[-1].decision_pending and game._spell_decider(game.stack[-1])==user and not game.stack[-1].ability_effect and game.card(game.stack[-1].uid).effect=="search_library")
        if not pending:
            content="This private library search is unavailable."
            if editing: await interaction.edit_original_response(content=content,view=None)
            else: await interaction.followup.send(content,ephemeral=True)
            return
        entries=game.library_search(user); pages=max(1,math.ceil(len(entries)/LibrarySearchView.page_size)); page=max(0,min(page,pages-1)); start=page*LibrarySearchView.page_size; visible=entries[start:start+LibrarySearchView.page_size]
        text="Demonic Tutor - private library search\n"+"\n".join(f"**{position}. {card.name}** - {card.kind}, {card.mana_cost or 'no mana cost'}" for position,card in visible)
        view=LibrarySearchView(self,game_id,user,page,pages,game)
        if editing: await interaction.edit_original_response(content=text,view=view)
        else: await interaction.followup.send(text,view=view,ephemeral=True)
    async def send_natural_selection(self,interaction,game_id,editing=False):
        game=self.games.get(game_id); user=interaction.user.id
        try: _,target,entries=game.natural_selection_decision(user) if game else (_ for _ in ()).throw(GameError("This private library choice is unavailable."))
        except GameError:
            content="This private library choice is unavailable."
            if editing: await interaction.edit_original_response(content=content,view=None)
            else: await interaction.followup.send(content,ephemeral=True)
            return
        text=f"Natural Selection - top of {target.user_id}'s library\n"+"\n".join(f"**{position}. {card.name}** - {card.kind}, {card.mana_cost or 'no mana cost'}" for position,card in entries)
        view=NaturalSelectionView(self,game_id,user,entries)
        if editing: await interaction.edit_original_response(content=text,view=view)
        else: await interaction.followup.send(text,view=view,ephemeral=True)
    async def complete_natural_selection(self,interaction,game_id,order,shuffle):
        game=self.games.get(game_id)
        if not game: await interaction.response.send_message("This match is unavailable.",ephemeral=True); return
        async with self.lock(game.game_id):
            try: game.choose_natural_selection(interaction.user.id,order,shuffle); game.record(interaction.user.id,"natural_selection_shuffle" if shuffle else "natural_selection_order"); self.advance_automatic(game); await self.save(game)
            except (GameError,ValueError) as error: await interaction.response.send_message(str(error),ephemeral=True); return
        await interaction.response.edit_message(content="Natural Selection completed.",view=None); await self.refresh_message(game)

    async def send_private_hand_decision(self,interaction,game_id,page,editing=False):
        game=self.games.get(game_id); user=interaction.user.id
        try: item,entries=game.private_hand_decision(user) if game else (_ for _ in ()).throw(GameError("This private hand decision is unavailable."))
        except GameError:
            content="This private hand decision is unavailable."
            if editing: await interaction.edit_original_response(content=content,view=None)
            else: await interaction.followup.send(content,ephemeral=True)
            return
        pages=max(1,math.ceil(len(entries)/PrivateHandDecisionView.page_size)); page=max(0,min(page,pages-1)); start=page*PrivateHandDecisionView.page_size
        visible=entries[start:start+PrivateHandDecisionView.page_size]
        effect=item.ability_effect if item is not None else "cleanup_discard"; name=game.card(item.uid).name if item is not None else "Cleanup"
        if effect=="discard_choice": heading=f"{name} - choose one card to discard"
        elif effect=="balance_hand": heading=f"{name} - privately choose {game._balance_required(item,user)} cards to discard"
        elif effect=="cleanup_discard": heading=f"Cleanup - privately choose {len(game.player(user).hand)-7} cards to discard"
        elif effect=="leng_discard": heading=f"Library of Leng - choose a destination for {visible[0][1].name}"
        elif effect=="word_choose": heading=f"Word of Command - choose a card to play from {item.target}s hand; use the word command for targets or X"
        elif effect=="mask_choose": heading=f"Illusionary Mask - privately choose an eligible creature or decline"
        else: heading=f"{name} - targeted hand"
        text=heading+"\n"+("\n".join(f"**{position}. {card.name}** - {card.kind}, {card.mana_cost or 'no mana cost'}" for position,card in visible) or "The targeted hand is empty.")
        view=PrivateHandDecisionView(self,game_id,user,page,pages,effect,entries)
        if editing: await interaction.edit_original_response(content=text,view=view)
        else: await interaction.followup.send(text,view=view,ephemeral=True)
    async def complete_private_hand_interaction(self,interaction,game_id,position):
        game=self.games.get(game_id)
        if not game:
            await interaction.response.send_message("This match is unavailable.",ephemeral=True); return
        async with self.lock(game.game_id):
            try:
                effect="cleanup_discard" if game.phase=="cleanup_discard" else game.stack[-1].ability_effect
                if effect=="cleanup_discard": game.choose_cleanup_discard(interaction.user.id,position if isinstance(position,(list,tuple)) else [position]); action="cleanup_discard"
                elif effect=="balance_hand": game.choose_balance(interaction.user.id,position); action="balance_hand_choice"
                elif effect=="word_choose": game.choose_word_command(interaction.user.id,position); action="word_of_command_choice"
                elif effect=="mask_choose": game.choose_illusionary_mask(interaction.user.id,position); action="illusionary_mask_choice"
                else: game.choose_private_hand(interaction.user.id,position); action="private_discard" if effect=="discard_choice" else "private_hand_view"
                game.record(interaction.user.id,action); self.advance_automatic(game); await self.save(game)
            except (GameError,IndexError,ValueError) as error:
                await interaction.response.send_message(str(error),ephemeral=True); return
        message=("Illusionary Mask choice completed." if effect=="mask_choose" else "Word of Command choice completed." if effect=="word_choose" else "Card discarded." if position is not None else "Hand view completed.")
        await interaction.response.edit_message(content=message,view=None); await self.refresh_message(game)

    async def complete_discard_destination(self,interaction,game_id,to_library):
        game=self.games.get(game_id)
        if not game:
            await interaction.response.send_message("This match is unavailable.",ephemeral=True); return
        async with self.lock(game.game_id):
            try:
                game.choose_discard_destination(interaction.user.id,to_library); game.record(interaction.user.id,"library_of_leng_top" if to_library else "library_of_leng_graveyard"); self.advance_automatic(game); await self.save(game)
            except (GameError,IndexError,ValueError) as error:
                await interaction.response.send_message(str(error),ephemeral=True); return
        await interaction.response.edit_message(content="Discard destination chosen.",view=None); await self.refresh_message(game)

    async def choose_library_interaction(self,interaction,game_id,position):
        game=self.games.get(game_id)
        if not game:
            await interaction.response.send_message("This match is unavailable.",ephemeral=True); return
        async with self.lock(game.game_id):
            try:
                game.choose_library(interaction.user.id,position); game.record(interaction.user.id,"search_library"); self.advance_automatic(game); await self.save(game)
            except (GameError,IndexError,ValueError) as error:
                await interaction.response.send_message(str(error),ephemeral=True); return
        await interaction.response.edit_message(content="Library search completed; the chosen card was added to your hand.",view=None)
        await self.refresh_message(game)
    async def act(self,i,game_id,action,label):
        game=self.games.get(game_id)
        if not game:
            await i.response.send_message("This match is unavailable.",ephemeral=True); return
        async with self.lock(game.game_id):
            try:
                action(game); game.record(i.user.id,label); self.advance_automatic(game); await self.save(game)
            except (GameError,IndexError,ValueError) as e: await i.response.send_message(str(e),ephemeral=True); return
            await i.response.defer()
            embed,file=await self.game_message(game)
            await i.edit_original_response(embed=embed,attachments=[file] if file else [],view=None if game.finished else GameView(self,game.game_id))
    async def mutate_ctx(self,ctx,action,label):
        try: game=self.find(ctx.author.id)
        except GameError as e: await ctx.send(str(e)); return
        async with self.lock(game.game_id):
            try:
                action(game); game.record(ctx.author.id,label); self.advance_automatic(game); await self.save(game)
            except (GameError,IndexError,ValueError) as e: await ctx.send(str(e)); return
        await self.refresh_message(game)
        await ctx.message.add_reaction("✅")
    @commands.group(name="mtg",invoke_without_command=True)
    async def mtg(self,ctx):
        """Collect cards, build toward custom decks, and play Magic.

        New players choose `starter red` or `starter green`, then use `collection`
        to browse owned printings. Start a match with `solo` or `challenge`; gameplay
        uses buttons and private hand controls, with `action` as a text fallback.
        """
        await ctx.send_help()
    @mtg.command(name="starter")
    async def starter_choice(self,ctx,color:str):
        """Choose your permanent red or green starter collection."""
        try: profile=await self.claim_starter(ctx.author.id,color)
        except GameError as error: await ctx.send(str(error)); return
        records=await self.collection_records(ctx.author.id); view=CollectionView(self,ctx.author.id,records,0)
        await ctx.send(f"You received the **{profile['starter'].title()} Starter**: 60 owned cards and its saved deck.",embed=self.collection_embed(profile,records,0),view=view)
    @mtg.command(name="collection")
    async def collection(self,ctx,page:int=1):
        """Browse your owned cards and printings."""
        profile=await self.player_profile(ctx.author.id)
        if not profile.get("starter"):
            await ctx.send(f"Choose your starter first with `{ctx.clean_prefix}mtg starter red` or `{ctx.clean_prefix}mtg starter green`."); return
        records=await self.collection_records(ctx.author.id); pages=max(1,math.ceil(len(records)/CollectionView.page_size))
        if not 1<=page<=pages: await ctx.send(f"Choose a page from 1 to {pages}."); return
        view=CollectionView(self,ctx.author.id,records,page-1); await ctx.send(embed=self.collection_embed(profile,records,page-1),view=view)
    @mtg.group(name="deck",invoke_without_command=True)
    async def deck(self,ctx):
        """Create, inspect, edit, and select saved decks."""
        profile=await self.player_profile(ctx.author.id)
        if not profile.get("starter"): await ctx.send(f"Choose a starter with `{ctx.clean_prefix}mtg starter red` or `green`."); return
        lines=[]
        for deck_id,item in profile.get("decks",{}).items():
            _,errors=resolve_deck(item.get("cards",{}),profile.get("collection",{}),CARDS)
            flags=(" · active" if deck_id==profile.get("active_deck") else "")+(" · valid" if not errors else " · invalid")
            lines.append(f"`{deck_id}` — **{item.get('name',deck_id)}**{flags}")
        await ctx.send("**Your MTG decks**\n"+"\n".join(lines))
    @deck.command(name="create")
    async def deck_create(self,ctx,*,name:str):
        """Clone your active deck under a new editable name."""
        try: deck_id,profile=await self.create_custom_deck(ctx.author.id,name)
        except GameError as error: await ctx.send(str(error)); return
        await ctx.send(f"Created `{deck_id}` by cloning your active deck. Use the exact printing keys shown in `mtg collection` to edit it.",embed=self.deck_embed(profile,deck_id))
    @deck.command(name="show")
    async def deck_show(self,ctx,*,deck_name:str=None):
        """Show a saved deck and its current validity."""
        try: profile=await self.player_profile(ctx.author.id); deck_id,_=self.find_saved_deck(profile,deck_name)
        except GameError as error: await ctx.send(str(error)); return
        await ctx.send(embed=self.deck_embed(profile,deck_id))
    @deck.command(name="add")
    async def deck_add(self,ctx,deck_id:str,printing_key:str,count:int=1):
        """Add owned printing copies to an editable deck."""
        try: deck_id,profile=await self.edit_saved_deck(ctx.author.id,deck_id,printing_key,count,True)
        except GameError as error: await ctx.send(str(error)); return
        await ctx.send(embed=self.deck_embed(profile,deck_id))
    @deck.command(name="remove")
    async def deck_remove(self,ctx,deck_id:str,printing_key:str,count:int=1):
        """Remove preferred printing copies from an editable deck."""
        try: deck_id,profile=await self.edit_saved_deck(ctx.author.id,deck_id,printing_key,count,False)
        except GameError as error: await ctx.send(str(error)); return
        await ctx.send(embed=self.deck_embed(profile,deck_id))
    @deck.command(name="select")
    async def deck_select(self,ctx,*,deck_name:str):
        """Select a valid saved deck for future matches."""
        try: deck_id,profile=await self.select_saved_deck(ctx.author.id,deck_name)
        except GameError as error: await ctx.send(str(error)); return
        await ctx.send(f"Selected **{profile['decks'][deck_id]['name']}** for future matches.")
    @mtg.group(name="packs",invoke_without_command=True)
    async def packs(self,ctx):
        """View unopened solo-victory packs."""
        profile=await self.player_profile(ctx.author.id)
        await ctx.send(f"You have **{int(profile.get('unopened_packs',0))}** unopened MTG pack(s). Each contains 4 commons, 2 uncommons, 1 rare or mythic, and 1 guaranteed land.")
    @packs.command(name="open")
    async def packs_open(self,ctx):
        """Open one earned eight-card pack."""
        try: cards,profile=await self.open_pack(ctx.author.id)
        except GameError as error: await ctx.send(str(error)); return
        counts=Counter(cards); lines=[f"**{CARDS[key].name}** ×{count} · {CARDS[key].rarity.title()} · `{key}`" for key,count in counts.items()]
        await ctx.send("**Pack opened**\n"+"\n".join(lines)+f"\n\nUnopened packs remaining: **{profile['unopened_packs']}**")
    @mtg.group(name="trade",invoke_without_command=True)
    async def trade(self,ctx):
        """Create and manage exact-quantity card trades."""
        trades=await self.config.trades(); own=[item for item in trades.values() if ctx.author.id in [int(user) for user in item["users"]]]
        if not own: await ctx.send(f"No pending trades. Start one with `{ctx.clean_prefix}mtg trade create @member`."); return
        await ctx.send("**Pending MTG trades**\n"+"\n".join(f"`#{item['trade_id']}` with <@{next(int(user) for user in item['users'] if int(user)!=ctx.author.id)}>" for item in own),allowed_mentions=discord.AllowedMentions.none())
    @trade.command(name="create")
    @commands.guild_only()
    async def trade_create(self,ctx,member:discord.Member):
        """Start a trade with another collection owner."""
        if member.bot or member.id==ctx.author.id: await ctx.send("Trade with another human member."); return
        try: trade=await self.create_trade(ctx.author.id,member.id)
        except GameError as error: await ctx.send(str(error)); return
        message=await ctx.send(content=f"{member.mention}, build this trade together with `mtg trade add {trade['trade_id']} PRINTING_KEY QUANTITY`.",embed=await self.trade_embed(trade),view=TradeView(self,trade["trade_id"]),allowed_mentions=discord.AllowedMentions(users=True))
        await self.bind_trade_message(trade["trade_id"],ctx.channel.id,message.id)
    @trade.command(name="add")
    async def trade_add(self,ctx,trade_id:int,printing_key:str,count:int=1):
        """Add an exact printing and quantity to your offer."""
        try: trade=await self.modify_trade(trade_id,ctx.author.id,printing_key,count,True)
        except GameError as error: await ctx.send(str(error)); return
        await self.refresh_trade(trade); await ctx.send(f"Updated trade #{trade_id}; both confirmations were cleared.")
    @trade.command(name="remove")
    async def trade_remove(self,ctx,trade_id:int,printing_key:str,count:int=1):
        """Remove an exact printing quantity from your offer."""
        try: trade=await self.modify_trade(trade_id,ctx.author.id,printing_key,count,False)
        except GameError as error: await ctx.send(str(error)); return
        await self.refresh_trade(trade); await ctx.send(f"Updated trade #{trade_id}; both confirmations were cleared.")
    @trade.command(name="show")
    async def trade_show(self,ctx,trade_id:int):
        """Show a pending trade and deck-impact warnings."""
        try: trade=await self.get_trade(trade_id)
        except GameError as error: await ctx.send(str(error)); return
        if ctx.author.id not in [int(user) for user in trade["users"]]: await ctx.send("You are not part of that trade."); return
        await ctx.send(embed=await self.trade_embed(trade),view=TradeView(self,trade_id))
    @trade.command(name="confirm")
    async def trade_confirm(self,ctx,trade_id:int):
        """Confirm the current exact trade offer."""
        try: trade,done=await self.confirm_trade(trade_id,ctx.author.id)
        except GameError as error: await ctx.send(str(error)); return
        await self.refresh_trade(trade)
        if done: await ctx.send(f"Trade #{trade_id} completed. Ownership transferred and the audit record was saved.",embed=await self.trade_embed(trade))
        else: await ctx.send(f"Confirmed trade #{trade_id}; waiting for the other player.")
    @trade.command(name="cancel")
    async def trade_cancel(self,ctx,trade_id:int):
        """Cancel a pending trade."""
        try: trade=await self.cancel_trade(trade_id,ctx.author.id)
        except GameError as error: await ctx.send(str(error)); return
        await self.refresh_trade(trade); await ctx.send(f"Trade #{trade_id} cancelled.",embed=await self.trade_embed(trade))
    @mtg.command(name="challenge")
    @commands.guild_only()
    async def challenge(self,ctx,member:discord.Member,*,deck_name:str=None):
        """Challenge another member using owned saved decks."""
        if member.bot or member.id==ctx.author.id: await ctx.send("Challenge another human member."); return
        try:
            challenger=await self.player_profile(ctx.author.id); opponent=await self.player_profile(member.id)
            if not challenger.get("starter") or not opponent.get("starter"): raise GameError("Both players must choose a starter collection first.")
            challenger_id,challenger_deck=self.find_saved_deck(challenger,deck_name); self.resolve_saved_deck(challenger,challenger_id)
            opponent_decks=[]
            for deck_id,deck in opponent.get("decks",{}).items():
                try: self.resolve_saved_deck(opponent,deck_id)
                except GameError: continue
                opponent_decks.append((deck_id,deck.get("name",deck_id)))
            if not opponent_decks: raise GameError("The challenged player has no valid deck.")
        except GameError as error: await ctx.send(str(error)); return
        await ctx.send(f"{member.mention}, {ctx.author.mention} challenged you with **{challenger_deck['name']}**. Choose one of your valid decks below.",view=ChallengeView(self,ctx.author.id,member.id,challenger_id,opponent_decks),allowed_mentions=discord.AllowedMentions(users=True))
    @mtg.command(name="solo")
    @commands.guild_only()
    async def solo(self,ctx,deck_name:str=None,difficulty:str="easy"):
        """Start solo with your selected saved deck. Difficulty: easy/normal."""
        if deck_name and deck_name.casefold() in DIFFICULTIES and difficulty=="easy": difficulty,deck_name=deck_name.casefold(),None
        difficulty=difficulty.casefold()
        if difficulty not in DIFFICULTIES: await ctx.send(f"Use `{ctx.clean_prefix}mtg solo [deck ID] [easy|normal]`."); return
        if not self.bot.user: await ctx.send("The solo opponent is not ready yet."); return
        try:
            profile=await self.player_profile(ctx.author.id)
            if not profile.get("starter"): raise GameError(f"Choose a starter first with `{ctx.clean_prefix}mtg starter red` or `green`.")
            deck_id,deck=self.find_saved_deck(profile,deck_name); cards=self.resolve_saved_deck(profile,deck_id)
            game=await self.create_solo_game(ctx.author.id,self.bot.user.id,ctx.channel.id,deck.get("name",deck_id),difficulty,cards,profile["starter"])
        except GameError as e: await ctx.send(str(e)); return
        embed,file=await self.game_message(game)
        message=await ctx.send(embed=embed,file=file,view=GameView(self,game.game_id)) if file else await ctx.send(embed=embed,view=GameView(self,game.game_id))
        game.message_id=message.id; await self.save(game)
    @mtg.command(name="status")
    async def status(self,ctx):
        try: game=self.find(ctx.author.id)
        except GameError as e: await ctx.send(str(e)); return
        async with self.lock(game.game_id):
            if self.advance_automatic(game): await self.save(game)
        embed,file=await self.game_message(game)
        if file: await ctx.send(embed=embed,file=file)
        else: await ctx.send(embed=embed)
    @mtg.group(name="action",invoke_without_command=True)
    async def action(self,ctx):
        """Commands used during an active match."""
        if ctx.invoked_subcommand is None: await ctx.send_help()
    @action.group(name="special",invoke_without_command=True)
    async def special(self,ctx):
        """Fallbacks for choices normally shown as buttons or menus."""
        if ctx.invoked_subcommand is None: await ctx.send_help()
    def catalog_records(self,scope,search=""):
        records=[]
        if scope=="all":
            records.extend(("playable",card) for card in BASE_CARDS.values())
            records.extend(("alpha",card) for card in ALPHA_CARDS)
        elif scope=="playable":
            records.extend(("alpha" if card.set_code=="lea" else "playable",card) for card in CARDS.values())
        else: records.extend(("alpha",card) for card in ALPHA_CARDS)
        if search:
            folded=search.casefold()
            records=[item for item in records if folded in item[1].name.casefold() or folded in item[1].key.casefold()]
        return sorted(records,key=lambda item:(item[1].name.casefold(),item[1].key.casefold()))

    def catalog_embed(self,browser):
        start=browser.page*browser.page_size; visible=browser.records[start:start+browser.page_size]
        def cell(value,width):
            value=str(value).replace("\n"," ").replace("\r"," ").replace("`","'")
            return (value if len(value)<=width else value[:width-1]+"…").ljust(width)
        lines=[f"{'#':>3}  {'Card':<25} {'Mana':<9} {'Type':<12} Status",f"{'—'*3}  {'—'*25} {'—'*9} {'—'*12} {'—'*9}"]
        for number,(source,card) in enumerate(visible,start+1):
            if source=="alpha":
                status="Ready" if card.engine_status=="playable" else "Reference"
                card_type=card.type_line.split(" — ",1)[0]
            else:
                status="Ready"; card_type=card.kind.title()
            lines.append(f"{number:>3}  {cell(card.name,25)} {cell(card.mana_cost or '—',9)} {cell(card_type,12)} {status}")
        title={"all":"MTG card catalog","playable":"Supported playable catalog","alpha":"Limited Edition Alpha catalog"}[browser.scope]
        description="Choose a numbered row from the menu below to open its card.\n\n```text\n"+"\n".join(lines)+"\n```"
        embed=discord.Embed(title=title,description=description,color=discord.Color.dark_green())
        summary=f"Page {browser.page+1}/{browser.pages} · {len(browser.records)} matching records"
        if not browser.search and browser.scope=="all": summary+=f" · {len(CARDS)} playable definitions · {len(BASE_CARDS)} core + {ALPHA_SET['printing_count']} Alpha printings"
        embed.set_footer(text=summary+" · Select a row for card details")
        return embed

    def find_catalog_card(self,query):
        raw=query.strip(); folded=raw.casefold(); alpha_only=folded.startswith("alpha " )
        if alpha_only: raw=raw[6:].strip(); folded=raw.casefold()
        matches=[] if alpha_only else [card for card in CARDS.values() if folded==card.key.casefold() or folded==card.name.casefold()]
        if not matches and not alpha_only: matches=[card for card in CARDS.values() if folded in card.name.casefold()]
        alpha_matches=search_alpha(raw) if alpha_only or not matches else []
        if matches and matches[0].set_code=="lea": alpha_matches=search_alpha(matches[0].key); matches=[]
        if alpha_matches: return "alpha",alpha_matches[0],len(alpha_matches)
        if matches: return "playable",matches[0],len(matches)
        return None,None,0

    async def card_presentation(self,source,card,match_count=1):
        if source=="alpha":
            description=card.oracle_text or "No current Oracle rules text."
            color=discord.Color.dark_green() if card.engine_status=="playable" else discord.Color.dark_gold()
            embed=discord.Embed(title=card.name,description=description,color=color)
            embed.add_field(name="Type",value=card.type_line,inline=False)
            embed.add_field(name="Mana",value=card.mana_cost or "None")
            if card.power is not None: embed.add_field(name="Power / toughness",value=f"{card.power} / {card.toughness}")
            embed.add_field(name="Alpha printing",value=f"`{card.key}` · #{card.collector_number} · {card.rarity.title()}",inline=False)
            status=("Playable in the supported rules subset; not included in the fixed starters or pack pools." if card.engine_status=="playable" else "Reference only — its mechanics are not implemented yet.")
            embed.add_field(name="Engine status",value=status,inline=False)
            if match_count>1: embed.add_field(name="Alternate Alpha art",value=f"{match_count} printings share this card name; use an exact `lea:NUMBER` key.",inline=False)
            source_url=f"https://scryfall.com/card/lea/{card.collector_number}"
        else:
            embed=discord.Embed(title=card.name,description=card.text or "No rules text.",color=discord.Color.dark_green())
            embed.add_field(name="Type",value=card.kind); embed.add_field(name="Mana",value=card.mana_cost or "None")
            if card.creature: embed.add_field(name="Power / toughness",value=f"{card.power} / {card.toughness}")
            embed.add_field(name="Catalog key",value=f"`{card.key}`")
            embed.add_field(name="Engine status",value="Playable in the supported rules subset.",inline=False)
            source_url=f"https://scryfall.com/card/{card.scryfall_id}"
        embed.add_field(name="Oracle ID",value=f"`{card.oracle_id}`",inline=False)
        embed.add_field(name="Source",value=f"[Scryfall]({source_url})",inline=False)
        embed.set_footer(text="Card data and images: Scryfall. Unofficial fan content; not approved by Wizards.")
        filename=card.key.replace(":","-")+".jpg"
        try:
            path=await self.art_cache.get(card); file=discord.File(path,filename=filename); embed.set_image(url=f"attachment://{filename}")
            return embed,file
        except (ArtError,aiohttp.ClientError,asyncio.TimeoutError,OSError):
            log.warning("Could not load card detail art for %s",card.key,exc_info=True); return embed,None

    async def show_catalog_page(self,interaction,browser,page):
        view=CatalogView(self,browser.user_id,browser.records,browser.scope,browser.search,page)
        kwargs={"embed":self.catalog_embed(view),"attachments":[],"view":view}
        if interaction.response.is_done(): await interaction.edit_original_response(**kwargs)
        else: await interaction.response.edit_message(**kwargs)

    async def show_catalog_detail(self,interaction,browser,index):
        source,card=browser.records[index]
        embed,file=await self.card_presentation(source,card)
        attribution=embed.footer.text
        embed.set_footer(text=f"Card {index+1}/{len(browser.records)} · Up returns to page {browser.page+1}/{browser.pages} · {attribution}")
        await interaction.edit_original_response(embed=embed,attachments=[file] if file else [],view=CatalogDetailView(browser,index))

    @mtg.command(name="card")
    async def card_detail(self,ctx,*,query:str):
        """Show a supported card or an original Alpha printing."""
        if not query.strip(): await ctx.send("Give me a card name, catalog key, or Alpha printing key."); return
        source,card,count=self.find_catalog_card(query)
        if not card: await ctx.send("No catalog card matched that search."); return
        embed,file=await self.card_presentation(source,card,count)
        if file: await ctx.send(embed=embed,file=file,allowed_mentions=discord.AllowedMentions.none())
        else: await ctx.send(embed=embed,allowed_mentions=discord.AllowedMentions.none())

    @mtg.command(name="catalog")
    async def catalog(self,ctx,*,query:str=None):
        """Interactively browse cards. Syntax: catalog [all|playable|alpha] [page|search]."""
        tokens=(query or "").split(); scope="all"; page=1
        if tokens and tokens[0].casefold() in ("all","playable","alpha"): scope=tokens.pop(0).casefold()
        if tokens and tokens[0].isdigit(): page=int(tokens.pop(0))
        search=" ".join(tokens); records=self.catalog_records(scope,search)
        if not records: await ctx.send("No catalog cards matched that search."); return
        pages=math.ceil(len(records)/CatalogView.page_size)
        if page<1 or page>pages: await ctx.send(f"Choose a page from 1 to {pages}."); return
        view=CatalogView(self,ctx.author.id,records,scope,search,page-1)
        await ctx.send(embed=self.catalog_embed(view),view=view,allowed_mentions=discord.AllowedMentions.none())
    @action.command(name="graveyard")
    async def graveyard(self,ctx,member:discord.Member=None):
        """List a player graveyard and its G:POSITION spell targets."""
        try: game=self.find(ctx.author.id)
        except GameError as e: await ctx.send(str(e)); return
        target=member.id if member else ctx.author.id
        player=game.player(target)
        names={user:str(self.bot.get_user(user).display_name if self.bot.get_user(user) else user).replace("\n"," ")[:32] for user in game.order}
        lines=[f"{position}. {game.card(uid).name}" for position,uid in enumerate(player.graveyard,1)]
        if not lines: await ctx.send(f"{names[target]} has no cards in their graveyard.",allowed_mentions=discord.AllowedMentions.none()); return
        pages=[]; current=f"**{names[target]} graveyard** — bottom → top; target your own cards with G:POSITION or another player with USER_ID:G:POSITION\n"
        for line in lines:
            if len(current)+len(line)+1>1900: pages.append(current); current=""
            current+=line+"\n"
        pages.append(current)
        for page in pages: await ctx.send(page,allowed_mentions=discord.AllowedMentions.none())
    @action.command(name="mana")
    async def mana(self,ctx,position:int,color:str=None):
        """Add mana from a battlefield permanent."""
        await self.mutate_ctx(ctx,lambda g:g.activate_mana(ctx.author.id,position,color),"mana")
    @special.command(name="vault")
    async def vault(self,ctx,choice:str,position:int=None):
        """Resolve a Time Vault turn choice."""
        normalized=choice.casefold()
        if normalized not in ("take","skip") or (normalized=="skip" and position is None): await ctx.send("Choose `take` or `skip POSITION`."); return
        await self.mutate_ctx(ctx,lambda g:g.choose_time_vault_turn(ctx.author.id,normalized=="skip",position),f"vault_{normalized}")
    @special.command(name="sanctuary")
    async def sanctuary(self,ctx,choice:str):
        """Resolve an Island Sanctuary draw choice."""
        normalized=choice.casefold()
        if normalized not in ("draw","skip"): await ctx.send("Choose `draw` or `skip`."); return
        await self.mutate_ctx(ctx,lambda g:g.choose_sanctuary_draw(ctx.author.id,normalized=="skip"),f"sanctuary_{normalized}")
    @special.command(name="channel")
    async def channel(self,ctx,amount:int=1):
        """Pay life for Channel mana."""
        await self.mutate_ctx(ctx,lambda g:g.activate_channel(ctx.author.id,amount),"channel")
    @special.command(name="angel")
    async def angel(self,ctx,target:str):
        """Buy Guardian Angel prevention."""
        await self.mutate_ctx(ctx,lambda g:g.activate_guardian_angel(ctx.author.id,target),"guardian_angel")
    @special.command(name="incarnation")
    async def incarnation(self,ctx,controller_id:int,position:int):
        """Use an owned Personal Incarnation."""
        await self.mutate_ctx(ctx,lambda g:g.activate_personal_incarnation(ctx.author.id,controller_id,position),"activate_owned_incarnation")
    @special.command(name="hydra")
    async def hydra(self,ctx,position:int,mode:str):
        """Use a Rock Hydra ability."""
        await self.mutate_ctx(ctx,lambda g:g.activate_hydra(ctx.author.id,position,mode),f"hydra_{mode.casefold()}")
    @special.command(name="hydraorder")
    async def hydraorder(self,ctx,position:int,order:str):
        """Set Rock Hydra damage ordering."""
        await self.mutate_ctx(ctx,lambda g:g.choose_hydra_order(ctx.author.id,position,order),"hydra_order")
    @special.command(name="mask")
    async def mask(self,ctx,position:int,x_value:int):
        """Activate Illusionary Mask."""
        await self.mutate_ctx(ctx,lambda g:g.activate_illusionary_mask(ctx.author.id,position,x_value),"illusionary_mask")
    @special.command(name="maskpick")
    async def maskpick(self,ctx,choice:str):
        """Choose Mask’s creature or decline."""
        position=None
        if choice.casefold()!="decline":
            try: position=int(choice)
            except ValueError:
                await ctx.send("Choose an eligible hand position or `decline`."); return
        await self.mutate_ctx(ctx,lambda g:g.choose_illusionary_mask(ctx.author.id,position),"illusionary_mask_choice")
    @special.command(name="activate")
    async def activate(self,ctx,position:int,target:str=None,x_value:int=None,choice_value:int=None):
        """Activate a supported card ability."""
        normalized=None if target and target.casefold() in {"-","none"} else target
        await self.mutate_ctx(ctx,lambda g:g.activate_ability(ctx.author.id,position,normalized,x_value,choice_value),"activate")
    @action.command(name="play")
    async def play(self,ctx,position:int,target:str=None,x_value:int=None):
        """Play a land or cast a card from your hand."""
        normalized=None if target and target.casefold() in {"-","none"} else target
        await self.mutate_ctx(ctx,lambda g:g.play(ctx.author.id,position,normalized,x_value),"play")
    @special.command(name="forktarget")
    async def forktarget(self,ctx,target:str="keep"):
        """Choose targets for a Fork copy."""
        await self.mutate_ctx(ctx,lambda g:g.choose_fork_target(ctx.author.id,target),"fork_target_choice")
    @action.command(name="attack")
    async def attack(self,ctx,*groups:str):
        """Declare attackers by battlefield position."""
        try:
            parsed=[[int(position) for position in group.split("+")] for group in groups]
        except ValueError:
            await ctx.send("Use battlefield positions, joining band members with `+`, such as `1 2+3`."); return
        positions=[position for group in parsed for position in group]; bands=[group for group in parsed if len(group)>1]
        await self.mutate_ctx(ctx,lambda g:g.declare_attackers(ctx.author.id,positions,bands),"attack")
    @special.command(name="bodyguard")
    async def bodyguard(self,ctx,position:int):
        """Choose a Veteran Bodyguard."""
        await self.mutate_ctx(ctx,lambda g:g.choose_bodyguard(ctx.author.id,position),"choose_bodyguard")
    @special.command(name="trample")
    async def trample(self,ctx,position:int,damage_to_blocker:int):
        """Set trample damage assignment."""
        await self.mutate_ctx(ctx,lambda g:g.assign_trample(ctx.author.id,position,damage_to_blocker),"trample")
    @action.command(name="block")
    async def block(self,ctx,*assignments:str):
        """Declare blockers by battlefield position."""
        def run(g):
            pairs=[]
            for item in assignments:
                a,b=item.split(":",1); pairs.append((int(a),int(b)))
            g.declare_blockers(ctx.author.id,pairs)
        await self.mutate_ctx(ctx,run,"block")
    @special.command(name="attackdamage")
    async def attackdamage(self,ctx,attacker_position:int,*assignments:str):
        """Divide damage among blockers."""
        def run(g):
            parsed=[]
            for item in assignments:
                blocker,damage=item.split(":",1); parsed.append((int(blocker),int(damage)))
            g.assign_attacker_damage(ctx.author.id,attacker_position,parsed)
        await self.mutate_ctx(ctx,run,"attacker_damage")
    @special.command(name="blockdamage")
    async def blockdamage(self,ctx,blocker_position:int,*assignments:str):
        """Divide damage among attackers."""
        def run(g):
            parsed=[]
            for item in assignments:
                attacker,damage=item.split(":",1); parsed.append((int(attacker),int(damage)))
            g.assign_blocker_damage(ctx.author.id,blocker_position,parsed)
        await self.mutate_ctx(ctx,run,"blocker_damage")
    @special.command(name="untap")
    async def untap(self,ctx,*positions:int):
        """Resolve a restricted untap."""
        await self.mutate_ctx(ctx,lambda g:g.choose_untap(ctx.author.id,positions),"untap")
    @action.command(name="pass")
    async def pass_(self,ctx): await self.mutate_ctx(ctx,lambda g:g.pass_priority(ctx.author.id),"pass")
    @special.command(name="trigger")
    async def trigger(self,ctx,choice:str,position:int=None):
        """Resolve a pending trigger."""
        normalized=choice.casefold()
        if normalized not in ("pay","draw","decline","sacrifice"): await ctx.send("Choose `pay`, `draw`, `decline`, or `sacrifice POSITION`."); return
        if normalized=="sacrifice" and position is None: await ctx.send("Provide the battlefield position to sacrifice."); return
        await self.mutate_ctx(ctx,lambda g:g.choose_trigger(ctx.author.id,normalized!="decline",position if normalized=="sacrifice" else None),f"trigger_{normalized}")
    @special.command(name="wording")
    async def wording(self,ctx,source:str,target:str):
        """Choose a replacement word."""
        await self.mutate_ctx(ctx,lambda g:g.choose_word_change(ctx.author.id,source,target),"word_change_choice")

    @special.command(name="orders")
    async def orders(self,ctx,choice:str):
        """Resolve False Orders."""
        if choice.casefold()=="decline": position=None
        else:
            try: position=int(choice)
            except ValueError: await ctx.send("Use `mtg action special orders ATTACKER_POSITION` or `mtg action special orders decline`."); return
        await self.mutate_ctx(ctx,lambda g:g.choose_false_orders(ctx.author.id,position),"false_orders_choice")
    @special.command(name="kudzu")
    async def kudzu(self,ctx,choice:str,position:int=None):
        """Choose Kudzu’s new land."""
        if choice.casefold()=="decline": controller=None
        else:
            try: controller=int(choice)
            except ValueError: await ctx.send("Use `mtg action special kudzu USER_ID POSITION` or `mtg action special kudzu decline`."); return
            if position is None: await ctx.send("Provide the target land battlefield position."); return
        await self.mutate_ctx(ctx,lambda g:g.choose_kudzu(ctx.author.id,controller,position),"kudzu_choice")

    @special.command(name="balance")
    async def balance(self,ctx,*positions:int):
        """Resolve a Balance choice."""
        await self.mutate_ctx(ctx,lambda g:g.choose_balance(ctx.author.id,positions),"balance_choice")

    @special.command(name="leak")
    async def leak(self,ctx,amount:int):
        """Choose a Power Leak payment."""
        await self.mutate_ctx(ctx,lambda g:g.choose_power_leak(ctx.author.id,amount),"power_leak")
    @special.command(name="selection")
    async def selection(self,ctx,*choices:str):
        """Resolve Natural Selection."""
        if len(choices)==1 and choices[0].casefold()=="shuffle": await self.mutate_ctx(ctx,lambda g:g.choose_natural_selection(ctx.author.id,shuffle=True),"natural_selection_shuffle"); return
        try: order=tuple(int(value) for value in choices)
        except ValueError: await ctx.send("Use `mtg action special selection shuffle` or `mtg action special selection 2 1 3`."); return
        await self.mutate_ctx(ctx,lambda g:g.choose_natural_selection(ctx.author.id,order),"natural_selection_order")
    @special.command(name="copy")
    async def copy(self,ctx,controller_id:str="none",position:int=None):
        """Choose a copyable permanent."""
        if controller_id.casefold()=="none": await self.mutate_ctx(ctx,lambda g:g.choose_copy(ctx.author.id),"copy_none"); return
        try: owner=int(controller_id)
        except ValueError: await ctx.send("Use `mtg action special copy USER_ID POSITION` or `mtg action special copy none`."); return
        if position is None: await ctx.send("Provide the battlefield position to copy."); return
        await self.mutate_ctx(ctx,lambda g:g.choose_copy(ctx.author.id,owner,position),"copy_choice")
    @special.command(name="doppelganger")
    async def doppelganger(self,ctx,choice:str,position:int=None):
        """Resolve Vesuvan Doppelganger."""
        normalized=choice.casefold()
        if normalized in ("copy","keep"):
            await self.mutate_ctx(ctx,lambda g:g.choose_vesuvan_copy(ctx.author.id,accept=normalized=="copy"),f"vesuvan_{normalized}"); return
        try: owner=int(choice)
        except ValueError: await ctx.send("Choose `USER_ID POSITION`, then use `copy` or `keep` when it resolves."); return
        if position is None: await ctx.send("Provide the battlefield position to target."); return
        await self.mutate_ctx(ctx,lambda g:g.choose_vesuvan_copy(ctx.author.id,owner,position),"vesuvan_copy_target")
    @action.command(name="concede")
    async def concede(self,ctx): await self.mutate_ctx(ctx,lambda g:g.concede(ctx.author.id),"concede")
    async def red_delete_data_for_user(self,*,requester,user_id):
        async with self.storage_lock:
            changed=False; games=await self.config.games()
            for key in list(games):
                if user_id in [int(x) for x in games[key].get("order",[])]:
                    games.pop(key); gid=int(key); self.games.pop(gid,None); self.channels.pop(gid,None); self.locks.pop(gid,None); changed=True
            if changed: await self.config.games.set(games)
            trades=await self.config.trades(); trades={key:value for key,value in trades.items() if user_id not in [int(item) for item in value.get("users",[])]}; await self.config.trades.set(trades)
            ledger=await self.config.trade_ledger(); await self.config.trade_ledger.set([item for item in ledger if user_id not in [int(value) for value in item.get("users",[])]])
            await self.config.user_from_id(user_id).clear()
