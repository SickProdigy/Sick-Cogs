import aiohttp
import discord
from redbot.core import Config, commands, checks
from redbot.core.utils.chat_formatting import box, pagify
from redbot.core.utils.menus import menu, DEFAULT_CONTROLS

class Coc(commands.Cog):
    """Clash of Clans War Updates"""

    async def red_delete_data_for_user(self, **kwargs):
        """ Nothing to delete """
        return

    def __init__(self, bot):
        self.bot = bot
        default_global = {"COC_API_KEY": None}

        self.config = Config.get_conf(self, 5218831554)
        self.config.register_guild(**default_global)

    @commands.command()
    async def coc(self, ctx):
        """Clash of Clan update on if in war or not"""

        api_key = await self.config.COC_API_KEY()
        if not api_key:
            return await ctx.send("No API key set for Clash of Clans. Get one at https://developer.clashofclans.com/")

        headers = {
            'Accept': 'application/json',
            'authorization': 'Bearer ' + api_key
        }

        clanNameKeyInput = '2QLUUJYVL'
        clanNameConcat = '%23' + clanNameKeyInput
        truncated_text = ''
        #return Current war endpoint
        try:
            async with aiohttp.request('GET', 'https://api.clashofclans.com/v1/clans/' + clanNameConcat + '/currentwar', headers=headers) as response:
                if response.status != 200:
                    return await ctx.send("Oops! Couldn't return results from COC api...")
                user_json = await response.json()
                truncated_text = str(user_json)[:1000]
        except aiohttp.ClientConnectionError as e:
            await ctx.send(f"Oops! Couldn't return results from COC api due to a connection error: {e}")
        except Exception as e:
            await ctx.send(f"An unexpected error occurred: {e}")
        clan_name = str(user_json['clan']['name'])
        clan_tag = user_json['clan']['tag']
        state_war = user_json['state']
        team_size = user_json['teamSize']
        embed = discord.Embed(
            description='Clan tag: ' + clan_tag,
            color=0x5CDBF0,
            timestamp=None
        )
        image1 = user_json['clan']['badgeUrls']['large']
        image2 = 'https://i.imgur.com/TFTXZvP.png'
        embed.set_author(name=clan_name, icon_url=image1)
        embed.add_field(name ='War State:', value=state_war)
        embed.add_field(name='Team Size:', value=team_size)
        embed.set_footer(text='Brought to you by SickGaming.net', icon_url=image2)
        embed.set_thumbnail(url=image1)
        
        await ctx.send(embed=embed)
        # await ctx.send (f"image1")
        # await ctx.send(f"'{clan_name}\n{clan_tag}\nState: {state_war}\nTeam Size: {team_size}'") # return results in json format
        # await ctx.send(f"'{clan_name}\n{clan_tag}\nState: {state_war}\nTeam Size: {team_size}'") 


    @checks.is_owner()
    @commands.command(name="setcocapi", aliases=["setcoc"])
    async def _setcocapi(self, ctx, key: str):
        """Set the api-key for Clash of Clans. Go to clash developer portal for access. Ex: 'Bearer abcdefghijklmnop123456789'"""

        if key:
            await self.config.COC_API_KEY.set(key)
            await ctx.send("Key set.")