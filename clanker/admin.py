import datetime
import io
import json
from typing import Optional

import discord
from redbot.core import checks, commands

from .constants import (
    DEFAULT_CLANKER_SUPPLY,
    LEGACY_LIVE_SUBMISSION_AVAILABLE,
    MERKLE_ROOT_RE,
    MIN_AIRDROP_LOCKUP_SECONDS,
)
from .helpers import (
    build_airdrop_merkle_tree,
    format_tokens,
    is_eth_address,
    parse_airdrop_lines,
    validate_airdrop_total,
)


class ClankerAdminMixin:
    @commands.guild_only()
    @commands.group(name="clankerset")
    @checks.is_owner()
    async def clankerset(self, ctx: commands.Context):
        """Owner-only Clanker configuration."""
        pass

    @clankerset.command(name="view")
    async def clankerset_view(self, ctx: commands.Context):
        """Show Clanker configuration without secrets."""
        await self.clanker_status(ctx)

    @clankerset.command(name="enabled")
    async def clankerset_enabled(self, ctx: commands.Context, enabled: bool):
        """Enable or disable Clanker launch requests."""
        await self.config.guild(ctx.guild).enabled.set(enabled)
        await ctx.send(f"Clanker launch requests are now {'enabled' if enabled else 'disabled'}.")

    @clankerset.command(name="submit")
    async def clankerset_submit(self, ctx: commands.Context, enabled: bool):
        """Keep legacy REST submission disabled during CryptoWallet migration."""
        if enabled and not LEGACY_LIVE_SUBMISSION_AVAILABLE:
            await self.config.guild(ctx.guild).submit_enabled.set(False)
            await ctx.send(
                "Legacy Clanker REST submission is permanently disabled. Launches remain "
                "unsigned Base Sepolia drafts until the protected CryptoWallet deployment "
                "intent passes security review."
            )
            return
        await self.config.guild(ctx.guild).submit_enabled.set(False)
        await ctx.send("Clanker launch mode set to dry-run/review only.")

    @clankerset.command(name="apiurl")
    async def clankerset_apiurl(self, ctx: commands.Context, api_base_url: str):
        """Set the Clanker API base URL."""
        api_base_url = api_base_url.strip().rstrip("/")
        if not self.validate_https_url(api_base_url):
            await ctx.send("API URL must be HTTPS.")
            return
        await self.config.guild(ctx.guild).api_base_url.set(api_base_url)
        warning = ""
        if api_base_url.rstrip("/") == "https://www.clanker.world/api":
            warning = (
                " Note: the public Clanker `/api/tokens` endpoint is read-only; "
                "live submit needs a verified partner deployment API base URL."
            )
        await ctx.send(f"Clanker API URL saved.{warning}")

    @clankerset.command(name="apipath")
    async def clankerset_apipath(self, ctx: commands.Context, *, api_submit_path: str):
        """Set the relative live-submit path under the configured Clanker API URL."""
        api_submit_path = api_submit_path.strip().lstrip("/")
        if not api_submit_path or api_submit_path.startswith(("http://", "https://")) or ".." in api_submit_path:
            await ctx.send("API submit path must be a relative path such as `tokens` or `deployments/clanker`.")
            return
        await self.config.guild(ctx.guild).api_submit_path.set(api_submit_path)
        await ctx.send(f"Clanker API submit path saved as `{api_submit_path}`.")

    @clankerset.command(name="apitoken")
    async def clankerset_apitoken(self, ctx: commands.Context, *, api_token: str):
        """Set the Clanker API bearer token in shared API token storage."""
        await self.bot.set_shared_api_tokens("clanker", api_token=api_token.strip())
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            pass
        await ctx.send("Clanker API token saved.")

    @clankerset.command(name="treasury")
    async def clankerset_treasury(self, ctx: commands.Context, treasury_address: str):
        """Set the bot-owner/SickGaming platform treasury EVM address."""
        treasury_address = treasury_address.strip()
        if not is_eth_address(treasury_address):
            await ctx.send("Treasury address must be a valid EVM address.")
            return
        await self.config.guild(ctx.guild).treasury_address.set(treasury_address)
        await ctx.send("SickGaming treasury address saved.")

    @clankerset.command(name="platformbps")
    async def clankerset_platformbps(self, ctx: commands.Context, basis_points: commands.Range[int, 0, 10000]):
        """Set the bot-owner platform reward basis points for launch requests."""
        await self.config.guild(ctx.guild).platform_bps.set(basis_points)
        await ctx.send(f"Bot-owner platform split set to {basis_points} bps.")

    @clankerset.command(name="channel")
    async def clankerset_channel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """Restrict Clanker launch creation to one channel; omit to use this channel."""
        channel = channel or ctx.channel
        await self.config.guild(ctx.guild).launch_channel_id.set(channel.id)
        await ctx.send(f"Clanker launches must now be started in {channel.mention}.")

    @clankerset.command(name="clearchannel")
    async def clankerset_clearchannel(self, ctx: commands.Context):
        """Allow Clanker launch creation in any channel."""
        await self.config.guild(ctx.guild).launch_channel_id.set(None)
        await ctx.send("Clanker launch channel restriction cleared.")

    @clankerset.command(name="approvalchannel")
    async def clankerset_approvalchannel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """Set the channel that receives Clanker launch record summaries; omit to use this channel."""
        channel = channel or ctx.channel
        await self.config.guild(ctx.guild).approval_channel_id.set(channel.id)
        await ctx.send(f"Clanker launch records will be posted to {channel.mention}.")

    @clankerset.command(name="clearapprovalchannel")
    async def clankerset_clearapprovalchannel(self, ctx: commands.Context):
        """Stop posting Clanker launch records to a configured channel."""
        await self.config.guild(ctx.guild).approval_channel_id.set(None)
        await ctx.send("Clanker approval/log channel cleared.")

    @clankerset.command(name="requireapproval")
    async def clankerset_requireapproval(self, ctx: commands.Context, enabled: bool):
        """Require owner approval before live Clanker submissions."""
        await self.config.guild(ctx.guild).approval_required.set(enabled)
        mode = "require owner approval before live submission" if enabled else "allow direct live submission after confirmation"
        await ctx.send(f"Clanker approval mode set to {mode}.")

    @clankerset.command(name="allowedrole")
    async def clankerset_allowedrole(self, ctx: commands.Context, role: discord.Role):
        """Require a role before users can create Clanker launch requests."""
        await self.config.guild(ctx.guild).allowed_role_id.set(role.id)
        await ctx.send(f"Only members with {role.mention} can create Clanker launch requests.")

    @clankerset.command(name="clearallowedrole")
    async def clankerset_clearallowedrole(self, ctx: commands.Context):
        """Clear the required role for Clanker launch requests."""
        await self.config.guild(ctx.guild).allowed_role_id.set(None)
        await ctx.send("Clanker allowed-role requirement cleared.")

    @clankerset.command(name="blockedrole")
    async def clankerset_blockedrole(self, ctx: commands.Context, role: discord.Role):
        """Block a role from creating Clanker launch requests."""
        await self.config.guild(ctx.guild).blocked_role_id.set(role.id)
        await ctx.send(f"Members with {role.mention} cannot create Clanker launch requests.")

    @clankerset.command(name="clearblockedrole")
    async def clankerset_clearblockedrole(self, ctx: commands.Context):
        """Clear the blocked role for Clanker launch requests."""
        await self.config.guild(ctx.guild).blocked_role_id.set(None)
        await ctx.send("Clanker blocked-role restriction cleared.")

    @clankerset.command(name="cooldown")
    async def clankerset_cooldown(self, ctx: commands.Context, seconds: int):
        """Set the per-user Clanker launch cooldown in seconds; 0 disables it."""
        if seconds < 0 or seconds > 86400:
            await ctx.send("Clanker launch cooldown must be between 0 and 86400 seconds.")
            return
        await self.config.guild(ctx.guild).launch_cooldown_seconds.set(seconds)
        await ctx.send(f"Clanker launch cooldown set to {seconds} seconds.")

    @clankerset.command(name="dailymax")
    async def clankerset_dailymax(self, ctx: commands.Context, launches: int):
        """Set the per-user 24-hour launch limit; 0 disables it."""
        if launches < 0 or launches > 100:
            await ctx.send("Clanker daily launch limit must be between 0 and 100 launches per user.")
            return
        await self.config.guild(ctx.guild).daily_max_per_user.set(launches)
        await ctx.send("Clanker daily launch limit disabled." if launches == 0 else f"Clanker daily launch limit set to {launches} per user.")

    @clankerset.group(name="airdrop")
    async def clankerset_airdrop(self, ctx: commands.Context):
        """Manage optional airdrop defaults for launch requests."""
        pass

    @clankerset_airdrop.command(name="enabled")
    async def clankerset_airdrop_enabled(self, ctx: commands.Context, enabled: bool):
        """Enable or disable a configured Clanker airdrop."""
        await self.config.guild(ctx.guild).airdrop_enabled.set(enabled)
        await ctx.send(f"Airdrop defaults are now {'enabled' if enabled else 'disabled'}.")

    @clankerset_airdrop.command(name="root")
    async def clankerset_airdrop_root(self, ctx: commands.Context, merkle_root: str):
        """Set the Merkle root for a prepared airdrop recipient list."""
        merkle_root = merkle_root.strip()
        if not MERKLE_ROOT_RE.fullmatch(merkle_root):
            await ctx.send("Merkle root must be a 32-byte hex string, like 0x plus 64 hex characters.")
            return
        await self.config.guild(ctx.guild).airdrop_merkle_root.set(merkle_root)
        await self.config.guild(ctx.guild).airdrop_proof_export.set(None)
        await ctx.send("Airdrop Merkle root saved. Any previously generated proof export was cleared because this root was set manually.")

    @clankerset_airdrop.command(name="build")
    async def clankerset_airdrop_build(self, ctx: commands.Context, supply: int, *, recipient_rows: str):
        """Build and store an airdrop Merkle root/proof export from recipient rows."""
        if supply <= 0 or supply > 10**18:
            await ctx.send("Supply must be between 1 and 1e18 tokens.")
            return
        try:
            recipients, total_amount = parse_airdrop_lines(recipient_rows, supply)
            if not recipients:
                raise ValueError("At least one recipient row is required.")
            validate_airdrop_total(total_amount, supply)
            export = build_airdrop_merkle_tree(recipients)
        except (ValueError, RuntimeError) as exc:
            await ctx.send(str(exc))
            return
        await self.config.guild(ctx.guild).airdrop_merkle_root.set(export["root"])
        await self.config.guild(ctx.guild).airdrop_amount.set(export["total_amount"])
        await self.config.guild(ctx.guild).airdrop_proof_export.set(export)
        await self.config.guild(ctx.guild).airdrop_enabled.set(True)
        content = json.dumps(export, indent=2, sort_keys=True).encode("utf-8")
        filename = f"clanker-airdrop-{ctx.guild.id}-{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M%S')}.json"
        await ctx.send(
            f"Airdrop Merkle root generated and defaults enabled: `{export['root']}` ({export['recipient_count']} recipients, {format_tokens(export['total_amount'])} tokens).",
            file=discord.File(io.BytesIO(content), filename=filename),
        )

    @clankerset_airdrop.command(name="export")
    async def clankerset_airdrop_export(self, ctx: commands.Context):
        """Export the currently stored configured airdrop proof metadata."""
        export = await self.config.guild(ctx.guild).airdrop_proof_export()
        if not export:
            await ctx.send("No generated airdrop proof export is stored for this server.")
            return
        content = json.dumps(export, indent=2, sort_keys=True).encode("utf-8")
        await ctx.send(
            "Configured airdrop proof export.",
            file=discord.File(io.BytesIO(content), filename=f"clanker-airdrop-config-{ctx.guild.id}.json"),
        )

    @clankerset_airdrop.command(name="amount")
    async def clankerset_airdrop_amount(self, ctx: commands.Context, amount: commands.Range[int, 0, 10**18]):
        """Set total token amount reserved for the configured airdrop."""
        if amount:
            try:
                validate_airdrop_total(amount, DEFAULT_CLANKER_SUPPLY)
            except ValueError as exc:
                await ctx.send(str(exc))
                return
        await self.config.guild(ctx.guild).airdrop_amount.set(amount)
        await self.config.guild(ctx.guild).airdrop_proof_export.set(None)
        await ctx.send(f"Airdrop amount set to {amount} tokens. Any generated proof export was cleared because the amount was set manually.")

    @clankerset_airdrop.command(name="lockup")
    async def clankerset_airdrop_lockup(self, ctx: commands.Context, seconds: int):
        """Set airdrop lockup in seconds; Clanker SDK minimum is 1 day."""
        if seconds < MIN_AIRDROP_LOCKUP_SECONDS or seconds > 315360000:
            await ctx.send("Airdrop lockup must be between 86400 and 315360000 seconds.")
            return
        await self.config.guild(ctx.guild).airdrop_lockup_seconds.set(seconds)
        await ctx.send(f"Airdrop lockup set to {seconds} seconds.")

    @clankerset_airdrop.command(name="vesting")
    async def clankerset_airdrop_vesting(self, ctx: commands.Context, seconds: commands.Range[int, 0, 315360000]):
        """Set optional airdrop vesting duration in seconds; 0 disables vesting."""
        await self.config.guild(ctx.guild).airdrop_vesting_seconds.set(seconds)
        await ctx.send(f"Airdrop vesting set to {seconds} seconds.")

    @clankerset_airdrop.command(name="admin")
    async def clankerset_airdrop_admin(self, ctx: commands.Context, admin_address: Optional[str] = None):
        """Set an optional airdrop admin address; omit to clear it."""
        if not admin_address:
            await self.config.guild(ctx.guild).airdrop_admin.set(None)
            await ctx.send("Airdrop admin cleared; Clanker will use its default.")
            return
        admin_address = admin_address.strip()
        if not is_eth_address(admin_address):
            await ctx.send("Airdrop admin must be a valid EVM address.")
            return
        await self.config.guild(ctx.guild).airdrop_admin.set(admin_address)
        await ctx.send("Airdrop admin saved.")


    @clankerset.group(name="audit")
    async def clankerset_audit(self, ctx: commands.Context):
        """Manage Clanker audit records."""
        pass

    @clankerset_audit.command(name="clear")
    async def clankerset_audit_clear(self, ctx: commands.Context):
        """Clear Clanker audit records for this guild."""
        await self.config.guild(ctx.guild).audit_log.set([])
        await ctx.send("Clanker audit log cleared.")
