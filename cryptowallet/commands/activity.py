from datetime import datetime

import discord
from redbot.core import commands

from ..core.models import TransactionIntent
from ..core.networks import BASE_SEPOLIA, NETWORKS, NetworkCapability
from ..providers import WalletProviderError
from ..providers.base_rpc import BaseRpcError, get_transaction
from ..core.validation import format_wei_as_eth, normalize_evm_address
from .constants import (
    HISTORY_PAGE_SIZE,
    WALLET_HISTORY_COOLDOWN_SECONDS,
    WALLET_RPC_COOLDOWN_SECONDS,
)
from .core import WalletCoreCommands
from .views import WalletHistoryView


class WalletActivityCommands:
    """Public transaction lookup and wallet activity commands."""

    NETWORK_ALIASES = {
        "base": "base-sepolia",
        "base-sepolia": "base-sepolia",
        "eth": "ethereum-sepolia",
        "ethereum": "ethereum-sepolia",
        "ethereum-sepolia": "ethereum-sepolia",
        "arb": "arbitrum-sepolia",
        "arbitrum": "arbitrum-sepolia",
        "arbitrum-sepolia": "arbitrum-sepolia",
        "polygon": "polygon-amoy",
        "pol": "polygon-amoy",
        "polygon-amoy": "polygon-amoy",
        "avax": "avalanche-fuji",
        "avalanche": "avalanche-fuji",
        "avalanche-fuji": "avalanche-fuji",
    }

    @classmethod
    def _activity_network(cls, value: str):
        return NETWORKS.get(cls.NETWORK_ALIASES.get(value.strip().lower(), ""))

    @staticmethod
    def _activity_embed(address: str, page: dict, page_index: int, color, network=BASE_SEPOLIA) -> discord.Embed:
        """Render one CDP-indexed page of public address activity."""
        embed = discord.Embed(title="Your Wallet Activity", color=color)
        normalized_address = address.lower()
        for item in page["transactions"][:HISTORY_PAGE_SIZE]:
            if not isinstance(item, dict):
                continue
            content = item.get("content") or {}
            tx_hash = str(item.get("transaction_hash") or content.get("hash") or "").lower()
            if len(tx_hash) != 66 or not tx_hash.startswith("0x"):
                continue
            try:
                int(tx_hash[2:], 16)
            except ValueError:
                continue

            transfers = []
            traces = content.get("flattened_traces") or []
            if isinstance(traces, list):
                for trace in traces:
                    if not isinstance(trace, dict) or trace.get("error"):
                        continue
                    from_address = str(trace.get("from") or "")
                    to_address = str(trace.get("to") or "")
                    try:
                        raw_value = str(trace.get("value") or "0")
                        value_wei = int(
                            raw_value,
                            16 if raw_value.lower().startswith("0x") else 10,
                        )
                    except ValueError:
                        continue
                    if (
                        value_wei > 0
                        and (
                            from_address.lower() == normalized_address
                            or to_address.lower() == normalized_address
                        )
                    ):
                        transfers.append((from_address, to_address, value_wei))

            from_address = str(content.get("from") or item.get("from_address_id") or "")
            to_address = str(content.get("to") or "")
            try:
                raw_value = str(content.get("value") or "0")
                value_wei = int(
                    raw_value, 16 if raw_value.lower().startswith("0x") else 10
                )
            except ValueError:
                value_wei = 0
            if transfers:
                from_address, to_address, value_wei = transfers[0]

            from_wallet = from_address.lower() == normalized_address
            to_wallet = to_address.lower() == normalized_address
            if from_wallet and to_wallet:
                direction = "🔵 Self transfer"
            elif from_wallet:
                direction = "🔴 Sent"
            elif to_wallet:
                direction = "🟢 Received"
            else:
                direction = "⚪ Contract activity"
            amount = (
                f" · {format_wei_as_eth(value_wei)} {network.native_symbol}"
                if value_wei > 0
                else ""
            )
            details = [
                f"From: `{from_address or 'Unavailable'}`",
                f"To: `{to_address or 'Contract creation'}`",
                f"TXID: [{tx_hash}]({network.explorer_url}/tx/{tx_hash})",
            ]
            timestamp = str(content.get("block_timestamp") or "")
            try:
                created_at = int(
                    datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()
                )
            except (TypeError, ValueError):
                created_at = 0
            if created_at:
                details.append(f"Confirmed <t:{created_at}:R>")
            embed.add_field(
                name=f"{direction}{amount}",
                value="\n".join(details),
                inline=False,
            )
        if not embed.fields:
            embed.description = f"No indexed {network.name} activity was found for this wallet."
        embed.set_footer(text="Latest 10 indexed transactions · Use the explorer for complete history")
        return embed

    @WalletCoreCommands.wallet.command(name="txid")
    async def wallet_txid(
        self, ctx: commands.Context, network_key: str, txid: str
    ):
        """Look up a public transaction on an explicitly selected testnet."""
        network = self._activity_network(network_key)
        if network is None or not network.supports(NetworkCapability.TRANSACTION_LOOKUP):
            await ctx.send(
                f"Choose `base`, `eth`, `arb`, `polygon`, or `avax`, for example: "
                f"`{ctx.clean_prefix}wallet txid base 0x...`"
            )
            return
        lookup = txid.strip().lower()
        if len(lookup) != 66 or not lookup.startswith("0x"):
            await ctx.send("Enter a complete transaction hash beginning with `0x`.")
            return
        try:
            int(lookup[2:], 16)
        except ValueError:
            await ctx.send("Enter a valid hexadecimal transaction hash.")
            return
        if not await self._wallet_read_allowed(
            ctx, "rpc", WALLET_RPC_COOLDOWN_SECONDS
        ):
            return
        try:
            transaction = await get_transaction(lookup, network.key)
        except BaseRpcError as exc:
            await ctx.send(f"{network.name} transaction lookup is unavailable: {exc}")
            return
        if transaction is None:
            await ctx.send(f"No {network.name} transaction was found with that TXID.")
            return
        stored_intent = None
        for data in (await self.expire_and_trim_intents(ctx.author)).values():
            if (
                str(data.get("transaction_hash") or "").lower() != lookup
                or str(data.get("network") or "") != network.key
            ):
                continue
            try:
                stored_intent = TransactionIntent.from_dict(data)
            except (KeyError, TypeError, ValueError):
                pass
            break
        wallet_transfers = transaction["wallet_transfers"]
        if stored_intent is not None:
            wallet_transfers = [
                {
                    "from_address": stored_intent.from_address,
                    "to_address": stored_intent.to_address,
                    "value_wei": stored_intent.value_wei,
                }
            ]
        success = transaction["success"]
        status = "Pending" if success is None else "Confirmed" if success else "Failed"
        color = (
            discord.Color.gold() if success is None
            else discord.Color.green() if success
            else discord.Color.red()
        )
        embed = discord.Embed(title=f"{status} {network.name} transaction", color=color)
        if (
            str(transaction["to_address"] or "").lower()
            == "0x5ff137d4b0fdcd49dca30c7cf57e578a026d2789"
        ):
            embed.description = (
                "This smart-account transfer appears under internal transactions "
                "in the explorer."
            )
        embed.add_field(name="Status", value=status, inline=True)
        embed.add_field(
            name="Network",
            value=f"{network.name} (`{network.chain_id}`)",
            inline=True,
        )
        if transaction["block_number"] is not None:
            embed.add_field(
                name="Block", value=f"`{transaction['block_number']}`", inline=True
            )
        embed.add_field(
            name="TXID",
            value=f"[{lookup}]({network.explorer_url}/tx/{lookup})",
            inline=False,
        )
        if wallet_transfers:
            for index, transfer in enumerate(wallet_transfers[:5], start=1):
                suffix = "" if len(wallet_transfers) == 1 else f" {index}"
                embed.add_field(
                    name=f"From{suffix}",
                    value=f"`{transfer['from_address']}`",
                    inline=False,
                )
                embed.add_field(
                    name=f"To{suffix}",
                    value=f"`{transfer['to_address']}`",
                    inline=False,
                )
                embed.add_field(
                    name=f"Value{suffix}",
                    value=(
                        f"{format_wei_as_eth(transfer['value_wei'])} "
                        f"{network.native_symbol}"
                    ),
                    inline=True,
                )
        elif wallet_transfers is None:
            embed.add_field(
                name="Transfer details",
                value="Temporarily unavailable; use the explorer link above.",
                inline=False,
            )
        else:
            embed.add_field(
                name="From",
                value=f"`{transaction['from_address']}`",
                inline=False,
            )
            embed.add_field(
                name="To",
                value=f"`{transaction['to_address'] or 'Contract creation'}`",
                inline=False,
            )
            embed.add_field(
                name="Value",
                value=(
                    f"{format_wei_as_eth(transaction['value_wei'])} "
                    f"{network.native_symbol}"
                ),
                inline=True,
            )
        embed.set_footer(text="Public on-chain transaction data")
        await ctx.send(embed=embed)

    @WalletCoreCommands.wallet.command(name="transactions", aliases=("tx", "trans", "history"))
    async def wallet_transactions(self, ctx: commands.Context, network_key: str = None):
        """Browse your wallet's indexed incoming and outgoing blockchain activity."""
        profile = await self._wallet_profile_or_error(ctx)
        if profile is None:
            return
        if network_key is None:
            embed = discord.Embed(
                title="Recent Wallet Transactions",
                description=(
                    "Choose a network below to view this wallet directly in its public "
                    f"block explorer. Use `{ctx.clean_prefix}wallet transactions base` or "
                    f"`{ctx.clean_prefix}wallet transactions eth` to load 10 indexed transactions in Discord."
                ),
                color=discord.Color.blurple(),
            )
            for network in NETWORKS.values():
                if not network.testnet:
                    continue
                account = self._account_for_network(profile, network.key)
                if account is None:
                    continue
                address = str(account.get("address") or "")
                embed.add_field(
                    name=network.name,
                    value=f"[View complete activity]({network.explorer_url}/address/{address})",
                    inline=False,
                )
            embed.set_footer(text="Explorer links do not use CDP history requests")
            await ctx.send(embed=embed)
            return
        network = self._activity_network(network_key)
        if network is None or not network.supports(NetworkCapability.HISTORY):
            await ctx.send("Choose `base`, `eth`, `arb`, `polygon`, or `avax` for indexed transaction history.")
            return
        if not await self._wallet_read_allowed(
            ctx, "history", WALLET_HISTORY_COOLDOWN_SECONDS
        ):
            return
        account = self._account_for_network(profile, network.key)
        if account is None:
            await ctx.send(f"Your wallet profile has no account for {network.name}.")
            return
        try:
            address = normalize_evm_address(str(account.get("address") or ""))
            page = await self.wallet_provider.get_transaction_history(
                address,
                network.key,
                limit=HISTORY_PAGE_SIZE,
            )
        except (ValueError, WalletProviderError) as exc:
            await ctx.send(f"Wallet activity is unavailable: {exc}")
            return
        view = WalletHistoryView(
            self,
            ctx.author.id,
            address,
            page,
            discord.Color.blurple(),
            network,
        )
        await ctx.send(embed=view.embed(), view=view)
