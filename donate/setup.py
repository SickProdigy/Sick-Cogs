from __future__ import annotations

from typing import List, Optional

import discord


class CardDetailsModal(discord.ui.Modal):
    def __init__(self, cog, guild: discord.Guild, data: dict):
        super().__init__(title="Donation card details")
        self.cog, self.guild = cog, guild
        self.title_input = discord.ui.TextInput(
            label="Card title", default=data.get("title", ""), max_length=256
        )
        self.description_input = discord.ui.TextInput(
            label="Description", default=data.get("description", ""),
            style=discord.TextStyle.paragraph, max_length=4096,
        )
        self.footer_input = discord.ui.TextInput(
            label="Footer", default=data.get("footer", ""), max_length=2048, required=False
        )
        for item in (self.title_input, self.description_input, self.footer_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        ok, message = await self.cog.save_card_details(
            self.guild, str(self.title_input.value), str(self.description_input.value),
            str(self.footer_input.value),
        )
        if not ok:
            await interaction.response.send_message(message, ephemeral=True)
            return
        await interaction.response.edit_message(
            content=message, embed=await self.cog.setup_embed(self.guild),
            view=DonateSetupView(self.cog, interaction.user),
        )


class MethodModal(discord.ui.Modal):
    def __init__(self, cog, guild: discord.Guild, key: str = "", data: Optional[dict] = None):
        super().__init__(title="Donation method")
        self.cog, self.guild, self.original_key = cog, guild, key
        data = data or {}
        self.key_input = discord.ui.TextInput(
            label="Method key", default=key, placeholder="paypal", max_length=40,
            required=not bool(key),
        )
        self.label_input = discord.ui.TextInput(
            label="Display label", default=data.get("label", ""), placeholder="PayPal", max_length=256
        )
        self.value_input = discord.ui.TextInput(
            label="Link, handle, or wallet address", default=data.get("value", ""),
            style=discord.TextStyle.paragraph, max_length=1024,
        )
        self.note_input = discord.ui.TextInput(
            label="Method note (optional)", default=data.get("note", ""),
            style=discord.TextStyle.paragraph, max_length=1024, required=False,
        )
        self.order_input = discord.ui.TextInput(
            label="Display order (0 = alphabetical)",
            default=str(data.get("order") or 0), max_length=4, required=False,
        )
        for item in (self.key_input, self.label_input, self.value_input, self.note_input, self.order_input):
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        ok, message = await self.cog.save_donation_method(
            self.guild, self.original_key, str(self.key_input.value),
            str(self.label_input.value), str(self.value_input.value),
            str(self.note_input.value), str(self.order_input.value),
        )
        if not ok:
            await interaction.response.send_message(message, ephemeral=True)
            return
        await interaction.response.edit_message(
            content=message, embed=await self.cog.setup_embed(self.guild),
            view=DonateSetupView(self.cog, interaction.user),
        )


class MethodSelect(discord.ui.Select):
    def __init__(self, parent: "MethodsView", methods: dict):
        options = [
            discord.SelectOption(
                label=str(data.get("label") or key)[:100], value=key,
                description=f"Key: {key} · order: {data.get('order') or 'alphabetical'}"[:100],
            )
            for key, data in sorted(methods.items(), key=parent.cog._method_sort_key)[:25]
        ]
        super().__init__(placeholder="Choose a donation method to manage", options=options)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        key = self.values[0]
        methods = await self.parent_view.cog.config.guild(interaction.guild).methods()
        data = methods.get(key)
        if data is None:
            await interaction.response.send_message("That donation method no longer exists.", ephemeral=True)
            return
        await interaction.response.edit_message(
            content=f"Managing **{data.get('label') or key}** (`{key}`).",
            embed=None, view=MethodActionView(self.parent_view.cog, interaction.user, key),
        )


class MethodDeleteConfirmView(discord.ui.View):
    def __init__(self, cog, author: discord.Member, key: str):
        super().__init__(timeout=120)
        self.cog, self.author, self.key = cog, author, key

    @discord.ui.button(label="Remove method", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        ok, message = await self.cog.remove_donation_method(interaction.guild, self.key)
        await interaction.response.edit_message(
            content=message, embed=await self.cog.setup_embed(interaction.guild),
            view=DonateSetupView(self.cog, interaction.user),
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="Donation method kept.", embed=await self.cog.setup_embed(interaction.guild),
            view=DonateSetupView(self.cog, interaction.user),
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_check(interaction, self.author)


class MethodActionView(discord.ui.View):
    def __init__(self, cog, author: discord.Member, key: str):
        super().__init__(timeout=300)
        self.cog, self.author, self.key = cog, author, key

    @discord.ui.button(label="Edit method", style=discord.ButtonStyle.primary)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = (await self.cog.config.guild(interaction.guild).methods()).get(self.key)
        if data is None:
            await interaction.response.send_message("That donation method no longer exists.", ephemeral=True)
            return
        await interaction.response.send_modal(MethodModal(self.cog, interaction.guild, self.key, data))

    @discord.ui.button(label="Remove method", style=discord.ButtonStyle.danger)
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=f"Remove donation method `{self.key}`? This cannot be undone from this editor.",
            embed=None, view=MethodDeleteConfirmView(self.cog, interaction.user, self.key),
        )

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=None, embed=await self.cog.setup_embed(interaction.guild),
            view=DonateSetupView(self.cog, interaction.user),
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_check(interaction, self.author)


class MethodsView(discord.ui.View):
    def __init__(self, cog, author: discord.Member, methods: dict):
        super().__init__(timeout=600)
        self.cog, self.author = cog, author
        if methods:
            self.add_item(MethodSelect(self, methods))

    @discord.ui.button(label="Add method", style=discord.ButtonStyle.success, row=1)
    async def add(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(MethodModal(self.cog, interaction.guild))

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=None, embed=await self.cog.setup_embed(interaction.guild),
            view=DonateSetupView(self.cog, interaction.user),
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_check(interaction, self.author)


class NoteModal(discord.ui.Modal):
    def __init__(self, cog, guild: discord.Guild, index: Optional[int] = None, value: str = ""):
        super().__init__(title="Donation note")
        self.cog, self.guild, self.index = cog, guild, index
        self.note_input = discord.ui.TextInput(
            label="Public note", default=value, style=discord.TextStyle.paragraph, max_length=500
        )
        self.add_item(self.note_input)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        ok, message = await self.cog.save_donation_note(
            self.guild, str(self.note_input.value), self.index
        )
        if not ok:
            await interaction.response.send_message(message, ephemeral=True)
            return
        await interaction.response.edit_message(
            content=message, embed=await self.cog.setup_embed(self.guild),
            view=DonateSetupView(self.cog, interaction.user),
        )


class NoteSelect(discord.ui.Select):
    def __init__(self, parent: "NotesView", notes: List[str]):
        options = [
            discord.SelectOption(label=f"Note {index + 1}", value=str(index), description=note[:100])
            for index, note in enumerate(notes[:25])
        ]
        super().__init__(placeholder="Choose a note to manage", options=options)
        self.parent_view = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        index = int(self.values[0])
        notes = await self.parent_view.cog.config.guild(interaction.guild).notes()
        if index >= len(notes):
            await interaction.response.send_message("That note no longer exists.", ephemeral=True)
            return
        await interaction.response.edit_message(
            content=f"Managing note {index + 1}: {notes[index]}", embed=None,
            view=NoteActionView(self.parent_view.cog, interaction.user, index),
        )


class NoteDeleteConfirmView(discord.ui.View):
    def __init__(self, cog, author: discord.Member, index: int):
        super().__init__(timeout=120)
        self.cog, self.author, self.index = cog, author, index

    @discord.ui.button(label="Remove note", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        _, message = await self.cog.remove_donation_note(interaction.guild, self.index)
        await interaction.response.edit_message(
            content=message, embed=await self.cog.setup_embed(interaction.guild),
            view=DonateSetupView(self.cog, interaction.user),
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="Donation note kept.", embed=await self.cog.setup_embed(interaction.guild),
            view=DonateSetupView(self.cog, interaction.user),
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_check(interaction, self.author)


class NoteActionView(discord.ui.View):
    def __init__(self, cog, author: discord.Member, index: int):
        super().__init__(timeout=300)
        self.cog, self.author, self.index = cog, author, index

    @discord.ui.button(label="Edit note", style=discord.ButtonStyle.primary)
    async def edit(self, interaction: discord.Interaction, button: discord.ui.Button):
        notes = await self.cog.config.guild(interaction.guild).notes()
        if self.index >= len(notes):
            await interaction.response.send_message("That note no longer exists.", ephemeral=True)
            return
        await interaction.response.send_modal(NoteModal(self.cog, interaction.guild, self.index, notes[self.index]))

    @discord.ui.button(label="Remove note", style=discord.ButtonStyle.danger)
    async def remove(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=f"Remove donation note {self.index + 1}?", embed=None,
            view=NoteDeleteConfirmView(self.cog, interaction.user, self.index),
        )

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=None, embed=await self.cog.setup_embed(interaction.guild),
            view=DonateSetupView(self.cog, interaction.user),
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_check(interaction, self.author)


class NotesView(discord.ui.View):
    def __init__(self, cog, author: discord.Member, notes: List[str]):
        super().__init__(timeout=600)
        self.cog, self.author = cog, author
        if notes:
            self.add_item(NoteSelect(self, notes))

    @discord.ui.button(label="Add note", style=discord.ButtonStyle.success, row=1)
    async def add(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(NoteModal(self.cog, interaction.guild))

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content=None, embed=await self.cog.setup_embed(interaction.guild),
            view=DonateSetupView(self.cog, interaction.user),
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_check(interaction, self.author)


class ResetConfirmView(discord.ui.View):
    def __init__(self, cog, author: discord.Member):
        super().__init__(timeout=120)
        self.cog, self.author = cog, author

    @discord.ui.button(label="Restore defaults", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.reset_donation_settings(interaction.guild)
        await interaction.response.edit_message(
            content="Donation settings restored to the default card.",
            embed=await self.cog.setup_embed(interaction.guild),
            view=DonateSetupView(self.cog, interaction.user),
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="Donation settings kept.", embed=await self.cog.setup_embed(interaction.guild),
            view=DonateSetupView(self.cog, interaction.user),
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_check(interaction, self.author)


class DonateSetupView(discord.ui.View):
    def __init__(self, cog, author: discord.Member):
        super().__init__(timeout=900)
        self.cog, self.author = cog, author

    @discord.ui.button(label="Card details", style=discord.ButtonStyle.primary)
    async def details(self, interaction: discord.Interaction, button: discord.ui.Button):
        data = await self.cog.donation_settings(interaction.guild)
        await interaction.response.send_modal(CardDetailsModal(self.cog, interaction.guild, data))

    @discord.ui.button(label="Donation methods", style=discord.ButtonStyle.primary)
    async def methods(self, interaction: discord.Interaction, button: discord.ui.Button):
        methods = await self.cog.config.guild(interaction.guild).methods()
        await interaction.response.edit_message(
            content="Add a method or choose one to edit, reorder, or remove.", embed=None,
            view=MethodsView(self.cog, interaction.user, methods),
        )

    @discord.ui.button(label="Notes", style=discord.ButtonStyle.secondary)
    async def notes(self, interaction: discord.Interaction, button: discord.ui.Button):
        notes = await self.cog.config.guild(interaction.guild).notes()
        await interaction.response.edit_message(
            content="Add a note or choose one to edit or remove.", embed=None,
            view=NotesView(self.cog, interaction.user, notes),
        )

    @discord.ui.button(label="Reset defaults", style=discord.ButtonStyle.danger)
    async def reset(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="Restore the complete default donation card? This replaces current card text, methods, and notes.",
            embed=None, view=ResetConfirmView(self.cog, interaction.user),
        )

    @discord.ui.button(label="Done", style=discord.ButtonStyle.success)
    async def done(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="Donation setup saved. Members can now use the donate command.",
            embed=await self.cog.setup_embed(interaction.guild), view=None,
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await owner_check(interaction, self.author)


async def owner_check(interaction: discord.Interaction, author: discord.Member) -> bool:
    if interaction.user.id != author.id:
        await interaction.response.send_message("Open your own donation setup card.", ephemeral=True)
        return False
    if not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message(
            "You need Manage Server permission to use donation setup.", ephemeral=True
        )
        return False
    return True
