import asyncio
import logging
import secrets
import math
import aiohttp
from typing import Dict
import discord
from redbot.core import Config, commands
from redbot.core.data_manager import cog_data_path
from .ai import DIFFICULTIES, advance_solo
from .art import HAND_PAGE_SIZE, ArtError, ScryfallArtCache, render_battlefield, render_hand
from .cards import BASE_CARDS, CARDS
from .catalog import ALPHA_CARDS, ALPHA_SET, search_alpha
from .engine import Game, GameError
from .views import CatalogDetailView, CatalogView, ChallengeView, GameView, HandPaginationView

log=logging.getLogger("red.sick-cogs.MTG")
CONFIG_IDENTIFIER=813604927115
DEFAULTS={"schema":1,"next_game_id":1,"games":{}}
MATCH_TIMEOUT_SECONDS=7*24*60*60

class MTG(commands.Cog):
    """Play a deliberately bounded solo or two-player Magic rules prototype."""
    __author__="SickProdigy"
    __version__="0.64.0"
    def __init__(self,bot):
        self.bot=bot; self.config=Config.get_conf(self,identifier=CONFIG_IDENTIFIER,force_registration=True)
        self.config.register_global(**DEFAULTS); self.games:Dict[int,Game]={}; self.locks={}; self.channels={}
        self.storage_lock=asyncio.Lock(); self.cleanup_task=None; self.session=None; self.art_cache=None
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
        for game in resumed: asyncio.create_task(self.refresh_message(game))
        self.cleanup_task=asyncio.create_task(self._cleanup_loop())
    def cog_unload(self):
        if self.cleanup_task: self.cleanup_task.cancel()
        if self.session and not self.session.closed: asyncio.create_task(self.session.close())
    def lock(self,gid): return self.locks.setdefault(gid,asyncio.Lock())
    def human_players(self,game):
        return [user for user in game.order if user != getattr(game,"ai_user",None)]
    def ensure_players_available(self,*users):
        for game in self.games.values():
            if not game.finished and any(user in self.human_players(game) for user in users):
                raise GameError("One player already has an active game.")
    async def create_game(self,a,b,channel):
        async with self.storage_lock:
            self.ensure_players_available(a,b)
            gid=await self.config.next_game_id(); await self.config.next_game_id.set(gid+1)
            users=[a,b]; secrets.SystemRandom().shuffle(users)
            game=Game(gid,users); game.message_id=0; self.games[gid]=game; self.channels[gid]=channel
            await self._save_unlocked(game)
        return game
    async def create_solo_game(self,human,ai,channel,deck,difficulty):
        async with self.storage_lock:
            self.ensure_players_available(human)
            gid=await self.config.next_game_id(); await self.config.next_game_id.set(gid+1)
            users=[human,ai]; secrets.SystemRandom().shuffle(users)
            other="green" if deck=="red" else "red"
            game=Game(gid,users,decks={human:deck,ai:other},ai_user=ai,ai_difficulty=difficulty)
            game.message_id=0; self.games[gid]=game; self.channels[gid]=channel
            advance_solo(game)
            await self._save_unlocked(game)
        return game
    async def _save_unlocked(self,game):
        raw=game.to_raw(); raw["channel_id"]=self.channels.get(game.game_id,0); raw["message_id"]=getattr(game,"message_id",0)
        games=await self.config.games(); games[str(game.game_id)]=raw; await self.config.games.set(games)
    async def save(self,game):
        async with self.storage_lock: await self._save_unlocked(game)
    async def resume_solo_games(self):
        resumed=[]
        for game in list(self.games.values()):
            async with self.lock(game.game_id):
                if advance_solo(game):
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
            if g.priority_user: e.description+=f"\nPriority: **{names[g.priority_user]}**"
            elif g.phase=="attackers": e.description+=f"\nWaiting for **{names[g.active_user]}** to declare attackers."
            elif g.phase=="blockers": e.description+=f"\nWaiting for **{names[g.opponent(g.active_user)]}** to declare blockers."
        for user in g.order:
            p=g.players[user]; field=[]
            for n,x in enumerate(p.battlefield,1):
                c=g.card(x.uid); state=" ↷" if x.tapped else ""
                stats=(lambda value:f" {value[0]}/{value[1]}")(g.current_stats(x)) if g.is_creature(x) else ""
                active=sorted(g.current_keywords(x)-set(c.keywords))
                active_protection=sorted(g.current_protections(x)-set(c.protection_colors))
                ability_parts=[c.ability_text] if c.ability_text else []
                if active: ability_parts.append("Active: "+", ".join(word.title() for word in active))
                if active_protection: ability_parts.append("Active protection: "+"/".join(active_protection))
                if x.color_override: ability_parts.append("Color: "+{"W":"White","U":"Blue","B":"Black","R":"Red","G":"Green"}[x.color_override])
                if x.animated_until_end_combat: ability_parts.append("Animated: 3/6 Golem artifact creature until end of combat")
                granted_regeneration=g.granted_regeneration_cost(x)
                if granted_regeneration: ability_parts.append(f"Granted: {granted_regeneration}: Regenerate this creature")
                if x.regeneration_shields: ability_parts.append(f"Regeneration shield ×{x.regeneration_shields}")
                if x.damage_prevention: ability_parts.append(f"Damage prevention remaining: {x.damage_prevention}")
                if x.plus_one_counters: ability_parts.append(f"+1/+1 counters: {x.plus_one_counters}")
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
            e.add_field(name=f"{names[user]} · {p.life} life · {len(p.hand)} cards",value=value,inline=False)
        if g.extra_turns:
            queued=" → ".join(names[user] for user in g.extra_turns)
            e.add_field(name="Extra turns queued",value=queued,inline=False)
        if g.prevent_combat_damage:
            e.add_field(name="Turn effect",value="All combat damage is prevented this turn.",inline=False)
        if g.trample_assignments:
            choices=[]
            for uid,amount in g.trample_assignments.items():
                _,attacker=g.find_permanent(uid)
                if attacker is not None: choices.append(f"{g.card(uid).name}: {amount} to blocker")
            if choices: e.add_field(name="Trample assignments",value="\n".join(choices),inline=False)
        if g.stack:
            stack_lines=[]
            for position,item in enumerate(reversed(g.stack),1):
                label=g.card(item.uid).name+(" ability" if item.ability_effect else "")
                if item.color_override: label+=f" [{item.color_override}]"
                if not item.ability_effect and "{X}" in g.card(item.uid).mana_cost: label+=f" (X={item.x_value})"
                if item.decision_pending: label+=f" (controller may {g.trigger_accept_label(item)} or Decline)"
                stack_lines.append(f"S:{position}. {label}")
            e.add_field(name="Stack · spells targetable with S:POSITION",value="\n".join(stack_lines),inline=False)
        if g.end_combat_destroys:
            pending=[]
            for item in g.end_combat_destroys:
                _,target=g.find_permanent(int(item.target.split(":")[1])); pending.append(f"{g.card(item.uid).name} → "+(g.card(target.uid).name if target else "departed creature"))
            e.add_field(name="Pending end-of-combat destruction",value="\n".join(pending),inline=False)
        if g.end_step_sacrifices:
            e.add_field(name="Pending end-step trigger",value="Sacrifice "+", ".join(g.card(uid).name for uid in g.end_step_sacrifices)+" · players may respond",inline=False)
        if g.finished: e.description=f"Winner: **{names[g.winner]}** - {g.finished_reason}." if g.winner else f"Match ended - {g.finished_reason}."
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
        text="\n".join(f"**{start+n}. {card.name}** - {card.kind}, {card.mana_cost or 'no mana cost'}" for n,card in enumerate(visible,1)) or "Your hand is empty."
        view=HandPaginationView(self,game_id,interaction.user.id,page,pages) if pages>1 else None
        try:
            image=await asyncio.to_thread(render_hand,cards,paths,page)
            file=discord.File(image,filename=f"mtg-hand-{game_id}-{page+1}.png")
            if editing: await interaction.edit_original_response(content=text,attachments=[file],view=view)
            else: await interaction.followup.send(text,file=file,view=view,ephemeral=True)
        except (ArtError,OSError):
            log.warning("Could not render private hand",exc_info=True)
            if editing: await interaction.edit_original_response(content=text,attachments=[],view=view)
            else: await interaction.followup.send(text,view=view,ephemeral=True)
    async def act(self,i,game_id,action,label):
        game=self.games.get(game_id)
        if not game:
            await i.response.send_message("This match is unavailable.",ephemeral=True); return
        async with self.lock(game.game_id):
            try:
                action(game); game.record(i.user.id,label); advance_solo(game); await self.save(game)
            except (GameError,IndexError,ValueError) as e: await i.response.send_message(str(e),ephemeral=True); return
            await i.response.defer()
            embed,file=await self.game_message(game)
            await i.edit_original_response(embed=embed,attachments=[file] if file else [],view=None if game.finished else GameView(self,game.game_id))
    async def mutate_ctx(self,ctx,action,label):
        game=self.find(ctx.author.id)
        async with self.lock(game.game_id):
            try:
                action(game); game.record(ctx.author.id,label); advance_solo(game); await self.save(game)
            except (GameError,IndexError,ValueError) as e: await ctx.send(str(e)); return
        await self.refresh_message(game)
        await ctx.message.add_reaction("✅")
    @commands.group(name="mtg",invoke_without_command=True)
    async def mtg(self,ctx):
        """Play the MTG rules prototype."""
        await ctx.send_help()
    @mtg.command(name="challenge")
    @commands.guild_only()
    async def challenge(self,ctx,member:discord.Member):
        """Challenge another member."""
        if member.bot or member.id==ctx.author.id: await ctx.send("Challenge another human member."); return
        await ctx.send(f"{member.mention}, {ctx.author.mention} challenged you to a two-player MTG prototype game.",view=ChallengeView(self,ctx.author.id,member.id),allowed_mentions=discord.AllowedMentions(users=True))
    @mtg.command(name="solo")
    @commands.guild_only()
    async def solo(self,ctx,deck:str="red",difficulty:str="easy"):
        """Start a reward-free solo match. Deck: red/green; difficulty: easy/normal."""
        deck=deck.casefold(); difficulty=difficulty.casefold()
        if deck in DIFFICULTIES and difficulty=="easy": difficulty,deck=deck,"red"
        if deck not in ("red","green") or difficulty not in DIFFICULTIES:
            await ctx.send(f"Use `{ctx.clean_prefix}mtg solo [red|green] [easy|normal]`."); return
        if not self.bot.user:
            await ctx.send("The solo opponent is not ready yet."); return
        try: game=await self.create_solo_game(ctx.author.id,self.bot.user.id,ctx.channel.id,deck,difficulty)
        except GameError as e: await ctx.send(str(e)); return
        embed,file=await self.game_message(game)
        message=await ctx.send(embed=embed,file=file,view=GameView(self,game.game_id)) if file else await ctx.send(embed=embed,view=GameView(self,game.game_id))
        game.message_id=message.id; await self.save(game)
    @mtg.command(name="status")
    async def status(self,ctx):
        try: game=self.find(ctx.author.id)
        except GameError as e: await ctx.send(str(e)); return
        async with self.lock(game.game_id):
            if advance_solo(game): await self.save(game)
        embed,file=await self.game_message(game)
        if file: await ctx.send(embed=embed,file=file)
        else: await ctx.send(embed=embed)
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
    @mtg.command(name="graveyard")
    async def graveyard(self,ctx,member:discord.Member=None):
        """List a player graveyard and its G:POSITION spell targets."""
        game=self.find(ctx.author.id); target=member.id if member else ctx.author.id
        player=game.player(target)
        names={user:str(self.bot.get_user(user).display_name if self.bot.get_user(user) else user).replace("\n"," ")[:32] for user in game.order}
        lines=[f"{position}. {game.card(uid).name}" for position,uid in enumerate(player.graveyard,1)]
        if not lines: await ctx.send(f"{names[target]} has no cards in their graveyard.",allowed_mentions=discord.AllowedMentions.none()); return
        pages=[]; current=f"**{names[target]} graveyard** — target your own cards with G:POSITION\n"
        for line in lines:
            if len(current)+len(line)+1>1900: pages.append(current); current=""
            current+=line+"\n"
        pages.append(current)
        for page in pages: await ctx.send(page,allowed_mentions=discord.AllowedMentions.none())
    @mtg.command(name="mana")
    async def mana(self,ctx,position:int,color:str=None):
        """Tap a supported mana permanent. Multi-color sources require W/U/B/R/G; sick creatures cannot tap."""
        await self.mutate_ctx(ctx,lambda g:g.activate_mana(ctx.author.id,position,color),"mana")
    @mtg.command(name="activate")
    async def activate(self,ctx,position:int,target:str=None):
        """Activate a supported non-mana ability, supplying PLAYER_ID or USER_ID:POSITION when targeted."""
        await self.mutate_ctx(ctx,lambda g:g.activate_ability(ctx.author.id,position,target),"activate")
    @mtg.command(name="play")
    async def play(self,ctx,position:int,target:str=None,x_value:int=None):
        """Play/cast a hand position with optional target and X; modal targets include tap:/untap: for Twiddle and life:/prevent: for Healing Salve."""
        normalized=None if target and target.casefold() in {"-","none"} else target
        await self.mutate_ctx(ctx,lambda g:g.play(ctx.author.id,position,normalized,x_value),"play")
    @mtg.command(name="attack")
    async def attack(self,ctx,*positions:int):
        """Declare battlefield positions as attackers; no positions skips combat."""
        await self.mutate_ctx(ctx,lambda g:g.declare_attackers(ctx.author.id,positions),"attack")
    @mtg.command(name="trample")
    async def trample(self,ctx,position:int,damage_to_blocker:int):
        """Choose how much trample damage an attacker assigns to its blocker; defaults to lethal."""
        await self.mutate_ctx(ctx,lambda g:g.assign_trample(ctx.author.id,position,damage_to_blocker),"trample")
    @mtg.command(name="block")
    async def block(self,ctx,*assignments:str):
        """Block as ATTACKER_POSITION:BLOCKER_POSITION; no values means no blocks."""
        def run(g):
            pairs={}
            for item in assignments:
                a,b=item.split(":",1); pairs[int(a)]=int(b)
            g.declare_blockers(ctx.author.id,pairs)
        await self.mutate_ctx(ctx,run,"block")
    @mtg.command(name="pass")
    async def pass_(self,ctx): await self.mutate_ctx(ctx,lambda g:g.pass_priority(ctx.author.id),"pass")
    @mtg.command(name="trigger")
    async def trigger(self,ctx,choice:str):
        """Resolve your pending optional trigger with `pay`, `draw`, or `decline`."""
        normalized=choice.casefold()
        if normalized not in ("pay","draw","decline"): await ctx.send("Choose `pay`, `draw`, or `decline`."); return
        await self.mutate_ctx(ctx,lambda g:g.choose_trigger(ctx.author.id,normalized!="decline"),f"trigger_{normalized}")
    @mtg.command(name="concede")
    async def concede(self,ctx): await self.mutate_ctx(ctx,lambda g:g.concede(ctx.author.id),"concede")
    async def red_delete_data_for_user(self,*,requester,user_id):
        async with self.storage_lock:
            changed=False; games=await self.config.games()
            for key in list(games):
                if user_id in [int(x) for x in games[key].get("order",[])]:
                    games.pop(key); gid=int(key); self.games.pop(gid,None); self.channels.pop(gid,None); self.locks.pop(gid,None); changed=True
            if changed: await self.config.games.set(games)
