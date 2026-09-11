import time

import discord
from redbot.core import commands

from ..core.networks import (
    BASE_SEPOLIA,
    KNOWN_NETWORKS,
    NETWORKS,
    ChainFamily,
    NetworkCapability,
)
from ..providers import WalletProviderError
from ..core.validation import format_atomic_amount
from .constants import WALLET_SUMMARY_COOLDOWN_SECONDS


class WalletCoreCommands:
    """User-facing wallet commands."""

    async def _wallet_read_allowed(
        self, ctx: commands.Context, key: str, cooldown_seconds: int
    ) -> bool:
        """Rate-limit provider reads for ordinary users while exempting administrators."""
        if await self.bot.is_owner(ctx.author):
            return True
        permissions = getattr(ctx.author, "guild_permissions", None)
        if permissions is not None and permissions.administrator:
            return True

        now = time.monotonic()
        cooldown_key = (ctx.author.id, key)
        allowed_at = self.wallet_read_cooldowns.get(cooldown_key, 0.0)
        if now < allowed_at:
            remaining = max(1, int(allowed_at - now + 0.999))
            await ctx.send(
                f"Please wait {remaining} second(s) before requesting that wallet data again."
            )
            return False
        self.wallet_read_cooldowns[cooldown_key] = now + cooldown_seconds
        return True

    async def _wallet_sensitive_allowed(self, ctx: commands.Context) -> bool:
        """Block signing-capable operations while an emergency lock is active."""
        if not await self.config.user(ctx.author).security_locked():
            return True
        await ctx.send(
            "This wallet is emergency-locked. Receiving funds, balances, history, and "
            "authorization revocation remain available, but sends, new authorization, "
            "and signer export are blocked. Contact the bot owner to unlock it."
        )
        return False

    async def _wallet_profile_or_error(self, ctx: commands.Context) -> dict | None:
        return await self._wallet_profile_for_user_or_error(ctx, ctx.author)

    async def _wallet_profile_for_user_or_error(
        self, ctx: commands.Context, user
    ) -> dict | None:
        if await self.config.provider_paused():
            await ctx.send(
                "CryptoWallet provider processing is paused by the bot owner. "
                "Local wallet settings remain available."
            )
            return None
        if getattr(user, "bot", False):
            await ctx.send("Wallet profiles cannot be provisioned for bot accounts.")
            return None
        try:
            return await self.get_or_create_wallet_profile(user)
        except WalletProviderError as exc:
            await ctx.send(f"Wallet provisioning is unavailable: {exc}")
        except RuntimeError as exc:
            await ctx.send(str(exc))
        return None

    async def _wallet_embed(
        self, ctx: commands.Context, profile: dict, user=None, network=None
    ) -> discord.Embed:
        target = user or ctx.author
        display_name = discord.utils.escape_markdown(target.display_name)
        embed = discord.Embed(title="Crypto Wallet", color=discord.Color.green())
        embed.description = f"{display_name}’s public testnet wallet portfolio."
        registry = await self.config.token_registry()
        network_emojis = await self.config.network_emojis()
        networks = [network] if network is not None else [
            item for item in NETWORKS.values() if item.testnet
        ]

        evm_account = next(
            (
                self._account_for_network(profile, item.key)
                for item in networks
                if item.family is ChainFamily.EVM
                and self._account_for_network(profile, item.key) is not None
            ),
            None,
        )
        evm_lines = []
        if evm_account is not None:
            address = str(evm_account.get("address") or "")
            evm_networks = [
                item for item in networks if item.family is ChainFamily.EVM
            ]
            network_badges = " · ".join(
                self._network_compact_label(item, network_emojis)
                for item in evm_networks
            )
            evm_lines.extend((f"Networks: {network_badges}", f"`{address}`"))

        solana_account = next(
            (
                self._account_for_network(profile, item.key)
                for item in networks
                if item.family is ChainFamily.SOLANA
                and self._account_for_network(profile, item.key) is not None
            ),
            None,
        )
        solana_lines = []
        if solana_account is not None:
            solana_lines.append(f"`{str(solana_account.get('address') or '')}`")

        evm_balance_added = False
        for item in networks:
            account = self._account_for_network(profile, item.key)
            if account is None or not item.supports(NetworkCapability.BALANCE):
                continue
            address = str(account.get("address") or "")
            native_balance = None
            try:
                native_balance = await self.wallet_provider.get_native_balance(
                    address, item.key
                )
            except (ValueError, WalletProviderError):
                pass

            try:
                discovered = await self.wallet_provider.get_token_balances(
                    address, item.key
                )
            except (ValueError, WalletProviderError):
                discovered = []
            merged = {
                str(token["contract_address"]).lower(): dict(token, status="indexed")
                for token in discovered
                if int(token.get("amount_atomic", 0) or 0) > 0
            }
            for contract, registered in (registry.get(item.key) or {}).items():
                status = str(registered.get("status") or "community")
                if status not in {"community", "recognized"}:
                    continue
                try:
                    asset = await self.wallet_provider.get_registered_token_asset(
                        address, item.key, contract
                    )
                except WalletProviderError:
                    continue
                if int(asset.get("amount_atomic", 0) or 0) <= 0:
                    continue
                merged[contract.lower()] = {
                    **asset,
                    "symbol": str(registered.get("symbol") or "TOKEN"),
                    "decimals": int(registered.get("decimals", 0)),
                    "status": status,
                }

            tokens = list(merged.values())
            if network is None and not native_balance and not tokens:
                continue
            balance_lines = []
            if native_balance is None:
                balance_lines.append(f"{item.native_symbol}: Temporarily unavailable")
            else:
                balance_lines.append(
                    f"{item.native_symbol}: **{format_atomic_amount(native_balance, item)}**"
                )
            for token_asset in tokens[:6]:
                amount = format_atomic_amount(
                    int(token_asset["amount_atomic"]),
                    item,
                    decimals=int(token_asset["decimals"]),
                )
                contract = str(token_asset["contract_address"])
                short_contract = f"{contract[:8]}…{contract[-6:]}"
                marker = " ✅" if token_asset.get("status") == "recognized" else ""
                balance_lines.append(
                    f"• {token_asset['symbol']}{marker}: **{amount}** "
                    f"([{short_contract}]({item.explorer_url}/token/{contract}))"
                )
            if len(tokens) > 6:
                balance_lines.append(f"• {len(tokens) - 6} more token(s) with a balance")

            balance_url = item.explorer_address_url(address)
            badge = self._network_badge(item, network_emojis)
            section = (
                f"{badge} **[{item.name}]({balance_url})**\n"
                + "\n".join(balance_lines)
            )
            if item.family is ChainFamily.SOLANA:
                solana_lines.extend(("", section))
            else:
                if not evm_balance_added:
                    evm_lines.append("")
                evm_lines.append(section)
                evm_balance_added = True

        if evm_lines:
            embed.add_field(
                name="━━ EVM WALLET ━━",
                value="\n".join(evm_lines)[:1024],
                inline=False,
            )
        if solana_lines:
            solana_network = next(
                item for item in networks if item.family is ChainFamily.SOLANA
            )
            embed.add_field(
                name="━━ SOLANA WALLET ━━",
                value="\n".join(solana_lines)[:1024],
                inline=False,
            )

        if not embed.fields:
            embed.description += " No enabled testnet accounts are available."
        embed.set_footer(
            text="Testnet assets only · Token names may be spoofed; verify contract addresses"
        )
        return embed

    @staticmethod
    def _network_compact_label(network, emoji_ids: dict | None = None) -> str:
        """Use an application emoji alone, or the network name when none is configured."""

        emoji_id = str((emoji_ids or {}).get(network.key) or "")
        if emoji_id.isdigit():
            return WalletCoreCommands._network_badge(network, emoji_ids)
        return network.name

    @staticmethod
    def _network_badge(network, emoji_ids: dict | None = None) -> str:
        emoji_names = {
            "base-sepolia": "base",
            "ethereum-sepolia": "ethereum",
            "arbitrum-sepolia": "arbitrum",
            "polygon-amoy": "polygon",
            "avalanche-fuji": "avalanche",
            "solana-devnet": "solana",
            "optimism": "optimism",
            "bnb": "bnb",
            "zora": "zora",
        }
        emoji_id = str((emoji_ids or {}).get(network.key) or "")
        if emoji_id.isdigit():
            return f"<:{emoji_names.get(network.key, 'chain')}:{emoji_id}>"
        return {
            "base-sepolia": "🔵",
            "ethereum-sepolia": "◆",
            "arbitrum-sepolia": "🔷",
            "polygon-amoy": "🟣",
            "avalanche-fuji": "🔺",
            "solana-devnet": "🟢",
            "optimism": "🔴",
            "bnb": "🟡",
            "zora": "⚪",
        }.get(network.key, "⛓️")

    @commands.group(
        name="wallet", aliases=("wallets", "cryptowallet"), invoke_without_command=True
    )
    async def wallet(self, ctx: commands.Context, member: discord.Member = None):
        """Show your wallet or another member's existing public wallet profile."""
        if not await self._wallet_read_allowed(
            ctx, "summary", WALLET_SUMMARY_COOLDOWN_SECONDS
        ):
            return
        if await self.config.provider_paused():
            await ctx.send(
                "CryptoWallet provider processing is paused by the bot owner. "
                "Local wallet settings remain available."
            )
            return
        target = member or ctx.author
        if target.id == ctx.author.id:
            profile = await self._wallet_profile_or_error(ctx)
            if profile is None:
                return
        else:
            profile = await self.config.user(target).profile()
            if profile is None:
                profile = await self._wallet_profile_for_user_or_error(ctx, target)
                if profile is None:
                    return
        await ctx.send(embed=await self._wallet_embed(ctx, profile, target))

    @wallet.error
    async def wallet_error(self, ctx: commands.Context, error: commands.CommandError):
        """Turn unknown wallet words into useful command guidance."""
        if isinstance(error, commands.MemberNotFound):
            await ctx.send(
                f"I couldn't find that wallet command. To view another member's public "
                f"wallet, mention them after `{ctx.clean_prefix}wallet`."
            )
            await ctx.send_help(ctx.command)
            return
        raise error

    @wallet.command(name="balance", aliases=("funds",))
    async def wallet_balance(self, ctx: commands.Context, network_key: str = None):
        """Show all testnet assets or details for one enabled testnet."""
        if not await self._wallet_read_allowed(
            ctx, "summary", WALLET_SUMMARY_COOLDOWN_SECONDS
        ):
            return
        profile = await self._wallet_profile_or_error(ctx)
        if profile is None:
            return
        network = None
        if network_key is not None:
            network = NETWORKS.get(network_key.strip().lower())
            if network is None or not network.testnet:
                await ctx.send(
                    f"That testnet is unavailable. Use `{ctx.clean_prefix}wallet networks` "
                    "to see enabled networks."
                )
                return
        await ctx.send(embed=await self._wallet_embed(ctx, profile, network=network))

    @wallet.command(name="mode", aliases=("environment",))
    async def wallet_mode(self, ctx: commands.Context, environment: str = None):
        """Show or select the wallet environment; live chains remain disabled."""
        user_config = self.config.user(ctx.author)
        current = await user_config.selected_environment()
        if environment is None:
            await ctx.send(f"Wallet environment: **{current}**")
            return
        requested = environment.strip().lower()
        if requested in {"live", "mainnet"}:
            await ctx.send(
                "Live wallet networks are not enabled in this CryptoWallet prototype yet. This restriction is enforced by the cog, not CDP. "
                "Continue using **testnet** mode until mainnet support is separately reviewed and enabled."
            )
            return
        if requested not in {"testnet", "test"}:
            await ctx.send("Choose `testnet` or `live`. Live chains are not available yet.")
            return
        await user_config.selected_environment.set("testnet")
        await ctx.send("Wallet environment set to **testnet**.")

    @wallet.group(name="token", aliases=("tokens",), invoke_without_command=True)
    async def wallet_token(self, ctx: commands.Context):
        """Add or list tokens shared by this bot installation."""
        await ctx.invoke(self.wallet_token_list)

    @wallet_token.command(name="add")
    async def wallet_token_add(
        self, ctx: commands.Context, network_key: str, contract_address: str
    ):
        """Validate and add an ERC-20 token for every wallet user."""
        if not await self._wallet_read_allowed(ctx, "token_submission", 30):
            return
        network = NETWORKS.get(network_key.strip().lower())
        if (
            network is None
            or network.family is not ChainFamily.EVM
            or not network.testnet
            or not network.supports(NetworkCapability.BALANCE)
        ):
            await ctx.send("Choose an enabled EVM testnet from `!wallet networks`.")
            return
        try:
            from ..core.validation import normalize_evm_address
            contract = normalize_evm_address(contract_address).lower()
        except ValueError:
            await ctx.send("Enter a valid EVM token contract address.")
            return
        registry = await self.config.token_registry()
        existing = (registry.get(network.key) or {}).get(contract)
        if existing is not None:
            state = str(existing.get("status") or "community")
            message = (
                "That token contract is banned from this bot installation."
                if state == "banned"
                else f"That token is already registered as **{state}**."
            )
            await ctx.send(message)
            return
        active = sum(
            entry.get("status") in {"community", "recognized"}
            for entry in (registry.get(network.key) or {}).values()
        )
        if active >= 25:
            await ctx.send("This network already has the maximum 25 visible shared tokens.")
            return
        profile = await self._wallet_profile_or_error(ctx)
        if profile is None:
            return
        account = self._account_for_network(profile, network.key)
        if account is None:
            await ctx.send(f"Your wallet has no account compatible with {network.name}.")
            return
        try:
            asset = await self.wallet_provider.get_registered_token_asset(
                str(account.get("address") or ""), network.key, contract, include_metadata=True
            )
        except WalletProviderError as exc:
            await ctx.send(f"Token validation failed: {exc}")
            return
        entry = {
            "contract_address": contract,
            "symbol": str(asset["symbol"]),
            "name": str(asset["name"]),
            "decimals": int(asset["decimals"]),
            "status": "community",
            "submitted_by": ctx.author.id,
            "submitted_at": int(time.time()),
        }
        async with self.config.token_registry() as stored:
            entries = stored.setdefault(network.key, {})
            if contract in entries:
                await ctx.send("That token was registered while your request was processing.")
                return
            entries[contract] = entry
        await ctx.send(
            f"Added **{entry['symbol']}** as a community token on {network.name}. "
            "It can now appear in every user’s portfolio. Verify the contract address; "
            "token names and symbols can be spoofed."
        )

    @wallet_token.command(name="list", aliases=("tokens",))
    async def wallet_token_list(self, ctx: commands.Context):
        """List visible shared tokens."""
        registry = await self.config.token_registry()
        lines = []
        for network_key, entries in registry.items():
            network = NETWORKS.get(network_key)
            if network is None:
                continue
            for contract, entry in entries.items():
                state = str(entry.get("status") or "community")
                if state not in {"community", "recognized"}:
                    continue
                marker = "✅ recognized" if state == "recognized" else "community"
                lines.append(
                    f"- **{entry.get('symbol', 'TOKEN')}** · {network.name} · {marker}\n  `{contract}`"
                )
        await ctx.send(
            "**Shared wallet tokens**\n" + "\n".join(lines)
            if lines else "No shared tokens are registered yet."
        )

    @wallet_token.command(name="default")
    async def wallet_token_default(
        self, ctx: commands.Context, network_or_action: str = None, asset: str = None
    ):
        """View, set, or reset the asset used by the short wallet send form."""
        user_config = self.config.user(ctx.author)
        if network_or_action is None:
            current = await user_config.default_send_asset()
            if not isinstance(current, dict):
                await ctx.send("Your default send asset follows the server default network native token.")
                return
            network = NETWORKS.get(str(current.get("network") or ""))
            if network is None:
                await ctx.send("Your stored default asset is unavailable. Use `wallet token default reset`.")
                return
            symbol = str(current.get("symbol") or network.native_symbol)
            contract = current.get("contract")
            detail = f" · `{contract}`" if contract else " · native token"
            await ctx.send(f"Default send asset: **{symbol}** on {network.name}{detail}.")
            return
        if network_or_action.strip().lower() in {"reset", "clear"}:
            await user_config.default_send_asset.clear()
            await ctx.send("Your default send asset now follows the server default network native token.")
            return
        network = self._send_network(network_or_action)
        if network is None or not network.testnet or not network.supports(NetworkCapability.SEND):
            await ctx.send("Choose a send-enabled testnet from `wallet networks`.")
            return
        selector = str(asset or "native").strip().lower()
        if selector in {"native", network.native_symbol.lower()}:
            selected = {"network": network.key, "kind": "native", "contract": None,
                        "symbol": network.native_symbol, "decimals": network.native_decimals}
        else:
            if network is not BASE_SEPOLIA:
                await ctx.send("Registered-token sends are only enabled on Base Sepolia.")
                return
            registry = await self.config.token_registry()
            matches = [(contract.lower(), entry)
                       for contract, entry in (registry.get(network.key) or {}).items()
                       if str(entry.get("status") or "") in {"community", "recognized"}
                       and (contract.lower() == selector
                            or str(entry.get("symbol") or "").lower() == selector)]
            if not matches:
                await ctx.send("That token is not enabled in the shared registry.")
                return
            if len(matches) != 1:
                await ctx.send("That symbol is ambiguous. Use the exact contract address.")
                return
            contract, entry = matches[0]
            selected = {"network": network.key, "kind": "erc20", "contract": contract,
                        "symbol": str(entry.get("symbol") or "TOKEN"),
                        "decimals": int(entry.get("decimals", -1))}
        await user_config.default_send_asset.set(selected)
        selected_contract = selected["contract"]
        selected_symbol = selected["symbol"]
        detail = f" (`{selected_contract}`)" if selected_contract else ""
        await ctx.send(
            f"Default send asset set to **{selected_symbol}** on {network.name}{detail}."
        )

    @wallet.command(name="notifications", aliases=("notify",))
    async def wallet_notifications(
        self, ctx: commands.Context, enabled: bool = None
    ):
        """Show or change optional wallet transaction confirmation DMs."""
        if enabled is None:
            enabled = await self.config.user(ctx.author).notifications_enabled()
            state = "enabled" if enabled else "disabled"
            await ctx.send(
                f"Wallet transaction DMs are currently **{state}**. "
                "Automatic incoming-deposit alerts are not implemented yet."
            )
            return
        await self.config.user(ctx.author).notifications_enabled.set(enabled)
        state = "enabled" if enabled else "disabled"
        await ctx.send(
            f"Wallet transaction DMs are now **{state}**. "
            "Transaction cards will continue updating either way. Automatic "
            "incoming-deposit alerts are not implemented yet."
        )

    @wallet.group(name="security", invoke_without_command=True)
    async def wallet_security(self, ctx: commands.Context):
        """Show emergency wallet-lock status and available protections."""
        locked = await self.config.user(ctx.author).security_locked()
        if locked:
            locked_at = int(await self.config.user(ctx.author).security_locked_at() or 0)
            when = f" since <t:{locked_at}:F>" if locked_at else ""
            await ctx.send(
                f"**Wallet security: emergency-locked{when}**\n"
                "New sends, authorization, renewal, and signer export are blocked. "
                "Receiving funds, public wallet data, and authorization revocation remain "
                "available. Only the bot owner can unlock this wallet."
            )
            return
        await ctx.send(
            "**Wallet security: standard**\n"
            f"Use `{ctx.clean_prefix}wallet security lock` if your Discord account or "
            "wallet access may be compromised. The lock takes effect immediately and "
            "only the bot owner can remove it. Optional independent 2FA is not configured yet."
        )

    @wallet_security.command(name="lock", aliases=("freeze",))
    async def wallet_security_lock(self, ctx: commands.Context):
        """Emergency-lock your wallet and revoke current bot signing authorization."""
        user_config = self.config.user(ctx.author)
        if await user_config.security_locked():
            await ctx.send("Your wallet is already emergency-locked.")
            return
        await user_config.security_locked.set(True)
        await user_config.security_locked_at.set(int(time.time()))
        await user_config.security_lock_source.set("user")
        async with user_config.intents() as intents:
            for intent in intents.values():
                if intent.get("status") == "pending":
                    intent["status"] = "rejected"
        profile = await user_config.profile()
        revocation = "No wallet profile or active authorization needed revocation."
        if profile is not None:
            try:
                await self.wallet_provider.revoke_authorization(profile, BASE_SEPOLIA.key)
                revocation = "The current bot signing authorization was revoked."
            except WalletProviderError:
                revocation = (
                    "The lock is active, but CDP revocation could not be confirmed. "
                    "The bot owner should retry revocation."
                )
        await ctx.send(
            "Your wallet is now emergency-locked. " + revocation + " Only the bot owner "
            "can unlock it; receiving funds and read-only wallet commands still work."
        )

    @wallet.command(name="networks")
    async def wallet_networks(self, ctx: commands.Context):
        """List networks enabled for this prototype."""
        lines = []
        for network in NETWORKS.values():
            capabilities = ", ".join(
                capability.value for capability in network.capabilities.enabled()
            )
            lines.append(
                f"- **{network.name}** — {network.reference_label} `{network.reference}` "
                f"({network.family.value.upper()}, {network.native_symbol}, testnet)\n"
                f"  Capabilities: {capabilities}"
            )
        planned = [
            f"- **{network.name}** — {network.reference_label} `{network.reference}` "
            f"({network.native_symbol}, {'testnet' if network.testnet else 'mainnet'}, "
            "unavailable until reviewed)"
            for network in KNOWN_NETWORKS.values()
            if not network.enabled
        ]
        message = "**Enabled wallet networks**\n" + "\n".join(lines)
        if planned:
            message += "\n\n**Planned networks (disabled)**\n" + "\n".join(planned)
        await ctx.send(message)

    @wallet.command(name="network")
    async def wallet_network(self, ctx: commands.Context, network_key: str = None):
        """Show or select the preferred network for network-specific commands."""
        user_config = self.config.user(ctx.author)
        current_key = await user_config.selected_network()
        if network_key is None:
            current = NETWORKS.get(current_key, BASE_SEPOLIA)
            await ctx.send(
                f"Your preferred wallet network is **{current.name}** (`{current.key}`). "
                f"Use `{ctx.clean_prefix}wallet networks` to see available networks."
            )
            return
        network = NETWORKS.get(network_key.strip().lower())
        if network is None or not network.supports(NetworkCapability.BALANCE):
            await ctx.send(
                f"That network is not available for wallet balances. Use "
                f"`{ctx.clean_prefix}wallet networks` to see available networks."
            )
            return
        await user_config.selected_network.set(network.key)
        await ctx.send(
            f"Network-specific wallet commands now use **{network.name}**. "
            "Transaction sending remains unavailable unless that network separately supports it."
        )

    @staticmethod
    def _account_for_network(profile: dict, network_key: str) -> dict | None:
        """Return the wallet account assigned to a configured network."""
        for account in profile.get("accounts") or []:
            if account.get("network") == network_key:
                return account
        network = NETWORKS.get(network_key)
        if network is not None and network.family is ChainFamily.EVM:
            for account in profile.get("accounts") or []:
                account_network = KNOWN_NETWORKS.get(str(account.get("network") or ""))
                if account_network is not None and account_network.family is ChainFamily.EVM:
                    return account
        return None
