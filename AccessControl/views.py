import discord


class AccessControlPanel(discord.ui.View):
    def __init__(self, cog, author):
        super().__init__(timeout=600)
        self.cog = cog
        self.author = author

    async def interaction_check(self, interaction):
        if (
            interaction.user.id != self.author.id
            or not await self.cog.bot.is_owner(interaction.user)
        ):
            await interaction.response.send_message(
                "Open your own bot-owner AccessControl panel.", ephemeral=True
            )
            return False
        return True

    async def refresh(self, interaction, notice=""):
        await interaction.response.edit_message(
            embed=await self.cog.status_embed(interaction.guild, notice),
            view=AccessControlPanel(self.cog, self.author),
        )

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary)
    async def refresh_button(self, interaction, button):
        await self.refresh(interaction)

    @discord.ui.button(label="Allow/remove this guild", style=discord.ButtonStyle.secondary)
    async def allow_guild(self, interaction, button):
        if interaction.guild is None:
            await interaction.response.send_message(
                "Use this control in a server.", ephemeral=True
            )
            return
        guilds = set(await self.cog.config.allowed_guild_ids())
        if interaction.guild.id in guilds:
            guilds.remove(interaction.guild.id)
            notice = "This guild was removed from the allowlist."
            action = "remove guild"
        else:
            guilds.add(interaction.guild.id)
            notice = "This guild was added to the allowlist."
            action = "allow guild"
        await self.cog.config.allowed_guild_ids.set(sorted(guilds))
        await self.cog.audit(interaction.user.id, action, str(interaction.guild.id))
        await self.refresh(interaction, notice)
