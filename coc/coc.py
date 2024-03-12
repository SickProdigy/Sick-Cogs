import aiohttp
import discord
import json
from datetime import datetime, timedelta
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
        default_global = {"COC_API_KEY": None, }
        default_guild = {"COC_CLAN_KEY": None}
        self.config = Config.get_conf(self, 5218831554)
        self.config.register_global(**default_global)
        self.config.register_guild(**default_guild)
    
        
    
    @commands.command()
    async def coc(self, ctx):
        """Clash of Clan information and war results"""
        """use -cocsetapi and -setcocclankey"""

        api_key = await self.config.COC_API_KEY()
        if not api_key:
            return await ctx.send("No API key set for Clash of Clans. Get one at https://developer.clashofclans.com/")
        clan_key = await self.config.COC_CLAN_KEY()
        if not clan_key:
            return await ctx.send("No Clan key set for Clash of Clans. Check clan profile, share, copy, paste behind -setcocclankey")
        clanNameKeyInput = clan_key
        if '#' in clanNameKeyInput:
            clanNameKeyInput = clanNameKeyInput.replace('#', "")
        clanNameConcat = '%23' + clanNameKeyInput
        headers = {
            'Accept': 'application/json',
            'authorization': 'Bearer ' + api_key
        }
        #return Current Clans endpoint
        try:
            async with aiohttp.request('GET', 'https://api.clashofclans.com/v1/clans/' + clanNameConcat, headers=headers) as response:
                if response.status != 200:
                    return await ctx.send("Oops! Couldn't return results from COC api...")
                user_json = await response.json()
        except aiohttp.ClientConnectionError as e:
            await ctx.send(f"Oops! Couldn't return results from COC api due to a connection error: {e}")
        except Exception as e:
            await ctx.send(f"An unexpected error occurred: {e}")
        
        clan_name = str(user_json['name'])
        clan_tag = user_json['tag']
        clan_description = user_json['description']
        members_count = user_json['members']
        war_frequency = user_json['warFrequency']
        
        embed = discord.Embed(
            description=clan_description,
            color=0x2ecc71,
            timestamp=None
        )
        image1 = user_json['badgeUrls']['large'] # clan logo thing
        image2 = 'https://i.imgur.com/TFTXZvP.png' # sg logo
        image3 = 'https://i.imgur.com/WAZjzZr.jpeg' # coc logo
        embed.set_author(name=clan_name, icon_url=image1)
        
        embed.add_field(name='Join Tag:', value=clan_tag)
        embed.add_field(name='Member Count:', value=members_count)
        embed.add_field(name='War Frequency:', value=war_frequency)
        
        embed.set_image(url=image3)
        embed.set_thumbnail(url=image1)
        embed.set_footer(text='Brought to you by SickGaming.net', icon_url=image2)
        
        await ctx.send(embed=embed)
        # await ctx.send(user_json['memberList']) # too much characters
        
        memberList = user_json['memberList']
        counter = 0
        # json_dict = json.loads(user_json)   # says it's already dict, must be str, bytes or bytearray to run this command
        for member_list in memberList:
            member_name = member_list.get('name', 'No name provided')
            member_tag = member_list.get('tag', 'No tag provided')
            th_level = member_list.get('townHallLevel', 'No th level?!')
            league_name = member_list.get('league', {}).get('name', 'No league provided')
            counter += 1
            await ctx.send(f"**User {counter}**\n🫅 Name: {member_name}, 👤 Tag: {member_tag}\n🏠 TH {th_level}, 🛡️ {league_name}")
        
        
    @commands.command()
    async def war(self, ctx):
        """Clash of Clan update on if in war or not"""

        api_key = await self.config.COC_API_KEY()
        if not api_key:
            return await ctx.send("No API key set for Clash of Clans. Get one at https://developer.clashofclans.com/ and use -setcocapi")
        
        clan_key = await self.config.COC_CLAN_KEY()
        if not clan_key:
            return await ctx.send("No Clan key set for Clash of Clans. Check clan profile, share, copy, paste. use -setcocclankey")
        clanNameKeyInput = clan_key
        if '#' in clanNameKeyInput:
            clanNameKeyInput = clanNameKeyInput.replace('#', "")
        clanNameConcat = '%23' + clanNameKeyInput
        headers = {
            'Accept': 'application/json',
            'authorization': 'Bearer ' + api_key
        }
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
        team_attacks = user_json['clan']['attacks']
        team_stars = user_json['clan']['stars']
        team_destruction = user_json['clan']['destructionPercentage']
        opponent_attacks = user_json['opponent']['attacks']
        opponent_stars = user_json['opponent']['stars']
        opponent_destruction = user_json['opponent']['destructionPercentage']
        war_prep_time = user_json['preparationStartTime']
        war_start_time = user_json['startTime']
        war_end_time = user_json['endTime']
        
        # Convert time to readable format
        war_prep_time_converted = datetime.strptime(war_prep_time, "%Y%m%dT%H%M%S.%fZ")
        war_start_time_converted = datetime.strptime(war_start_time, "%Y%m%dT%H%M%S.%fZ")
        war_end_time_converted = datetime.strptime(war_end_time, "%Y%m%dT%H%M%S.%fZ")
        wptc_est = war_prep_time_converted - timedelta(hours=5)
        wstc_est = war_start_time_converted - timedelta(hours=5)
        wetc_est = war_end_time_converted - timedelta(hours=5)
        team_attacks_full_amount = user_json['teamSize']*user_json['attacksPerMember']
        if state_war == "preparation":
            embed = discord.Embed(
                description='Clash of Clan War Status',
                color=0x2ecc71,
                timestamp=None
            )
            image1 = user_json['clan']['badgeUrls']['large'] # clan logo thing
            image2 = 'https://i.imgur.com/TFTXZvP.png' # sg logo
            image3 = 'https://i.imgur.com/WAZjzZr.jpeg' # coc logo
            embed.set_author(name=clan_name, icon_url=image1)
            
            embed.add_field(name ='War State:', value=state_war)
            embed.add_field(name='Team Size:', value=team_size)
            embed.add_field(name='Attacks available:', value=team_attacks_full_amount)
            
            embed.add_field(name ='War Prep time:', value=wptc_est.strftime("%I:%M %p %b-%d-%Y"))
            embed.add_field(name ='War Start time:', value=wstc_est.strftime("%I:%M %p %b-%d-%Y"))
            embed.add_field(name ='War End time:', value=wetc_est.strftime("%I:%M %p %b-%d-%Y"))
            

            embed.set_image(url=image3)
            embed.set_thumbnail(url=image1)
            embed.set_footer(text='Brought to you by SickGaming.net', icon_url=image2)

        elif state_war == "inWar":
            embed = discord.Embed(
                description='Clash of Clan War Status. Time is in EST',
                color=0x992d22,
                timestamp=None
            )
            image1 = user_json['clan']['badgeUrls']['large']
            image2 = 'https://i.imgur.com/TFTXZvP.png'
            image3 = 'https://i.imgur.com/WAZjzZr.jpeg' # coc logo
            embed.set_author(name=clan_name, icon_url=image1)
            
            embed.add_field(name ='War State:', value=state_war)
            embed.add_field(name='Team Size:', value=team_size)
            embed.add_field(name='Total Attacks:', value=team_attacks_full_amount)
            
            embed.add_field(name ='War Prep time:', value=wptc_est.strftime("%I:%M %p %b-%d-%Y"))
            embed.add_field(name ='War Start time:', value=wstc_est.strftime("%I:%M %p %b-%d-%Y"))
            embed.add_field(name ='War End time:', value=wetc_est.strftime("%I:%M %p %b-%d-%Y"))
            
            # splits them up in 3's automagically, how can I change this?
            embed.add_field(name ='Attacks Used:', value=team_attacks)
            embed.add_field(name ='Stars Gained:', value=team_stars)
            embed.add_field(name ='Team Destruction:', value=team_destruction)
            
            embed.add_field(name='Opponent Attacks:', value=opponent_attacks)
            embed.add_field(name='Opponent Stars:', value=opponent_stars)
            embed.add_field(name='Opponent Destruction:', value=opponent_destruction)
            

            embed.set_image(url=image3)
            embed.set_thumbnail(url=image1)
            embed.set_footer(text='Brought to you by SickGaming.net', icon_url=image2)
        await ctx.send(embed=embed)
        # await ctx.send (f"image1")
        # await ctx.send(f"'{clan_name}\n{clan_tag}\nState: {state_war}\nTeam Size: {team_size}'") # return results in json format
        # await ctx.send(f"'{clan_name}\n{clan_tag}\nState: {state_war}\nTeam Size: {team_size}'") 



    @checks.is_owner()
    @commands.command(name="setcocapi", aliases=["setcoc"])
    async def _setcocapi(self, ctx, key: str):
        """Set the api-key for Clash of Clans. Go to clash developer portal for key. Ex: 'abcdefghijklmnop123456789'"""

        if key:
            await self.config.COC_API_KEY.set(key)
            await ctx.send("Key set.")

    @checks.guildowner()
    @commands.command(name="setcocclankey", aliases=["setcocclan"])
    async def _setcocclankey(self, ctx, key: str):
        """Set the Clan Tag for Clash of Clans auto updates."""

        if key:
            await self.config.COC_CLAN_KEY.set(key)
            await ctx.send("Key set.")

    @checks.guildowner()
    @commands.command(name="setcocwarchannel", aliases=["setwarchannel"])
    async def _setcocwarchannel(self, ctx, key: str):
        """Set the channel for Clash of Clans war updates."""

        if key:
            await self.config.COC_WAR_CHANNEL.set(key)
            await ctx.send("Key set.")