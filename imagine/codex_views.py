import discord

from .codex_manager import CodexManagerError


class CodexLinkInstructions(discord.ui.View):
    def __init__(self, url):
        super().__init__(timeout=600)
        self.add_item(discord.ui.Button(
            label="Open ChatGPT authorization", style=discord.ButtonStyle.link, url=url
        ))


class CodexSetupView(discord.ui.View):
    def __init__(self, cog, owner_id):
        super().__init__(timeout=600)
        self.cog = cog
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id or not await self.cog.bot.is_owner(interaction.user):
            await interaction.response.send_message(
                "Only a bot owner can manage the bot's Codex account.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Install / update Codex", emoji="⬇️", style=discord.ButtonStyle.primary)
    async def install(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            version = await self.cog.codex_manager.install()
        except CodexManagerError as exc:
            await interaction.edit_original_response(content=f"**Codex installation failed**\n{exc}")
            return
        await interaction.edit_original_response(
            content=f"**Codex is installed**\n`{version or 'version unavailable'}`\n"
                    "Use **Link ChatGPT** next."
        )

    @discord.ui.button(label="Link ChatGPT", emoji="🔗", style=discord.ButtonStyle.success)
    async def link(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        server = None
        try:
            server, request = await self.cog.codex_manager.begin_device_login()
            await interaction.edit_original_response(
                content=("**Link Codex to ChatGPT**\n"
                         "1. Open the authorization page.\n"
                         f"2. Enter this one-time code: `{request['userCode']}`\n"
                         "3. Complete the ChatGPT authorization.\n\n"
                         "Waiting for ChatGPT…"),
                view=CodexLinkInstructions(request["verificationUrl"]),
            )
            result = await server.wait_for_login(request["loginId"])
            if not result.get("success"):
                raise CodexManagerError(result.get("error") or "ChatGPT did not approve the link.")
            await interaction.edit_original_response(
                content="**Codex connected**\nThe bot can now use this ChatGPT account.", view=None
            )
        except CodexManagerError as exc:
            await interaction.edit_original_response(content=f"**Codex linking failed**\n{exc}", view=None)
        finally:
            if server:
                await server.close()

    @discord.ui.button(label="Connection status", emoji="ℹ️", style=discord.ButtonStyle.secondary)
    async def status(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        version = await self.cog.codex_manager.version()
        if not version:
            await interaction.edit_original_response(content="**Codex status**\nNot installed.")
            return
        try:
            account = await self.cog.codex_manager.account()
        except CodexManagerError as exc:
            await interaction.edit_original_response(
                content=f"**Codex status**\nInstalled: `{version}`\nStatus check failed: {exc}"
            )
            return
        state = "Connected to ChatGPT" if account and account.get("type") == "chatgpt" else "Not connected"
        plan = account.get("planType") if account else None
        await interaction.edit_original_response(
            content=f"**Codex status**\nInstalled: `{version}`\nAccount: **{state}**"
                    + (f"\nPlan: `{plan}`" if plan else "")
        )

    @discord.ui.button(label="Usage", emoji="📊", style=discord.ButtonStyle.secondary, row=1)
    async def usage(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            usage = await self.cog.codex_manager.usage()
            limits = await self.cog.codex_manager.rate_limits()
        except CodexManagerError as exc:
            await interaction.edit_original_response(content=f"**Codex usage unavailable**\n{exc}")
            return

        lines = ["**Codex account usage**"]
        rate_limits = limits.get("rateLimitsByLimitId") or {}
        if not rate_limits and limits.get("rateLimits"):
            item = limits["rateLimits"]
            rate_limits = {item.get("limitId", "codex"): item}
        for limit_id, item in list(rate_limits.items())[:5]:
            label = item.get("limitName") or limit_id
            windows = []
            for window_name in ("primary", "secondary"):
                window = item.get(window_name)
                if not isinstance(window, dict):
                    continue
                used = window.get("usedPercent")
                duration = window.get("windowDurationMins")
                reset = window.get("resetsAt")
                text = f"{used:g}% used" if isinstance(used, (int, float)) else "usage unknown"
                if duration:
                    text += f" / {duration:g}m window"
                if isinstance(reset, (int, float)):
                    text += f" · resets <t:{int(reset)}:R>"
                windows.append(text)
            if windows:
                lines.append(f"**{label}**: " + "; ".join(windows))

        summary = usage.get("summary") or {}
        summary_fields = (
            ("lifetimeTokens", "Lifetime tokens"),
            ("peakDailyTokens", "Peak daily tokens"),
            ("currentStreakDays", "Current streak"),
        )
        for key, label in summary_fields:
            value = summary.get(key)
            if isinstance(value, (int, float)):
                suffix = " days" if key == "currentStreakDays" else ""
                lines.append(f"{label}: `{value:,.0f}{suffix}`")

        buckets = usage.get("dailyUsageBuckets") or []
        recent = [bucket for bucket in buckets[-7:] if bucket.get("startDate") and
                  isinstance(bucket.get("tokens"), (int, float))]
        if recent:
            lines.append("**Recent daily tokens**")
            lines.extend(f"{bucket['startDate']}: `{bucket['tokens']:,.0f}`" for bucket in recent)
        if len(lines) == 1:
            lines.append("The linked account did not return usage or rate-limit details.")
        await interaction.edit_original_response(content="\n".join(lines)[:2000])

    @discord.ui.button(label="Disconnect", emoji="🔒", style=discord.ButtonStyle.danger)
    async def disconnect(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.cog.codex_manager.logout()
        except CodexManagerError as exc:
            await interaction.edit_original_response(content=f"**Disconnect failed**\n{exc}")
            return
        await interaction.edit_original_response(content="**Codex disconnected**")
