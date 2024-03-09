from redbot.core import commands
import aiohttp
import requests
from dotenv import load_dotenv
import os


class Coc(commands.Cog):
    """Clash of Clans API Link"""

    async def red_delete_data_for_user(self, **kwargs):
        """ Nothing to delete """
        return

    def __init__(self, bot):
        self.bot = bot

    @commands.command()
    async def coc(self, ctx):
        """Update on if in war or not"""


        
        # Start of custom script
        load_dotenv()
        
        auth_key_coc = os.environ.get("AUTH_KEY_COC")
        
        headers = {
            'Accept': 'application/json',
            'authorization': auth_key_coc
        }
        
        def clan_current_war():
            #return user profile inofrmation
            response = requests.get('https://api.clashofclans.com/v1/clans/' + clanNameConcat + '/currentwar', headers=headers)
            user_json = response.json()
            print(user_json)
            
        def search_clan():
            # sbumit a clan search
            response = requests.get('https://api.clashofclans.com/v1/clans?name=' + clanNameSearch, headers=headers)
            clan_json = response.json()
            for clan in clan_json['items']:
                print(clan['name'] + ': is level ' + str(clan['clanLevel'])) # if you dont ' ' name will show undefined?
        
        clanNameSearch = ''
        clanNameKeyInput = '2QLUUJYVL'
        clanNameConcat = '%23' + clanNameKeyInput

        try:
            async with clan_current_war() as r:
                if r.status != 200:
                    return await ctx.send("Oops! There was an error with COC...")
                result = await r.text(encoding="UTF-8")
        except aiohttp.ClientConnectionError:
            return await ctx.send("Oops! There was an error with COC...")
        
        # clan_current_war()
        # search_clan()
        
        # end of custom script
        
        await ctx.send(f"`{result}`")

        # ig should send results to discord that you called the command in.