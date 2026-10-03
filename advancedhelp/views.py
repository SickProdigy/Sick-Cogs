import math

import discord


PAGE_SIZE = 10


class HelpSelect(discord.ui.Select):
    def __init__(self, view, kind, options):
        placeholder = {
            "audience": "Choose an audience",
            "category": "Choose a category",
            "command": "Choose a command",
        }[kind]
        super().__init__(placeholder=placeholder, options=options[:25])
        self.help_view = view
        self.kind = kind

    async def callback(self, interaction):
        value = self.values[0]
        if self.kind == "audience":
            self.help_view.audience = value
            self.help_view.category = None
            self.help_view.screen = "audience"
        elif self.kind == "category":
            self.help_view.category = value
            self.help_view.screen = "category"
        else:
            self.help_view.command_name = value
            self.help_view.screen = "command"
        self.help_view.page = 0
        await self.help_view.render(interaction)


class NavButton(discord.ui.Button):
    def __init__(self, view, action, label, style=discord.ButtonStyle.secondary):
        super().__init__(label=label, style=style)
        self.help_view = view
        self.action = action

    async def callback(self, interaction):
        view = self.help_view
        if self.action == "home":
            view.screen, view.audience, view.category, view.page = "home", None, None, 0
        elif self.action == "back":
            if view.screen == "command":
                view.screen = "category"
            elif view.screen == "category":
                view.screen, view.category = "audience", None
            else:
                view.screen, view.audience = "home", None
            view.page = 0
        elif self.action == "previous":
            view.page = max(0, view.page - 1)
        elif self.action == "next":
            view.page += 1
        await view.render(interaction)


class AdvancedHelpView(discord.ui.View):
    def __init__(self, cog, ctx, catalog, timeout, delete_on_timeout=False):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.ctx = ctx
        self.catalog = catalog
        self.author_id = ctx.author.id
        self.screen = "home"
        self.audience = None
        self.category = None
        self.command_name = None
        self.page = 0
        self.message = None
        self.delete_on_timeout = delete_on_timeout
        self.rebuild()

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Open your own help menu.", ephemeral=True
            )
            return False
        return True

    def _pages(self, items):
        return max(1, math.ceil(len(items) / PAGE_SIZE))

    def _slice(self, items):
        pages = self._pages(items)
        self.page = min(self.page, pages - 1)
        start = self.page * PAGE_SIZE
        return items[start:start + PAGE_SIZE], pages

    def _category_option(self, name):
        label, emoji, description = self.catalog.category_metadata(name)
        count = len(self.catalog.commands(self.audience, name))
        return discord.SelectOption(
            label=label[:100], value=name, emoji=emoji,
            description=(description or f"{count} commands")[:100],
        )

    def rebuild(self):
        self.clear_items()
        if self.screen == "home":
            options = []
            for key in self.catalog.audiences:
                label, emoji, description = self.catalog.metadata(key)
                options.append(
                    discord.SelectOption(
                        label=label, value=key, emoji=emoji, description=description[:100]
                    )
                )
            if options:
                self.add_item(HelpSelect(self, "audience", options))
        elif self.screen == "audience":
            categories = self.catalog.categories(self.audience)
            visible, pages = self._slice(categories)
            self.add_item(
                HelpSelect(
                    self,
                    "category",
                    [self._category_option(name) for name in visible],
                )
            )
            self._add_paging(pages)
            self.add_item(NavButton(self, "home", "Home"))
        elif self.screen == "category":
            commands = self.catalog.commands(self.audience, self.category)
            visible, pages = self._slice(commands)
            self.add_item(
                HelpSelect(
                    self,
                    "command",
                    [
                        discord.SelectOption(
                            label=command.qualified_name[:100],
                            value=command.qualified_name,
                            description=command.format_shortdoc_for_context(self.ctx)[:100] or None,
                        )
                        for command in visible
                    ],
                )
            )
            self._add_paging(pages)
            self.add_item(NavButton(self, "back", "Back"))
            self.add_item(NavButton(self, "home", "Home"))
        else:
            self.add_item(NavButton(self, "back", "Back"))
            self.add_item(NavButton(self, "home", "Home"))

    def _add_paging(self, pages):
        if pages > 1:
            previous = NavButton(self, "previous", "Previous")
            previous.disabled = self.page == 0
            following = NavButton(self, "next", "Next")
            following.disabled = self.page >= pages - 1
            self.add_item(previous)
            self.add_item(following)

    def selected_command(self):
        for command in self.catalog.commands(self.audience, self.category):
            if command.qualified_name == self.command_name:
                return command
        return None

    async def embed(self):
        color = await self.ctx.embed_color()
        if self.screen == "home":
            embed = discord.Embed(
                title="Help",
                description="Choose an available audience area.",
                color=color,
            )
            for key in self.catalog.audiences:
                label, emoji, description = self.catalog.metadata(key)
                categories = len(self.catalog.categories(key))
                embed.add_field(
                    name=f"{emoji} {label}",
                    value=f"{description}\n{categories} categories",
                    inline=False,
                )
            return embed
        if self.screen == "audience":
            label, emoji, _ = self.catalog.metadata(self.audience)
            categories = self.catalog.categories(self.audience)
            visible, pages = self._slice(categories)
            embed = discord.Embed(
                title=f"{emoji} {label}",
                description="\n".join(
                    f"**{self.catalog.category_metadata(name)[0]}** — "
                    f"{len(self.catalog.commands(self.audience, name))} commands"
                    for name in visible
                ),
                color=color,
            )
            embed.set_footer(text=f"Category page {self.page + 1}/{pages}")
            return embed
        if self.screen == "category":
            commands = self.catalog.commands(self.audience, self.category)
            visible, pages = self._slice(commands)
            embed = discord.Embed(
                title=self.catalog.category_metadata(self.category)[0],
                description="\n".join(
                    f"**{command.qualified_name}** — "
                    f"{command.format_shortdoc_for_context(self.ctx) or 'No description.'}"
                    for command in visible
                ),
                color=color,
            )
            embed.set_footer(text=f"Command page {self.page + 1}/{pages}")
            return embed
        command = self.selected_command()
        if command is not None:
            try:
                visible = await command.can_see(self.ctx)
                runnable = await command.can_run(
                    self.ctx, check_all_parents=True
                )
            except Exception:
                visible = runnable = False
            if not visible or not runnable:
                command = None
        if command is None:
            return discord.Embed(
                title="Command unavailable",
                description="Return to the category and choose again.",
                color=color,
            )
        signature = self.cog.formatter.get_command_signature(self.ctx, command)
        help_text = command.format_help_for_context(self.ctx) or "No additional help."
        embed = discord.Embed(
            title=command.qualified_name,
            description=help_text[:4000],
            color=color,
        )
        embed.add_field(name="Syntax", value=signature[:1024], inline=False)
        if command.aliases:
            embed.add_field(
                name="Aliases", value=", ".join(command.aliases)[:1024], inline=False
            )
        return embed

    async def render(self, interaction):
        self.rebuild()
        await interaction.response.edit_message(embed=await self.embed(), view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            try:
                if self.delete_on_timeout:
                    await self.message.delete()
                    return
                await self.message.edit(
                    content="This help session expired.", view=self
                )
            except discord.HTTPException:
                pass
