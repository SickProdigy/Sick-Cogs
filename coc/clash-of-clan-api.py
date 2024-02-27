from redbot.core import Config  # redbot additions, import config
from redbot.core import commands # redbot additions

from urllib import request
import requests
from bs4 import BeautifulSoup
import json

class Coc(commands.Cog):   # for redbot name your cog 'Coc' here 
    def __init__(self):
        self.config = Config.get_conf(self, identifier=1234567890)  # grab an identifier in the class's __init__ function, in case someone has same cog name

        self.config.register_global(
            foo=True
        )

    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()

        default_global = {"WOLFRAM_API_KEY": None}

        self.config = Config.get_conf(self, 9628832554)
        self.config.register_guild(**default_global)


    @commands.command(coc)
    async def return_some_data(self, ctx):
        await ctx.send(await self.config.foo())


# url='https://api.clashofclans.com/v1/clans/%232GYRJV2YV/currentwar' # Hangorthia; current war, not working
# url='https://api.clashofclans.com/v1/clans/%232GYRJV2YV' # Hangorthia
url='https://api.clashofclans.com/v1/clans/%232GYRJV2YV/members' # Hangorthia, pull all members url

auth = {'authorization': 'Bearer AUTHKEYHERE'}
userAgent = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_5) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/50.0.2661.102 Safari/537.36'}
headers = auth

result = requests.get(url, headers=headers)  # adding headers

# html = request.urlopen(url).read()    # returns an object with an info() method which returns the headers

soup = BeautifulSoup(result.content, 'html.parser')   # result.content will show content of requests.get
site_json=json.loads(soup.text)
print(site_json)
