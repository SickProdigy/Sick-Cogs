import math
from dataclasses import dataclass
import discord
from .data import SPECIES,generation_for,sprite
PAGE_SIZE=15
STYLE_DEFAULT="retro"
FENCE=chr(96)*3

def generation_entries(generation):
    return sorted((x for x in SPECIES.values() if generation_for(x.id)==generation),key=lambda x:x.id)

@dataclass
class PokedexSession:
    user_id:int
    seen:set
    caught:set
    style:str=STYLE_DEFAULT
    generation:int=1
    filter_name:str="all"
    page:int=0
    selected_id:int|None=None
    def entries(self):
        values=generation_entries(self.generation)
        if self.filter_name=="seen":values=[x for x in values if x.id in self.seen]
        if self.filter_name=="caught":values=[x for x in values if x.id in self.caught]
        return values
    @property
    def pages(self):return max(1,math.ceil(len(self.entries())/PAGE_SIZE))
    def normalize(self):
        self.page=min(max(0,self.page),self.pages-1)
        if self.selected_id not in {x.id for x in self.entries()}:self.selected_id=None
    def search(self,query):
        query=query.strip();values=generation_entries(self.generation)
        if query.isdigit():match=next((x for x in values if x.id==int(query)),None)
        else:
            key=query.casefold()
            match=next((x for x in values if x.id in self.seen and x.name.casefold()==key),None)
        if not match:return False
        self.filter_name="all";self.selected_id=match.id;self.page=values.index(match)//PAGE_SIZE
        return True

class PokedexStyle:
    key="";label=""
    @staticmethod
    def status(session,species_id):
        return "caught" if species_id in session.caught else "seen" if species_id in session.seen else "unseen"
    @classmethod
    def details(cls,session,item):
        state=cls.status(session,item.id)
        if state=="unseen":return ["Entry locked.","Encounter this Pokemon to reveal its identity."]
        lines=["Type: "+" / ".join(x.title() for x in item.types)]
        if state=="seen":return lines+["Catch this Pokemon to unlock its complete research data."]
        return lines+[f"HP {item.hp} | Attack {item.attack} | Defense {item.defense}",f"Sp. Atk {item.special_attack} | Sp. Def {item.special_defense} | Speed {item.speed}","Abilities: "+(", ".join(x.replace("-"," ").title() for x in item.abilities) or "Unknown"),f"Catch rate: {item.catch_rate}"]
    @staticmethod
    def footer(session):
        values=generation_entries(session.generation);ids={x.id for x in values}
        return f"{len(session.seen&ids)} seen | {len(session.caught&ids)} caught | {session.filter_name.title()} | Page {session.page+1}/{session.pages}"

class RetroStyle(PokedexStyle):
    key="retro";label="Retro"
    def list_embed(self,session):
        values=session.entries()[session.page*PAGE_SIZE:(session.page+1)*PAGE_SIZE];rows=[]
        for item in values:
            state=self.status(session,item.id);mark={"caught":"O","seen":"o","unseen":"-"}[state]
            name=item.name.upper() if state!="unseen" else "???";rows.append(f"{mark} #{item.id:03d}  {name[:15]}")
        rows=rows or ["No matching entries."]
        box=["+----------------------+",*[f"| {row:<20} |" for row in rows],"+----------------------+"]
        embed=discord.Embed(title=f"POKEDEX | GENERATION {session.generation}",description="\n".join([FENCE,*box,FENCE]),color=discord.Color.red())
        embed.set_footer(text=self.footer(session));return embed
    def detail_embed(self,session,item):
        state=self.status(session,item.id);name=item.name.upper() if state!="unseen" else "???";mark={"caught":"O CAUGHT","seen":"o SEEN","unseen":"- UNKNOWN"}[state]
        box=["+----------------------+",f"| #{item.id:03d} {name[:14]:<14} |",f"| {mark:<20} |","+----------------------+"]
        embed=discord.Embed(title="POKEDEX DATA",description="\n".join([FENCE,*box,FENCE,*self.details(session,item)]),color=discord.Color.red())
        if state!="unseen":embed.set_thumbnail(url=sprite(item.id))
        embed.set_footer(text=self.footer(session));return embed

class CompactStyle(PokedexStyle):
    key="compact";label="Compact"
    def list_embed(self,session):
        values=session.entries()[session.page*PAGE_SIZE:(session.page+1)*PAGE_SIZE];lines=[]
        for item in values:
            state=self.status(session,item.id);mark={"caught":"[C]","seen":"[S]","unseen":"[ ]"}[state]
            lines.append(f"{mark} **#{item.id:03d}** {item.name if state!='unseen' else '???'}")
        embed=discord.Embed(title=f"Generation {session.generation} Pokedex",description="\n".join(lines) or "No entries match this filter.",color=discord.Color.blurple())
        embed.set_footer(text=self.footer(session));return embed
    def detail_embed(self,session,item):
        state=self.status(session,item.id);name=item.name if state!="unseen" else "Unknown Pokemon"
        embed=discord.Embed(title=f"#{item.id:03d} | {name}",description="\n".join(self.details(session,item)),color=discord.Color.blurple())
        if state!="unseen":embed.set_thumbnail(url=sprite(item.id))
        embed.set_footer(text=self.footer(session));return embed

POKEDEX_STYLES={x.key:x for x in (RetroStyle(),CompactStyle())}
def resolve_style(key):return POKEDEX_STYLES.get(key,POKEDEX_STYLES[STYLE_DEFAULT])
def render_pokedex(session):
    session.normalize();style=resolve_style(session.style)
    return style.detail_embed(session,SPECIES[session.selected_id]) if session.selected_id else style.list_embed(session)

class EntrySelect(discord.ui.Select):
    def __init__(self,view):
        self.pokedex_view=view
        values=view.session.entries()[view.session.page*PAGE_SIZE:(view.session.page+1)*PAGE_SIZE]
        options=[]
        for item in values:
            state=PokedexStyle.status(view.session,item.id)
            name=item.name if state!="unseen" else "???"
            options.append(discord.SelectOption(label=f"#{item.id:03d} {name}",value=str(item.id),default=item.id==view.session.selected_id))
        super().__init__(placeholder="Open an entry",options=options or [discord.SelectOption(label="No matching entries",value="none")],disabled=not options,row=4)
    async def callback(self,interaction):
        if self.values[0]=="none":return
        view=self.pokedex_view;view.session.selected_id=int(self.values[0]);view.rebuild();await view.refresh(interaction)

class GenerationSelect(discord.ui.Select):
    def __init__(self,view):
        self.pokedex_view=view;generations=sorted({generation_for(x.id) for x in SPECIES.values()})
        super().__init__(placeholder="Generation",options=[discord.SelectOption(label=f"Generation {x}",value=str(x),default=x==view.session.generation) for x in generations],row=3)
    async def callback(self,interaction):
        view=self.pokedex_view;view.session.generation=int(self.values[0]);view.session.page=0;view.session.selected_id=None
        view.rebuild();await view.refresh(interaction)

class SearchModal(discord.ui.Modal,title="Search Pokedex"):
    query=discord.ui.TextInput(label="Pokedex number or discovered name",placeholder="25 or Pikachu",max_length=32)
    def __init__(self,view):super().__init__();self.pokedex_view=view
    async def on_submit(self,interaction):
        view=self.pokedex_view
        if not view.session.search(str(self.query)):
            await interaction.response.send_message("No matching discovered Pokemon was found in this generation.",ephemeral=True);return
        view.rebuild();await view.refresh(interaction)

class PokedexView(discord.ui.View):
    def __init__(self,cog,session):
        super().__init__(timeout=300);self.cog=cog;self.session=session;self.message=None;self.rebuild()
    async def interaction_check(self,interaction):
        if interaction.user.id==self.session.user_id:return True
        await interaction.response.send_message("This Pokedex belongs to another trainer.",ephemeral=True);return False
    async def on_timeout(self):
        for item in self.children:item.disabled=True
        if self.message:
            try:await self.message.edit(view=self)
            except discord.HTTPException:pass
    def rebuild(self):
        self.clear_items();values=self.session.entries()
        self.previous.disabled=self.session.page<=0;self.next.disabled=self.session.page>=self.session.pages-1
        self.details.disabled=not values;self.details.label="List" if self.session.selected_id else "Details"
        self.filter.label=f"Filter: {self.session.filter_name.title()}"
        for item in (self.previous,self.details,self.next,self.search,self.filter):self.add_item(item)
        self.add_item(GenerationSelect(self));self.add_item(EntrySelect(self))
    async def refresh(self,interaction):await interaction.response.edit_message(embed=render_pokedex(self.session),view=self)
    @discord.ui.button(label="Previous",style=discord.ButtonStyle.secondary,row=0)
    async def previous(self,interaction,button):
        self.session.page-=1;self.session.selected_id=None;self.rebuild();await self.refresh(interaction)
    @discord.ui.button(label="Details",style=discord.ButtonStyle.primary,row=0)
    async def details(self,interaction,button):
        if self.session.selected_id:self.session.selected_id=None
        else:
            values=self.session.entries();index=min(self.session.page*PAGE_SIZE,len(values)-1)
            if index>=0:self.session.selected_id=values[index].id
        self.rebuild();await self.refresh(interaction)
    @discord.ui.button(label="Next",style=discord.ButtonStyle.secondary,row=0)
    async def next(self,interaction,button):
        self.session.page+=1;self.session.selected_id=None;self.rebuild();await self.refresh(interaction)
    @discord.ui.button(label="Search",style=discord.ButtonStyle.secondary,row=1)
    async def search(self,interaction,button):await interaction.response.send_modal(SearchModal(self))
    @discord.ui.button(label="Filter: All",style=discord.ButtonStyle.secondary,row=1)
    async def filter(self,interaction,button):
        names=("all","seen","caught");self.session.filter_name=names[(names.index(self.session.filter_name)+1)%3]
        self.session.page=0;self.session.selected_id=None;self.rebuild();await self.refresh(interaction)
