from redbot.core import commands
import aiohttp

from redbot.core import Config, commands, checks
from redbot.core.utils.chat_formatting import box, pagify
from redbot.core.utils.menus import menu, DEFAULT_CONTROLS

class Coc(commands.Cog):
    """Clash of Clans API Link"""

    async def red_delete_data_for_user(self, **kwargs):
        """ Nothing to delete """
        return

    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()
        default_global = {"COC_API_KEY": None}

        self.config = Config.get_conf(self, 5218831554)
        self.config.register_guild(**default_global)




    @commands.command()
    async def coc(self, ctx):
        """Update on if in war or not"""

        api_key = await self.config.COC_API_KEY()
        if not api_key:
            return await ctx.send("No API key set for Clash of Clans. Get one at https://developer.clashofclans.com/")

        headers = {
            'Accept': 'application/json',
            'authorization': 'Bearer ' + api_key
        }

        clanNameSearch = ''
        clanNameKeyInput = '2QLUUJYVL'
        clanNameConcat = '%23' + clanNameKeyInput

        #return Current war endpoint
        try:
            async with aiohttp.request( 'GET','https://api.clashofclans.com/v1/clans/' + clanNameConcat + '/currentwar', headers=headers) as response:
                if response.status != 200:
                    return await ctx.send("Oops! Couldn't return results from COC api...")
                user_json = await response.json()
        except aiohttp.ClientConnectionError:
            return await ctx.send("Oops! Couldn't return results from COC api...")
        
        await ctx.send(f"'{user_json}'") # return results in json format


    @checks.is_owner()
    @commands.command(name="setcocapi", aliases=["setcoc"])
    async def _setwolframapi(self, ctx, key: str):
        """Set the api-key for Clash of Clans. Go to clash developer portal for access. Ex: 'Bearer abcdefghijklmnop123456789'"""

        if key:
            await self.config.COC_API_KEY.set(key)
            await ctx.send("Key set.")