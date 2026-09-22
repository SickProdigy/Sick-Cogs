import datetime
import io
import json
from typing import Optional

import discord
from redbot.core import checks, commands

from .constants import (
    DEFAULT_CLANKER_SUPPLY,
    MAX_EXTENSION_PERCENTAGE,
    MIN_VAULT_LOCKUP_SECONDS,
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


def validate_extension_allocations(vault_percentage: int, airdrop_amount: int) -> None:
    airdrop_bps = (airdrop_amount * 10_000 + DEFAULT_CLANKER_SUPPLY - 1) // DEFAULT_CLANKER_SUPPLY
    if vault_percentage * 100 + airdrop_bps > MAX_EXTENSION_PERCENTAGE * 100:
        raise ValueError("Clanker vault and airdrop allocations cannot exceed 90% of supply.")


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

    @clankerset.command(name="treasury")
    async def clankerset_treasury(self, ctx: commands.Context, treasury_address: str):
        """Set the bot-owner/SickGaming platform treasury EVM address."""
        treasury_address = treasury_address.strip()
        if not is_eth_address(treasury_address):
            await ctx.send("Treasury address must be a valid EVM address.")
            return
        await self.config.treasury_address.set(treasury_address)
        await ctx.send("SickGaming treasury address saved.")

    @clankerset.command(name="platformbps")
    async def clankerset_platformbps(self, ctx: commands.Context, basis_points: commands.Range[int, 0, 10000]):
        """Set the bot-owner platform reward basis points for launch requests."""
        await self.config.platform_bps.set(basis_points)
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

    @clankerset.command(name="pause")
    async def clankerset_pause(self, ctx: commands.Context, paused: bool):
        """Pause or resume all new Clanker launch creation."""
        await self.config.emergency_paused.set(paused)
        await self.record_owner_action(ctx.author.id, "pause" if paused else "resume")
        await ctx.send("Clanker launch creation is now {}.".format("paused" if paused else "available"))

    @clankerset.command(name="maxoutstanding")
    async def clankerset_maxoutstanding(self, ctx: commands.Context, maximum: commands.Range[int, 1, 100]):
        """Set the deployment-wide unfinished-request cap per user."""
        await self.config.max_outstanding_per_user.set(maximum)
        await self.record_owner_action(ctx.author.id, "max_outstanding", maximum)
        await ctx.send("Users may keep up to {} unfinished Clanker requests.".format(maximum))

    @clankerset.command(name="blockuser")
    async def clankerset_blockuser(self, ctx: commands.Context, user_id: int):
        """Block one Discord user from creating new launches."""
        if user_id <= 0 or user_id == int(ctx.author.id):
            await ctx.send("Enter a valid Discord user ID other than your own.")
            return
        values = {int(value) for value in await self.config.blocked_user_ids()}
        values.add(user_id)
        await self.config.blocked_user_ids.set(sorted(values))
        await self.record_owner_action(ctx.author.id, "block_user", user_id)
        await ctx.send("Blocked user `{}` from new Clanker launches.".format(user_id))

    @clankerset.command(name="unblockuser")
    async def clankerset_unblockuser(self, ctx: commands.Context, user_id: int):
        """Remove a Clanker launch block from one Discord user."""
        values = {int(value) for value in await self.config.blocked_user_ids()}
        values.discard(user_id)
        await self.config.blocked_user_ids.set(sorted(values))
        await self.record_owner_action(ctx.author.id, "unblock_user", user_id)
        await ctx.send("Removed the Clanker block for user `{}`.".format(user_id))

    @clankerset.command(name="blockguild")
    async def clankerset_blockguild(self, ctx: commands.Context, guild_id: int):
        """Block one Discord server as a source of new launches."""
        if guild_id <= 0:
            await ctx.send("Enter a valid Discord server ID.")
            return
        values = {int(value) for value in await self.config.blocked_guild_ids()}
        values.add(guild_id)
        await self.config.blocked_guild_ids.set(sorted(values))
        await self.record_owner_action(ctx.author.id, "block_guild", guild_id)
        await ctx.send("Blocked server `{}` as a source of new Clanker launches.".format(guild_id))

    @clankerset.command(name="unblockguild")
    async def clankerset_unblockguild(self, ctx: commands.Context, guild_id: int):
        """Remove a Clanker source block from one Discord server."""
        values = {int(value) for value in await self.config.blocked_guild_ids()}
        values.discard(guild_id)
        await self.config.blocked_guild_ids.set(sorted(values))
        await self.record_owner_action(ctx.author.id, "unblock_guild", guild_id)
        await ctx.send("Removed the Clanker source block for server `{}`.".format(guild_id))

    @clankerset.command(name="blocks")
    async def clankerset_blocks(self, ctx: commands.Context):
        """Show current deployment-wide Clanker safety controls."""
        users = await self.config.blocked_user_ids()
        guilds = await self.config.blocked_guild_ids()
        embed = discord.Embed(title="Clanker operational controls", color=discord.Color.orange())
        embed.add_field(name="Emergency pause", value="On" if await self.config.emergency_paused() else "Off")
        embed.add_field(name="Outstanding cap", value=str(await self.config.max_outstanding_per_user()))
        def bounded_ids(values):
            shown = ["`{}`".format(value) for value in values[:25]]
            if len(values) > 25:
                shown.append("… and {} more".format(len(values) - 25))
            return ", ".join(shown) or "None"

        embed.add_field(name="Blocked users", value=bounded_ids(users), inline=False)
        embed.add_field(name="Blocked servers", value=bounded_ids(guilds), inline=False)
        await ctx.send(embed=embed)

    @clankerset.command(name="analytics", aliases=("metrics",))
    async def clankerset_analytics(self, ctx: commands.Context, days: commands.Range[int, 1, 365] = 30):
        """Show private deployment-wide launch activity and source counts."""
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        records = []
        for record in await self.all_launch_records():
            try:
                created = datetime.datetime.fromisoformat(str(record.get("created_at") or "").replace("Z", "+00:00"))
            except ValueError:
                continue
            if created >= cutoff:
                records.append(record)
        statuses = {}
        sources = {}
        users = set()
        for record in records:
            status = str(record.get("status") or "unknown")
            statuses[status] = statuses.get(status, 0) + 1
            source = int(record.get("origin_guild_id", 0) or 0)
            sources[source] = sources.get(source, 0) + 1
            requester = int(record.get("requester_id", 0) or 0)
            if requester:
                users.add(requester)
        embed = discord.Embed(title="Clanker owner analytics", description="Private operational totals for the last {} day{}.".format(days, "" if days == 1 else "s"), color=discord.Color.blue())
        embed.add_field(name="Records", value=str(len(records)))
        embed.add_field(name="Unique requesters", value=str(len(users)))
        embed.add_field(name="Statuses", value="\n".join("{}: {}".format(key.replace("_", " ").title(), value) for key, value in sorted(statuses.items())) or "None", inline=False)
        top_sources = sorted(sources.items(), key=lambda item: (-item[1], item[0]))[:10]
        source_lines = []
        for guild_id, count in top_sources:
            guild = self.bot.get_guild(guild_id) if guild_id else None
            label = guild.name if guild is not None else ("DM or unknown" if not guild_id else str(guild_id))
            source_lines.append("{} (`{}`): {}".format(label, guild_id, count))
        embed.add_field(name="Top sources", value="\n".join(source_lines) or "None", inline=False)
        await ctx.send(embed=embed)

    @clankerset.command(name="owneraudit")
    async def clankerset_owneraudit(self, ctx: commands.Context, limit: commands.Range[int, 1, 25] = 10):
        """Show recent reversible owner control changes."""
        entries = await self.config.owner_audit_log()
        lines = ["{} · {}{}".format(entry.get("created_at", "unknown"), str(entry.get("action") or "unknown").replace("_", " "), " · {}".format(entry["target"]) if entry.get("target") is not None else "") for entry in entries[-limit:]]
        embed = discord.Embed(title="Clanker owner-control audit", description="\n".join(lines) or "No owner control changes recorded.", color=discord.Color.blue())
        await ctx.send(embed=embed)

    @clankerset.group(name="vault")
    async def clankerset_vault(self, ctx: commands.Context):
        """Manage optional token vault defaults."""
        pass

    @clankerset_vault.command(name="enabled")
    async def clankerset_vault_enabled(self, ctx: commands.Context, enabled: bool):
        """Enable or disable the configured token vault."""
        if enabled:
            settings = await self.config.guild(ctx.guild).all()
            percentage = int(settings.get("vault_percentage") or 0)
            if percentage < 1:
                await ctx.send("Set a vault percentage from 1 through 90 before enabling it.")
                return
            try:
                validate_extension_allocations(
                    percentage, int(settings.get("airdrop_amount") or 0)
                    if settings.get("airdrop_enabled") else 0,
                )
            except ValueError as exc:
                await ctx.send(str(exc))
                return
        await self.config.guild(ctx.guild).vault_enabled.set(enabled)
        state = "enabled" if enabled else "disabled"
        await ctx.send(f"Vault defaults are now {state}.")

    @clankerset_vault.command(name="percentage")
    async def clankerset_vault_percentage(self, ctx: commands.Context, percentage: commands.Range[int, 1, 90]):
        """Set the whole-token-supply percentage allocated to the vault."""
        settings = await self.config.guild(ctx.guild).all()
        try:
            validate_extension_allocations(
                percentage, int(settings.get("airdrop_amount") or 0)
                if settings.get("airdrop_enabled") else 0,
            )
        except ValueError as exc:
            await ctx.send(str(exc))
            return
        await self.config.guild(ctx.guild).vault_percentage.set(percentage)
        await ctx.send(f"Vault allocation set to {percentage}% of the fixed supply.")

    @clankerset_vault.command(name="lockup")
    async def clankerset_vault_lockup(self, ctx: commands.Context, seconds: int):
        """Set vault lockup seconds; Clanker v4 requires at least seven days."""
        if seconds < MIN_VAULT_LOCKUP_SECONDS or seconds > 315360000:
            await ctx.send("Vault lockup must be between 604800 and 315360000 seconds.")
            return
        await self.config.guild(ctx.guild).vault_lockup_seconds.set(seconds)
        await ctx.send(f"Vault lockup set to {seconds} seconds.")

    @clankerset_vault.command(name="vesting")
    async def clankerset_vault_vesting(self, ctx: commands.Context, seconds: commands.Range[int, 0, 315360000]):
        """Set optional vault vesting seconds; zero disables vesting."""
        await self.config.guild(ctx.guild).vault_vesting_seconds.set(seconds)
        await ctx.send(f"Vault vesting set to {seconds} seconds.")

    @clankerset_vault.command(name="recipient")
    async def clankerset_vault_recipient(self, ctx: commands.Context, recipient: Optional[str] = None):
        """Set the vault recipient; omit it to default to the token admin."""
        if not recipient:
            await self.config.guild(ctx.guild).vault_recipient.set(None)
            await ctx.send("Vault recipient cleared; launches will use the token admin.")
            return
        recipient = recipient.strip()
        if not is_eth_address(recipient):
            await ctx.send("Vault recipient must be a valid EVM address.")
            return
        await self.config.guild(ctx.guild).vault_recipient.set(recipient)
        await ctx.send("Vault recipient saved.")

    @clankerset.group(name="airdrop")
    async def clankerset_airdrop(self, ctx: commands.Context):
        """Manage optional airdrop defaults for launch requests."""
        pass

    @clankerset_airdrop.command(name="enabled")
    async def clankerset_airdrop_enabled(self, ctx: commands.Context, enabled: bool):
        """Enable or disable a configured Clanker airdrop."""
        if enabled:
            settings = await self.config.guild(ctx.guild).all()
            try:
                validate_extension_allocations(
                    int(settings.get("vault_percentage") or 0)
                    if settings.get("vault_enabled") else 0,
                    int(settings.get("airdrop_amount") or 0),
                )
            except ValueError as exc:
                await ctx.send(str(exc))
                return
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
    async def clankerset_airdrop_build(self, ctx: commands.Context, *, recipient_rows: str):
        """Build and store an airdrop Merkle root/proof export from recipient rows."""
        try:
            recipients, total_amount = parse_airdrop_lines(recipient_rows, DEFAULT_CLANKER_SUPPLY)
            if not recipients:
                raise ValueError("At least one recipient row is required.")
            validate_airdrop_total(total_amount, DEFAULT_CLANKER_SUPPLY)
            settings = await self.config.guild(ctx.guild).all()
            validate_extension_allocations(
                int(settings.get("vault_percentage") or 0)
                if settings.get("vault_enabled") else 0, total_amount,
            )
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
                settings = await self.config.guild(ctx.guild).all()
                validate_extension_allocations(
                    int(settings.get("vault_percentage") or 0)
                    if settings.get("vault_enabled") else 0, amount,
                )
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
