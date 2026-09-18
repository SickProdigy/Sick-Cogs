from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

import discord
from red_commons.logging import getLogger
from redbot.core import Config, commands
from redbot.core.bot import Red
from redbot.core.commands import Context
from redbot.core.i18n import Translator

from .converter import (
    ButtonStyleConverter,
    RawUserIds,
    RoleEmojiConverter,
    RoleHierarchyConverter,
    SelfRoleConverter,
)

if TYPE_CHECKING:
    from .buttons import ButtonRole, ButtonRoleConverter
    from .select import SelectOptionRoleConverter, SelectRole, SelectRoleConverter


log = getLogger("red.Sick-Cogs.RoleTools")
_ = Translator("Roletools", __file__)


class RoleToolsMixin(ABC):
    """
    Base class for well behaved type hint detection with composite class.

    Basically, to keep developers sane when not all attributes are defined in each mixin.
    """

    def __init__(self, *_args):
        super().__init__()
        self.config: Config
        self.bot: Red
        self.settings: Dict[Any, Any]
        self._ready: asyncio.Event
        self.views: Dict[int, Dict[str, discord.ui.View]]
        self.is_discord: bool

    @commands.group(invoke_without_command=True)
    @commands.guild_only()
    async def roletools(self, ctx: Context) -> None:
        """Member role help and server role-management commands."""
        if ctx.invoked_subcommand is not None:
            return
        prefix = ctx.clean_prefix
        embed = discord.Embed(
            title="RoleTools",
            description="View the self-roles this server has made available, then add or remove one.",
            color=discord.Color.blurple(),
        )
        embed.add_field(
            name="1. View available self-roles",
            value=f"`{prefix}selfroles` lists roles you can add or remove (`{prefix}roletools viewroles` also works).",
            inline=False,
        )
        embed.add_field(
            name="2. Add or remove a self-role",
            value=f"`{prefix}roletools selfrole @Role` toggles an available role. Mentions, IDs, and role names are accepted.",
            inline=False,
        )
        embed.add_field(
            name="3. Or use a role menu",
            value="Reaction, button, and select role messages can be used directly. Their result is shown to you privately.",
            inline=False,
        )
        embed.add_field(
            name="Server managers",
            value=(
                f"`{prefix}roletools setup` opens the interactive role catalog, publishing, "
                f"synchronization, and removal controls. `{prefix}roletools adminhelp` shows "
                "the advanced command-based setup options."
            ),
            inline=False,
        )
        embed.add_field(
            name="Need more detail?",
            value=f"`{prefix}help roletools selfrole` explains member role commands. `{prefix}help roletools setup` explains the manager setup card.",
            inline=False,
        )
        await ctx.send(embed=embed)

    #######################################################################
    # roletools.py                                                        #
    #######################################################################

    @abstractmethod
    async def confirm_selfassignable(
        self, ctx: commands.Context, roles: List[discord.Role]
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def selfrole(self, ctx: commands.Context, *, role: SelfRoleConverter) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def selfrole_add(self, ctx: commands.Context, *, role: SelfRoleConverter) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def selfrole_remove(self, ctx: commands.Context, *, role: SelfRoleConverter) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def giverole(
        self,
        ctx: commands.Context,
        role: RoleHierarchyConverter,
        *who: Union[discord.Role, discord.TextChannel, discord.Member, str],
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def removerole(
        self,
        ctx: commands.Context,
        role: RoleHierarchyConverter,
        *who: Union[discord.Role, discord.TextChannel, discord.Member, str],
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def forcerole(
        self,
        ctx: commands.Context,
        users: commands.Greedy[Union[discord.Member, RawUserIds]],
        *,
        role: RoleHierarchyConverter,
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def forceroleremove(
        self,
        ctx: commands.Context,
        users: commands.Greedy[Union[discord.Member, RawUserIds]],
        *,
        role: RoleHierarchyConverter,
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def viewroles(self, ctx: commands.Context, *, role: Optional[discord.Role]) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def roletools_slash(self, ctx: Context) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def roletools_global_slash(self, ctx: Context) -> None:
        raise NotImplementedError()

    #######################################################################
    # inclusive.py                                                        #
    #######################################################################

    @abstractmethod
    async def inclusive(self, ctx: commands.Context) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def inclusive_add(
        self,
        ctx: commands.Context,
        role: RoleHierarchyConverter,
        *,
        include: commands.Greedy[RoleHierarchyConverter],
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def mutual_inclusive_add(self, ctx: Context, *roles: RoleHierarchyConverter) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def inclusive_remove(
        self,
        ctx: commands.Context,
        role: RoleHierarchyConverter,
        *,
        include: commands.Greedy[RoleHierarchyConverter],
    ) -> None:
        raise NotImplementedError()

    #######################################################################
    # exclusive.py                                                        #
    #######################################################################

    @abstractmethod
    async def exclusive(self, ctx: commands.Context) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def exclusive_add(
        self,
        ctx: commands.Context,
        role: RoleHierarchyConverter,
        *,
        exclude: commands.Greedy[RoleHierarchyConverter],
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def mutual_exclusive_add(self, ctx: Context, *roles: RoleHierarchyConverter) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def exclusive_remove(
        self,
        ctx: commands.Context,
        role: RoleHierarchyConverter,
        *,
        exclude: commands.Greedy[RoleHierarchyConverter],
    ) -> None:
        raise NotImplementedError()

    #######################################################################
    # requires.py                                                         #
    #######################################################################

    @abstractmethod
    async def required_roles(self, ctx: commands.Context) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def required_add(
        self,
        ctx: commands.Context,
        role: RoleHierarchyConverter,
        *,
        required: commands.Greedy[RoleHierarchyConverter],
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def required_remove(
        self,
        ctx: commands.Context,
        role: RoleHierarchyConverter,
        *,
        required: commands.Greedy[RoleHierarchyConverter],
    ) -> None:
        raise NotImplementedError()

    #######################################################################
    # settings.py                                                         #
    #######################################################################

    @abstractmethod
    async def selfadd(
        self,
        ctx: commands.Context,
        true_or_false: Optional[bool] = None,
        *,
        role: RoleHierarchyConverter,
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def selfrem(
        self,
        ctx: commands.Context,
        true_or_false: Optional[bool] = None,
        *,
        role: RoleHierarchyConverter,
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def atomic(
        self, ctx: commands.Context, true_or_false: Optional[Union[bool, str]] = None
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def globalatomic(self, ctx: Context, true_or_false: Optional[bool] = None) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def cost(
        self,
        ctx: commands.Context,
        cost: Optional[int] = None,
        *,
        role: RoleHierarchyConverter,
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def sticky(
        self,
        ctx: commands.Context,
        true_or_false: Optional[bool] = None,
        *,
        role: RoleHierarchyConverter,
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def autorole(
        self,
        ctx: commands.Context,
        true_or_false: Optional[bool] = None,
        *,
        role: RoleHierarchyConverter,
    ) -> None:
        raise NotImplementedError()

    #######################################################################
    # reactions.py                                                        #
    #######################################################################

    @abstractmethod
    async def react_coms(self, ctx: Context) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def cleanup(self, ctx: commands.Context) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def ownercleanup(self, ctx: Context) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def reactroles(self, ctx: commands.Context) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def clearreact(
        self,
        ctx: commands.Context,
        message: discord.Message,
        *emojis: Optional[Union[discord.Emoji, str]],
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def make_react(
        self,
        ctx: commands.Context,
        message: discord.Message,
        emoji: Union[discord.Emoji, str],
        *,
        role: RoleHierarchyConverter,
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def remreact(
        self,
        ctx: commands.Context,
        message: discord.Message,
        *,
        role_or_emoji: Union[RoleHierarchyConverter, discord.Emoji, str],
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def bulkreact(
        self,
        ctx: Context,
        message: discord.Message,
        *role_emoji: RoleEmojiConverter,
    ) -> None:
        raise NotImplementedError()

    #######################################################################
    # events.py                                                           #
    #######################################################################

    @abstractmethod
    async def check_guild_verification(
        self, member: discord.Member, guild: discord.Guild
    ) -> Union[bool, int]:
        raise NotImplementedError()

    @abstractmethod
    async def wait_for_verification(self, member: discord.Member, guild: discord.Guild) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def check_atomicity(self, guild: discord.Guild) -> bool:
        raise NotImplementedError()

    @abstractmethod
    async def give_roles(
        self,
        member: discord.Member,
        roles: List[discord.Role],
        reason: Optional[str] = None,
        *,
        check_required: bool = True,
        check_exclusive: bool = True,
        check_inclusive: bool = True,
        check_cost: bool = True,
        check_private_groups: bool = True,
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def remove_roles(
        self,
        member: discord.Member,
        roles: List[discord.Role],
        reason: Optional[str] = None,
        *,
        check_inclusive: bool = True,
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def _auto_give(self, member: discord.Member) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def _sticky_leave(self, member: discord.Member) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def _sticky_join(self, member: discord.Member) -> None:
        raise NotImplementedError()

    #######################################################################
    # buttons.py                                                          #
    #######################################################################

    @abstractmethod
    async def initialize_buttons(self):
        raise NotImplementedError()

    @abstractmethod
    async def buttons(self, ctx: commands.Context) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def create_button(
        self,
        ctx: commands.Context,
        name: str,
        role: RoleHierarchyConverter,
        label: Optional[str] = None,
        emoji: Optional[Union[discord.PartialEmoji, str]] = None,
        style: Optional[ButtonStyleConverter] = discord.ButtonStyle.primary,
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def delete_button(self, ctx: commands.Context, *, name: str) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def button_roles_view(self, ctx: commands.Context) -> None:
        raise NotImplementedError()

    #######################################################################
    # select.py                                                           #
    #######################################################################

    @abstractmethod
    async def initialize_select(self) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def select(self, ctx: commands.Context) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def create_select_menu(
        self,
        ctx: commands.Context,
        name: str,
        options: commands.Greedy[SelectOptionRoleConverter],
        min_values: Optional[int] = None,
        max_values: Optional[int] = None,
        *,
        placeholder: Optional[str] = None,
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def delete_select_menu(self, ctx: commands.Context, *, name: str) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def create_select_option(
        self,
        ctx: commands.Context,
        name: str,
        role: RoleHierarchyConverter,
        label: Optional[str] = None,
        description: Optional[str] = None,
        emoji: Optional[Union[discord.PartialEmoji, str]] = None,
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def delete_select_option(self, ctx: commands.Context, *, name: str) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def select_menus_view(self, ctx: commands.Context) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def select_options_view(self, ctx: commands.Context) -> None:
        raise NotImplementedError()

    #######################################################################
    # messages.py                                                         #
    #######################################################################

    @abstractmethod
    async def save_settings(
        self,
        guild: discord.Guild,
        message_key: str,
        *,
        buttons: List[ButtonRole] = [],
        select_menus: List[SelectRole] = [],
    ):
        raise NotImplementedError()

    @abstractmethod
    async def send_message(
        self,
        ctx: Context,
        channel: discord.TextChannel,
        buttons: commands.Greedy[ButtonRoleConverter],
        menus: commands.Greedy[SelectRoleConverter],
        *,
        message: str,
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def check_and_replace_existing(self, guild_id: int, message_key: str):
        raise NotImplementedError()

    @abstractmethod
    async def edit_message(
        self,
        ctx: Context,
        message: discord.Message,
        buttons: commands.Greedy[ButtonRoleConverter],
        menus: commands.Greedy[SelectRoleConverter],
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def send_select(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        menus: commands.Greedy[SelectRoleConverter],
        *,
        message: str,
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def edit_with_select(
        self,
        ctx: commands.Context,
        message: discord.Message,
        menus: commands.Greedy[SelectRoleConverter],
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def send_buttons(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        buttons: commands.Greedy[ButtonRoleConverter],
        *,
        message: str,
    ) -> None:
        raise NotImplementedError()

    @abstractmethod
    async def edit_with_buttons(
        self,
        ctx: commands.Context,
        message: discord.Message,
        buttons: commands.Greedy[ButtonRoleConverter],
    ) -> None:
        raise NotImplementedError()
