import discord
from redbot.core import Config, commands
from redbot.core.utils.chat_formatting import pagify


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
        return value.strip().lower().replace(" ", "-")

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

    async def _donation_embed(self, ctx: commands.Context) -> discord.Embed:
        title = await self.config.guild(ctx.guild).title()
        description = await self.config.guild(ctx.guild).description()
        footer = await self.config.guild(ctx.guild).footer()
        methods = await self.config.guild(ctx.guild).methods()
        notes = await self.config.guild(ctx.guild).notes()

        embed = discord.Embed(
            title=title or DEFAULT_TITLE,
            description=description or DEFAULT_DESCRIPTION,
            color=await ctx.embed_color(),
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
            await ctx.send(page)

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

    @donate_set.command(name="view")
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
        await ctx.send("\n".join(lines))

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
        await ctx.send(f"Removed note: {removed}")

    @donate_set.command(name="clear")
    @commands.guild_only()
    @commands.admin_or_permissions(manage_guild=True)
    async def donateset_clear(self, ctx: commands.Context):
        """Reset this server's donation settings."""

        await self.config.guild(ctx.guild).clear()
        await ctx.send("Donation settings reset.")
