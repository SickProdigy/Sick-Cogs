import re

import discord
from redbot.core import Config, commands
from redbot.core.utils.chat_formatting import pagify

from .setup import DonateSetupView


DEFAULT_TITLE = "Support SickGaming"
DEFAULT_DESCRIPTION = (
    "Donations help keep the community, servers, events, and projects running. "
    "Every bit of support helps us keep building and hosting more for everyone."
)
DEFAULT_FOOTER = "Thank you for supporting SickGaming.net"
DEFAULT_METHODS = {
    "donation-page": {
        "label": "Donation Page",
        "value": "https://sickgaming.net/misc.php?action=help&hid=13",
        "note": "Review benefit levels and donation details.",
        "order": 1,
    },
    "paypal": {
        "label": "PayPal",
        "value": "https://www.paypal.me/SickGamingNet",
        "note": "One-time donations through PayPal.",
    },
    "patreon": {
        "label": "Patreon",
        "value": "https://www.patreon.com/SickGaming",
        "note": "Recurring support for the community.",
    },
    "btc": {
        "label": "Bitcoin (BTC)",
        "value": "1GVxfmPtNwEm4wmm7miuEYrpAYxEK6dMwR",
        "note": "Bitcoin network only.",
        "code": True,
    },
    "eth": {
        "label": "Ethereum (ETH)",
        "value": "0x2e19d3A3c040E5Be9dF4797d8e2de056E39DFBa9",
        "note": "ETH/EVM network only.",
        "code": True,
    },
    "ltc": {
        "label": "Litecoin (LTC)",
        "value": "LdDkPbPbLtDfg1331EgWmsHUWXLDkiXGTM",
        "note": "Litecoin network only.",
        "code": True,
    },
}
DEFAULT_NOTES = [
    "Donations are optional and help keep SickGaming community services running.",
    "All donations are final and non-refundable.",
]


def default_methods() -> dict:
    return {key: value.copy() for key, value in DEFAULT_METHODS.items()}


class Donate(commands.Cog):
    """Share configured donation links and support options."""

    __author__ = ["SickProdigy"]
    __version__ = "1.1.0"

    def __init__(self, bot):
        self.bot = bot
        self.config = Config.get_conf(self, identifier=5829017346, force_registration=True)
        self.config.register_guild(
            title=DEFAULT_TITLE,
            description=DEFAULT_DESCRIPTION,
            footer=DEFAULT_FOOTER,
            methods=default_methods(),
            notes=list(DEFAULT_NOTES),
        )

    async def red_delete_data_for_user(self, **kwargs):
        """Nothing to delete."""
        return

    @staticmethod
    def _clean_key(value: str) -> str:
        value = re.sub(r"[^a-z0-9_-]+", "-", value.strip().lower())
        return value.strip("-_")[:40]

    @staticmethod
    def _display_label(key: str, data: dict) -> str:
        label = str(data.get("label") or key).strip()
        note = str(data.get("note") or "").strip()
        if data.get("code") and note:
            label = f"{label} - {note}"
        return label or key

    @staticmethod
    def _method_sort_key(item: tuple) -> tuple:
        key, data = item
        order = data.get("order")
        if isinstance(order, int) and order > 0:
            return 0, order, Donate._display_label(key, data).lower()
        return 1, Donate._display_label(key, data).lower()

    @staticmethod
    def _format_method_value(data: dict) -> str:
        value = str(data.get("value") or "").strip()
        note = str(data.get("note") or "").strip()
        if data.get("code") and value:
            value = "```text\n" + value.replace("```", "'''") + "\n```"
        if note and not data.get("code"):
            return f"{value}\n{note}"
        return value

    async def donation_settings(self, guild: discord.Guild) -> dict:
        group = self.config.guild(guild)
        return {
            "title": await group.title(), "description": await group.description(),
            "footer": await group.footer(), "methods": await group.methods(),
            "notes": await group.notes(),
        }

    async def donation_embed_for(self, guild: discord.Guild, color: discord.Color) -> discord.Embed:
        settings = await self.donation_settings(guild)
        title, description, footer = settings["title"], settings["description"], settings["footer"]
        methods, notes = settings["methods"], settings["notes"]

        embed = discord.Embed(
            title=title or DEFAULT_TITLE,
            description=description or DEFAULT_DESCRIPTION,
            color=color,
        )

        if methods:
            for key, data in sorted(methods.items(), key=self._method_sort_key):
                value = self._format_method_value(data)
                if value:
                    embed.add_field(name=self._display_label(key, data), value=value[:1024], inline=False)
        else:
            embed.add_field(
                name="Donation Options",
                value="No donation methods have been configured yet.",
                inline=False,
            )

        if notes:
            note_text = "\n".join(f"- {note}" for note in notes if note)
            if note_text:
                embed.add_field(name="Notes", value=note_text[:1024], inline=False)

        embed.set_footer(text=footer or DEFAULT_FOOTER)
        return embed

    async def _donation_embed(self, ctx: commands.Context) -> discord.Embed:
        return await self.donation_embed_for(ctx.guild, await ctx.embed_color())

    async def setup_embed(self, guild: discord.Guild) -> discord.Embed:
        embed = await self.donation_embed_for(guild, discord.Color.blurple())
        embed.set_author(name="Donate setup preview · submitted forms save immediately")
        return embed

    def validate_embed_budget(self, settings: dict):
        total = len(settings.get("title") or DEFAULT_TITLE)
        total += len(settings.get("description") or DEFAULT_DESCRIPTION)
        total += len(settings.get("footer") or DEFAULT_FOOTER)
        for key, data in settings.get("methods", {}).items():
            field_value = self._format_method_value(data)
            if len(field_value) > 1024:
                return False, f"Donation method `{key}` is too long for one Discord field."
            total += len(self._display_label(key, data)) + len(field_value)
        notes = [note for note in settings.get("notes", []) if note]
        if notes:
            note_text = "\n".join(f"- {note}" for note in notes)
            if len(note_text) > 1024:
                return False, "The combined donation notes are too long for Discord."
            total += len("Notes") + len(note_text)
        if total > 5900:
            return False, "Those changes would make the donation card too long for Discord."
        return True, ""

    async def save_card_details(self, guild: discord.Guild, title: str, description: str, footer: str):
        title, description, footer = title.strip(), description.strip(), footer.strip()
        if not title or not description:
            return False, "The card title and description cannot be empty."
        settings = await self.donation_settings(guild)
        settings.update({"title": title[:256], "description": description[:4096], "footer": footer[:2048]})
        valid, message = self.validate_embed_budget(settings)
        if not valid:
            return False, message
        group = self.config.guild(guild)
        await group.title.set(settings["title"])
        await group.description.set(settings["description"])
        await group.footer.set(settings["footer"])
        return True, "Donation card details updated."

    async def save_donation_method(
        self, guild: discord.Guild, original_key: str, key: str, label: str,
        value: str, note: str, order_text: str,
    ):
        method_key = self._clean_key(key)
        label, value, note = label.strip(), value.strip(), note.strip()
        if not method_key:
            return False, "Method key must contain a letter or number."
        if not label or not value:
            return False, "Method label and value cannot be empty."
        try:
            order = int(order_text.strip() or "0")
        except ValueError:
            return False, "Display order must be a whole number from 0 to 999."
        if order < 0 or order > 999:
            return False, "Display order must be from 0 to 999."
        methods = await self.config.guild(guild).methods()
        if not original_key and method_key not in methods and len(methods) >= 24:
            return False, "A donation card supports at most 24 methods so one field remains available for notes."
        if original_key and method_key != original_key and method_key in methods:
            return False, f"A donation method already uses key `{method_key}`."
        previous = dict(methods.get(original_key or method_key, {}))
        data = {"label": label[:256], "value": value[:1024], "note": note[:1024]}
        if previous.get("code"):
            data["code"] = True
        if order:
            data["order"] = order
        if original_key and original_key != method_key:
            methods.pop(original_key, None)
        methods[method_key] = data
        settings = await self.donation_settings(guild)
        settings["methods"] = methods
        valid, message = self.validate_embed_budget(settings)
        if not valid:
            return False, message
        await self.config.guild(guild).methods.set(methods)
        return True, f"Donation method `{method_key}` saved."

    async def remove_donation_method(self, guild: discord.Guild, key: str):
        methods = await self.config.guild(guild).methods()
        if key not in methods:
            return False, "That donation method no longer exists."
        methods.pop(key)
        await self.config.guild(guild).methods.set(methods)
        return True, f"Donation method `{key}` removed."

    async def save_donation_note(self, guild: discord.Guild, note: str, index=None):
        note = note.strip()
        if not note:
            return False, "Donation note cannot be empty."
        notes = await self.config.guild(guild).notes()
        if index is None:
            if len(notes) >= 25:
                return False, "The interactive editor supports at most 25 notes."
            notes.append(note[:500])
        elif index < 0 or index >= len(notes):
            return False, "That donation note no longer exists."
        else:
            notes[index] = note[:500]
        settings = await self.donation_settings(guild)
        settings["notes"] = notes
        valid, message = self.validate_embed_budget(settings)
        if not valid:
            return False, message
        await self.config.guild(guild).notes.set(notes)
        return True, "Donation note saved."

    async def remove_donation_note(self, guild: discord.Guild, index: int):
        notes = await self.config.guild(guild).notes()
        if index < 0 or index >= len(notes):
            return False, "That donation note no longer exists."
        removed = notes.pop(index)
        await self.config.guild(guild).notes.set(notes)
        return True, f"Removed note: {removed}"

    async def reset_donation_settings(self, guild: discord.Guild) -> None:
        await self.config.guild(guild).clear()

    @commands.group(name="donate", aliases=("donations", "support"), invoke_without_command=True)
    @commands.guild_only()
    async def donate(self, ctx: commands.Context):
        """Share this server's donation links and support options in one clean message.

        Server admins can configure donations with `donate set`.
        """

        embed = await self._donation_embed(ctx)
        if ctx.me is not None and ctx.channel.permissions_for(ctx.me).embed_links:
            await ctx.send(embed=embed)
            return

        methods = await self.config.guild(ctx.guild).methods()
        notes = await self.config.guild(ctx.guild).notes()
        lines = [embed.title or DEFAULT_TITLE, embed.description or ""]
        for key, data in sorted(methods.items(), key=self._method_sort_key):
            lines.append(f"{self._display_label(key, data)}: {self._format_method_value(data)}")
        if notes:
            lines.extend(["Notes", *[f"- {note}" for note in notes if note]])
        for page in pagify("\n".join(line for line in lines if line), delims=["\n"], page_length=1800):
            await ctx.send(page, allowed_mentions=discord.AllowedMentions.none())

    @donate.group(name="set", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def donate_set(self, ctx: commands.Context):
        """Configure this server's donation display.

        This is an admin command. Users can run `donate`,
        `donations`, or `support` after donation methods are configured.
        """

        message = (
            "**Donate Settings**\n\n"
            "- `donate set setup` (alias: `interactive`)\n"
            "- `donate set view`\n"
            "- `donate set title <text>`\n"
            "- `donate set description <text>`\n"
            "- `donate set footer <text>`\n"
            "- `donate set method <key> <label> | <value> [| note]`\n"
            "- `donate set order <key> <number>`\n"
            "- `donate set remove <key>`\n"
            "- `donate set note add <text>`\n"
            "- `donate set note remove <number>`\n"
            "- `donate set clear`\n\n"
            "Example: `donate set method paypal PayPal | https://paypal.me/example`"
        )
        await ctx.send(message)

    @donate_set.command(name="setup", aliases=["interactive"])
    @commands.bot_has_permissions(embed_links=True)
    async def donateset_setup(self, ctx: commands.Context):
        """Open the interactive donation-card setup dashboard.

        Existing text commands remain available as command-line fallbacks.
        """
        await ctx.send(
            embed=await self.setup_embed(ctx.guild),
            view=DonateSetupView(self, ctx.author),
        )

    @donate_set.command(name="view")
    @commands.bot_has_permissions(embed_links=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def donateset_view(self, ctx: commands.Context):
        """Preview the donation embed users will see."""

        await ctx.send(embed=await self._donation_embed(ctx))

    @donate_set.command(name="title")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def donateset_title(self, ctx: commands.Context, *, title: str):
        """Set the donation embed title.

        Example:
        `donate set title Support SickGaming`
        """

        await self.config.guild(ctx.guild).title.set(title.strip()[:256])
        await ctx.send("Donation title updated.")

    @donate_set.command(name="description")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def donateset_description(self, ctx: commands.Context, *, description: str):
        """Set the donation embed description.

        Example:
        `donate set description Donations help keep the community running.`
        """

        await self.config.guild(ctx.guild).description.set(description.strip()[:2048])
        await ctx.send("Donation description updated.")

    @donate_set.command(name="footer")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def donateset_footer(self, ctx: commands.Context, *, footer: str):
        """Set the donation embed footer.

        Example:
        `donate set footer Thank you for supporting SickGaming.net`
        """

        await self.config.guild(ctx.guild).footer.set(footer.strip()[:2048])
        await ctx.send("Donation footer updated.")

    @donate_set.command(name="method")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def donateset_method(self, ctx: commands.Context, key: str, *, method: str):
        """Add or update a donation method.

        Use:
        `donate set method <key> <label> | <value> [| note]`

        Examples:
        `donate set method paypal PayPal | https://paypal.me/example`
        `donate set method cashapp Cash App | $example`
        `donate set method btc Bitcoin | bc1qexampleaddress | BTC only.`
        """

        parts = [part.strip() for part in method.split("|", 2)]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            return await ctx.send(
                "Use `donate set method <key> <label> | <value> [| note]`, "
                "for example `donate set method paypal PayPal | https://paypal.me/example`."
            )

        method_key = self._clean_key(key)
        if not method_key:
            return await ctx.send("Method key cannot be empty.")

        label, value = parts[0], parts[1]
        note = parts[2] if len(parts) > 2 else ""
        async with self.config.guild(ctx.guild).methods() as methods:
            methods[method_key] = {
                "label": label[:256],
                "value": value[:1024],
                "note": note[:1024],
            }

        await ctx.send(f"Donation method `{method_key}` updated.")

    @donate_set.command(name="order")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def donateset_order(self, ctx: commands.Context, key: str, position: int):
        """Pin a donation method to a display position.

        Ordered methods show first by number. Unordered methods show after
        them in alphabetical order. Use `0` to remove a pinned order.

        Example:
        `donate set order donation-page 1`
        """

        method_key = self._clean_key(key)
        async with self.config.guild(ctx.guild).methods() as methods:
            if method_key not in methods:
                return await ctx.send(f"`{method_key}` is not configured.")
            if position < 0:
                return await ctx.send("Order number must be 0 or higher.")
            if position == 0:
                methods[method_key].pop("order", None)
                return await ctx.send(f"Donation method `{method_key}` order cleared.")
            methods[method_key]["order"] = position

        await ctx.send(f"Donation method `{method_key}` order set to {position}.")

    @donate_set.command(name="remove")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def donateset_remove(self, ctx: commands.Context, key: str):
        """Remove a configured donation method by key.

        Example:
        `donate set remove paypal`
        """

        method_key = self._clean_key(key)
        async with self.config.guild(ctx.guild).methods() as methods:
            if method_key not in methods:
                return await ctx.send(f"`{method_key}` is not configured.")
            methods.pop(method_key)

        await ctx.send(f"Donation method `{method_key}` removed.")

    @donate_set.group(name="note", invoke_without_command=True)
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def donateset_note(self, ctx: commands.Context):
        """List or manage donation notes.

        Notes appear at the bottom of the public donation embed.
        """

        notes = await self.config.guild(ctx.guild).notes()
        if not notes:
            return await ctx.send("No donation notes are configured.")
        lines = [f"{index}. {note}" for index, note in enumerate(notes, start=1)]
        await ctx.send("\n".join(lines), allowed_mentions=discord.AllowedMentions.none())

    @donateset_note.command(name="add")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def donateset_note_add(self, ctx: commands.Context, *, note: str):
        """Add a donation note.

        Example:
        `donate set note add Donations are optional and never required.`
        """

        note = note.strip()
        if not note:
            return await ctx.send("Donation note cannot be empty.")

        async with self.config.guild(ctx.guild).notes() as notes:
            notes.append(note[:500])
        await ctx.send("Donation note added.")

    @donateset_note.command(name="remove")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def donateset_note_remove(self, ctx: commands.Context, index: int):
        """Remove a donation note by number.

        Example:
        `donate set note remove 1`
        """

        async with self.config.guild(ctx.guild).notes() as notes:
            if index < 1 or index > len(notes):
                return await ctx.send("That note number does not exist.")
            removed = notes.pop(index - 1)
        await ctx.send(f"Removed note: {removed}", allowed_mentions=discord.AllowedMentions.none())

    @donate_set.command(name="clear")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def donateset_clear(self, ctx: commands.Context):
        """Reset this server's donation settings."""

        await self.config.guild(ctx.guild).clear()
        await ctx.send("Donation settings reset.")
