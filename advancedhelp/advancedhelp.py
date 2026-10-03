import discord
from redbot.core import Config, checks, commands
from redbot.core.utils.chat_formatting import pagify

from .formatter import AdvancedHelpFormatter
from .models import DEFAULT_AUDIENCE_LABELS
from .views import AdvancedHelpView

CONFIG_IDENTIFIER = 438917205171
DEFAULT_GLOBAL = {
    "schema_version": 1,
    "audience_overrides": {},
    "category_overrides": {},
    "audience_order": ["member", "staff", "server_owner", "bot_owner"],
    "audience_appearance": {},
    "category_appearance": {},
}
DEFAULT_GUILD = {
    "destination": "channel",
    "timeout_seconds": 180,
    "delete_on_timeout": False,
    "role_groups": {},
}


class AdvancedHelp(commands.Cog):
    """Audience-first interactive help using Red's permission filtering."""

    __author__ = ["SickProdigy"]
    __version__ = "0.1.0"

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(
            self, identifier=CONFIG_IDENTIFIER, force_registration=True
        )
        self.config.register_global(**DEFAULT_GLOBAL)
        self.config.register_guild(**DEFAULT_GUILD)
        self.formatter = AdvancedHelpFormatter(self)

    async def cog_load(self):
        self.bot.set_help_formatter(self.formatter)

    def cog_unload(self):
        if getattr(self.bot, "_help_formatter", None) is self.formatter:
            self.bot.reset_help_formatter()

    async def red_delete_data_for_user(self, *, requester, user_id: int):
        return

    async def send_help_home(self, ctx, catalog):
        if not catalog.audiences:
            await ctx.send("No commands are available in this context.")
            return
        settings = await self.config.guild(ctx.guild).all() if ctx.guild else DEFAULT_GUILD
        destination = settings["destination"]
        timeout = max(30, min(int(settings["timeout_seconds"]), 900))
        use_embeds = await ctx.embed_requested()
        if not use_embeds:
            await self.send_text_help(ctx, catalog, destination)
            return
        view = AdvancedHelpView(
            self, ctx, catalog, timeout, bool(settings["delete_on_timeout"])
        )
        embed = await view.embed()
        try:
            if destination == "dm":
                message = await ctx.author.send(embed=embed, view=view)
                if ctx.guild:
                    await ctx.tick()
            else:
                message = await ctx.send(embed=embed, view=view)
        except (discord.Forbidden, discord.HTTPException):
            if destination == "dm":
                await ctx.send(
                    "I could not send help by DM. Check your privacy settings."
                )
            else:
                await self.send_text_help(ctx, catalog, "channel")
            return
        view.message = message

    async def send_text_help(self, ctx, catalog, destination):
        lines = ["Available help areas:"]
        for audience in catalog.audiences:
            label, _emoji, _description = catalog.metadata(audience)
            lines.append(f"{label}: {', '.join(catalog.categories(audience))}")
        pages = list(pagify("\n".join(lines), page_length=1900))
        try:
            target = ctx.author.send if destination == "dm" else ctx.send
            for page in pages:
                await target(page)
            if destination == "dm" and ctx.guild:
                await ctx.tick()
        except (discord.Forbidden, discord.HTTPException):
            if destination == "dm":
                await ctx.send(
                    "I could not send help by DM. Check your privacy settings."
                )

    @commands.group(name="advancedhelpset", aliases=["advhelpset"], invoke_without_command=True)
    @checks.is_owner()
    async def advancedhelpset(self, ctx):
        """Configure the AdvancedHelp formatter."""
        global_data = await self.config.all()
        guild_data = await self.config.guild(ctx.guild).all() if ctx.guild else DEFAULT_GUILD
        await ctx.send(
            f"Destination: {guild_data['destination']}\n"
            f"Timeout: {guild_data['timeout_seconds']} seconds\n"
            f"Audience overrides: {len(global_data['audience_overrides'])}\n"
            f"Category overrides: {len(global_data['category_overrides'])}\n"
            f"Role groups: {len(guild_data['role_groups'])}"
        )

    @advancedhelpset.command(name="destination")
    @commands.guild_only()
    async def destination(self, ctx, mode: str):
        """Choose channel or DM delivery for bare prefix help."""
        mode = mode.lower()
        if mode not in {"channel", "dm"}:
            await ctx.send("Destination must be channel or dm.")
            return
        await self.config.guild(ctx.guild).destination.set(mode)
        await ctx.tick()

    @advancedhelpset.command(name="timeout")
    @commands.guild_only()
    async def timeout(self, ctx, seconds: int):
        """Set the interactive timeout from 30 through 900 seconds."""
        if not 30 <= seconds <= 900:
            await ctx.send("Timeout must be from 30 through 900 seconds.")
            return
        await self.config.guild(ctx.guild).timeout_seconds.set(seconds)
        await ctx.tick()

    @advancedhelpset.command(name="deleteontimeout")
    @commands.guild_only()
    async def delete_on_timeout(self, ctx, enabled: bool):
        """Delete interactive help when it expires instead of disabling it."""
        await self.config.guild(ctx.guild).delete_on_timeout.set(enabled)
        await ctx.tick()

    @advancedhelpset.group(name="audience", invoke_without_command=True)
    async def audience_group(self, ctx):
        """List command audience overrides."""
        overrides = await self.config.audience_overrides()
        await ctx.send(
            "\n".join(f"{key} -> {value}" for key, value in sorted(overrides.items()))
            or "No audience overrides."
        )

    @audience_group.command(name="set")
    async def audience_set(self, ctx, audience: str, *, command_name: str):
        """Assign a command to member, staff, server_owner, or bot_owner."""
        audience = audience.lower()
        if audience not in DEFAULT_AUDIENCE_LABELS:
            await ctx.send("Unknown audience.")
            return
        command = self.bot.get_command(command_name)
        if command is None:
            await ctx.send("That command was not found.")
            return
        overrides = await self.config.audience_overrides()
        overrides[command.qualified_name.lower()] = audience
        await self.config.audience_overrides.set(overrides)
        await ctx.tick()

    @audience_group.command(name="remove")
    async def audience_remove(self, ctx, *, command_name: str):
        command = self.bot.get_command(command_name)
        key = command.qualified_name.lower() if command else command_name.lower()
        overrides = await self.config.audience_overrides()
        existed = overrides.pop(key, None)
        await self.config.audience_overrides.set(overrides)
        await ctx.send("Override removed." if existed else "No override existed.")

    @advancedhelpset.group(name="category", invoke_without_command=True)
    async def category_group(self, ctx):
        """List command category overrides."""
        overrides = await self.config.category_overrides()
        await ctx.send(
            "\n".join(f"{key} -> {value}" for key, value in sorted(overrides.items()))
            or "No category overrides."
        )

    @category_group.command(name="set")
    async def category_set(self, ctx, category: str, *, command_name: str):
        """Assign an available command to a presentation category."""
        command = self.bot.get_command(command_name)
        if command is None:
            await ctx.send("That command was not found.")
            return
        category = category.strip()
        if not category or len(category) > 100:
            await ctx.send("Category names must contain 1 through 100 characters.")
            return
        overrides = await self.config.category_overrides()
        overrides[command.qualified_name.lower()] = category
        await self.config.category_overrides.set(overrides)
        await ctx.tick()

    @category_group.command(name="remove")
    async def category_remove(self, ctx, *, command_name: str):
        command = self.bot.get_command(command_name)
        key = command.qualified_name.lower() if command else command_name.lower()
        overrides = await self.config.category_overrides()
        existed = overrides.pop(key, None)
        await self.config.category_overrides.set(overrides)
        await ctx.send("Override removed." if existed else "No override existed.")

    @audience_group.command(name="appearance")
    async def audience_appearance(
        self, ctx, audience: str, emoji: str, *, text: str
    ):
        """Set audience emoji and name|description."""
        audience = audience.lower()
        if audience not in DEFAULT_AUDIENCE_LABELS:
            await ctx.send("Unknown audience.")
            return
        parts = [part.strip() for part in text.split("|", 1)]
        if not parts[0] or len(parts[0]) > 80:
            await ctx.send("Provide a name up to 80 characters.")
            return
        description = parts[1] if len(parts) == 2 else ""
        if len(description) > 100:
            await ctx.send("Descriptions may contain at most 100 characters.")
            return
        values = await self.config.audience_appearance()
        values[audience] = {
            "name": parts[0], "emoji": emoji, "description": description
        }
        await self.config.audience_appearance.set(values)
        await ctx.tick()

    @audience_group.command(name="order")
    async def audience_order(self, ctx, *, audiences: str):
        """Set all four audience keys in display order."""
        values = audiences.lower().split()
        if set(values) != set(DEFAULT_AUDIENCE_LABELS) or len(values) != 4:
            await ctx.send(
                "Provide member, staff, server_owner, and bot_owner exactly once."
            )
            return
        await self.config.audience_order.set(values)
        await ctx.tick()

    @category_group.command(name="appearance")
    async def category_appearance(
        self, ctx, category: str, order: int, emoji: str, *, text: str
    ):
        """Set category order, emoji, and name|description."""
        parts = [part.strip() for part in text.split("|", 1)]
        if not parts[0] or len(parts[0]) > 100:
            await ctx.send("Provide a display name up to 100 characters.")
            return
        description = parts[1] if len(parts) == 2 else ""
        if len(description) > 100:
            await ctx.send("Descriptions may contain at most 100 characters.")
            return
        values = await self.config.category_appearance()
        values[category] = {
            "name": parts[0],
            "emoji": None if emoji.lower() == "none" else emoji,
            "description": description,
            "order": order,
        }
        await self.config.category_appearance.set(values)
        await ctx.tick()

    @advancedhelpset.group(name="rolegroup", invoke_without_command=True)
    @commands.guild_only()
    async def role_group(self, ctx):
        """List role-backed presentation groups."""
        groups = await self.config.guild(ctx.guild).role_groups()
        lines = [
            f"{name}: roles {', '.join(map(str, data.get('role_ids', [])))}; "
            f"categories {', '.join(data.get('categories', [])) or 'none'}"
            for name, data in sorted(groups.items())
        ]
        await ctx.send("\n".join(lines) if lines else "No role-backed help groups.")

    @role_group.command(name="add")
    async def role_group_add(self, ctx, name: str, role: discord.Role):
        """Add a Discord role to a named presentation group."""
        key = name.strip().lower()
        if not key or len(key) > 50:
            await ctx.send("Group names must contain 1 through 50 characters.")
            return
        groups = await self.config.guild(ctx.guild).role_groups()
        if key not in groups and len(groups) >= 20:
            await ctx.send("A server may configure at most 20 role groups.")
            return
        group = groups.setdefault(
            key,
            {
                "display_name": name.strip(),
                "role_ids": [],
                "categories": [],
                "emoji": "🔖",
                "description": "",
                "order": 1000,
            }
        )
        group["role_ids"] = sorted({*group["role_ids"], role.id})
        await self.config.guild(ctx.guild).role_groups.set(groups)
        await ctx.tick()

    @role_group.command(name="category")
    async def role_group_category(self, ctx, name: str, *, category: str):
        """Add a presentation category to a named role group."""
        groups = await self.config.guild(ctx.guild).role_groups()
        group = groups.get(name.strip().lower())
        if group is None:
            await ctx.send("That role group does not exist.")
            return
        group["categories"] = sorted({*group.get("categories", []), category.strip()})
        await self.config.guild(ctx.guild).role_groups.set(groups)
        await ctx.tick()

    @role_group.command(name="appearance")
    async def role_group_appearance(
        self, ctx, name: str, order: int, emoji: str, *, text: str
    ):
        """Set a role group order, emoji, and name|description."""
        groups = await self.config.guild(ctx.guild).role_groups()
        group = groups.get(name.strip().lower())
        if group is None:
            await ctx.send("That role group does not exist.")
            return
        parts = [part.strip() for part in text.split("|", 1)]
        if not parts[0] or len(parts[0]) > 80:
            await ctx.send("Provide a display name up to 80 characters.")
            return
        description = parts[1] if len(parts) == 2 else ""
        if len(description) > 100:
            await ctx.send("Descriptions may contain at most 100 characters.")
            return
        group.update(
            display_name=parts[0],
            order=order,
            emoji="🔖" if emoji.lower() == "none" else emoji,
            description=description,
        )
        await self.config.guild(ctx.guild).role_groups.set(groups)
        await ctx.tick()

    @role_group.command(name="remove")
    async def role_group_remove(self, ctx, *, name: str):
        groups = await self.config.guild(ctx.guild).role_groups()
        existed = groups.pop(name.strip().lower(), None)
        await self.config.guild(ctx.guild).role_groups.set(groups)
        await ctx.send("Role group removed." if existed else "No role group existed.")
