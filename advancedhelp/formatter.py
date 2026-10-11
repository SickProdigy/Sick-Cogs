from dataclasses import replace

from discord.ext import commands as dpy_commands
from redbot.core.commands.help import HelpSettings, RedHelpFormatter

from .models import build_catalog


class AdvancedHelpFormatter(RedHelpFormatter):
    def __init__(self, cog):
        self.cog = cog

    async def send_help(self, ctx, help_for=None, *, from_help_command=False):
        if help_for is not None and not isinstance(help_for, dpy_commands.bot.BotBase):
            return await super().send_help(
                ctx, help_for, from_help_command=from_help_command
            )
        settings = await HelpSettings.from_context(ctx)
        settings = replace(settings, verify_checks=True, show_hidden=False)
        mapping = await self.get_bot_help_mapping(ctx, help_settings=settings)
        global_data = await self.cog.config.all()
        catalog = build_catalog(
            mapping,
            global_data["audience_overrides"],
            global_data["category_overrides"],
            global_data["audience_order"],
            global_data["audience_appearance"],
            global_data["category_appearance"],
        )
        if ctx.guild:
            role_ids = {role.id for role in getattr(ctx.author, "roles", [])}
            groups = await self.cog.config.guild(ctx.guild).role_groups()
            for key, group in sorted(
                groups.items(),
                key=lambda item: (int(item[1].get("order", 1000)), item[0]),
            ):
                if role_ids.intersection(group.get("role_ids", [])):
                    catalog.add_role_group(
                        key,
                        group.get("display_name", key),
                        group.get("categories", []),
                        group.get("emoji", "🔖"),
                        group.get("description"),
                    )
        await self.cog.send_help_home(ctx, catalog)
