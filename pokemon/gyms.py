from dataclasses import dataclass

import discord

from .data import SPECIES,experience_to_next
from .models import OwnedPokemon,pokemon_max_hp


@dataclass(frozen=True)
class Gym:
    key: str
    badge: str
    symbol: str
    leader: str
    city: str
    team: tuple

    @property
    def species_id(self):return self.team[-1][0]

    @property
    def level(self):return self.team[-1][1]


KANTO_GYMS=(
    Gym("boulder","Boulder Badge","🪨","Brock","Pewter City",((74,12),(95,14))),
    Gym("cascade","Cascade Badge","💧","Misty","Cerulean City",((121,21),)),
    Gym("thunder","Thunder Badge","⚡","Lt. Surge","Vermilion City",((26,24),)),
    Gym("rainbow","Rainbow Badge","🌈","Erika","Celadon City",((45,29),)),
    Gym("soul","Soul Badge","☠️","Koga","Fuchsia City",((110,43),)),
    Gym("marsh","Marsh Badge","🔮","Sabrina","Saffron City",((65,43),)),
    Gym("volcano","Volcano Badge","🔥","Blaine","Cinnabar Island",((59,47),)),
    Gym("earth","Earth Badge","🌍","Giovanni","Viridian City",((112,50),)),
)
GYMS={gym.key:gym for gym in KANTO_GYMS}


def earned_badges(raw):
    known=set(GYMS)
    return [key for key in dict.fromkeys(raw or []) if key in known]


def next_gym(raw):
    badges=set(earned_badges(raw))
    return next((gym for gym in KANTO_GYMS if gym.key not in badges),None)


def gym_by_key(value):
    key=(value or "").casefold().replace(" badge","").replace(" ","")
    for gym in KANTO_GYMS:
        if key in {gym.key,gym.leader.casefold().replace(" ","").replace(".","")}:
            return gym
    return None


def badge_case(raw):
    badges=set(earned_badges(raw))
    return " ".join(gym.symbol if gym.key in badges else "◻️" for gym in KANTO_GYMS)


def gym_status_embed(user,conf,challenge_command="[p]poke gym challenge"):
    badges=earned_badges(conf.get("badges",[]));upcoming=next_gym(badges)
    embed=discord.Embed(title="Kanto Gym Challenge",color=discord.Color.gold())
    names=", ".join(GYMS[key].badge for key in badges) or "No badges earned yet."
    embed.description=badge_case(badges)+"\n"+names
    embed.add_field(name="Badges",value=f"{len(badges)}/{len(KANTO_GYMS)}",inline=True)
    if upcoming:
        team=" · ".join(f"{SPECIES[species_id].name} Lv. {level}" for species_id,level in upcoming.team)
        embed.add_field(name="Next challenge",value=f"{upcoming.leader} · {upcoming.city}\n{team}",inline=True)
        embed.set_footer(text=f"Use {challenge_command} when your party is ready, or press Challenge {upcoming.leader} below.")
    else:
        embed.add_field(name="Journey",value="All eight Kanto badges earned.",inline=True)
        embed.set_footer(text="You completed the Kanto Gym challenge.")
    return embed


def trainer_profile_embed(user,conf,max_collection):
    badges=earned_badges(conf.get("badges",[]))
    owned={item["instance_id"]:item for item in conf.get("collection",[])}
    lead=owned.get(conf.get("party",[None])[0]) if conf.get("party") else None
    name=getattr(user,"display_name",getattr(user,"name","Trainer"))
    embed=discord.Embed(title=f"{name}'s Trainer Profile",color=discord.Color.red())
    avatar=getattr(getattr(user,"display_avatar",None),"url",None)
    if avatar:embed.set_thumbnail(url=avatar)
    badge_names=", ".join(GYMS[key].badge for key in badges) or "No badges earned yet."
    embed.add_field(name=f"Badge Case · {len(badges)}/8",value=badge_case(badges)+"\n"+badge_names,inline=False)
    embed.add_field(name="Pokédex",value=f"{len(conf.get('pokedex_seen',[]))} seen\n{len(conf.get('pokedex_caught',[]))} caught",inline=True)
    embed.add_field(name="Collection",value=f"{len(conf.get('collection',[]))}/{max_collection} Pokémon\n{len(conf.get('party',[]))}/6 in party",inline=True)
    items=conf.get("items",{})
    embed.add_field(name="Bag",value=f"{conf.get('balls',0)} Poké · {items.get('great_ball',0)} Great · {items.get('ultra_ball',0)} Ultra",inline=True)
    if lead:
        needed=experience_to_next(lead["species_id"],lead["level"])
        progress="MAX" if not needed else f"{lead.get('experience',0)}/{needed} XP"
        partner=OwnedPokemon.from_raw(lead);maximum=pokemon_max_hp(partner)
        current=maximum if partner.current_hp is None else partner.current_hp
        embed.add_field(name="Partner",value=f"{SPECIES[lead['species_id']].name} · Lv. {lead['level']}\nHP {current}/{maximum} · {progress}",inline=False)
    else:
        embed.add_field(name="Partner",value="Choose a starter to begin your journey.",inline=False)
    upcoming=next_gym(badges)
    embed.set_footer(text=(f"Next: {upcoming.leader} in {upcoming.city}" if upcoming else "Kanto Gym challenge complete"))
    return embed
