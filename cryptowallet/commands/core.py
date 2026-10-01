import logging
import secrets
import time
from urllib.parse import quote

import discord
from redbot.core import commands

from ..core.environment import WalletEnvironment, parse_wallet_environment
from ..core.networks import (
    BASE_MAINNET,
    BASE_SEPOLIA,
    ETHEREUM_SEPOLIA,
    KNOWN_NETWORKS,
    NETWORKS,
    ChainFamily,
    NetworkCapability,
    resolve_network,
)
from ..providers import WalletProviderError
from ..backend.terms import (
    CRYPTOWALLET_MAINNET_TERMS_VERSION,
    CRYPTOWALLET_TERMS_PRODUCT,
)
from ..core.validation import format_atomic_amount
from .constants import WALLET_SUMMARY_COOLDOWN_SECONDS
from .views import (
    WalletEmergencyLockView,
    WalletTermsAcceptanceView,
    WalletTermsReferenceView,
    WalletTotpEnrollmentView,
    WalletTotpManagementView,
)


log = logging.getLogger("red.Sick-Cogs.CryptoWallet")


async def testnet_path_allowed(
    cog, ctx: commands.Context, *, explicit: bool = False
) -> bool:
    """Fail closed for testnet routes outside the installation's operating mode."""
    setting = getattr(getattr(cog, "config", None), "operating_mode", None)
    raw = await setting() if setting is not None else WalletEnvironment.TESTNET.value
    environment = parse_wallet_environment(raw) or WalletEnvironment.TESTNET
    if environment is WalletEnvironment.TESTNET:
        return True
    if environment is WalletEnvironment.MAINNET and explicit:
        return True
    if environment is WalletEnvironment.MAINNET_ONLY:
        await ctx.send(
            "Testnet commands are disabled by this bot's `mainnet-only` wallet mode."
        )
    else:
        await ctx.send(
            f"Mainnet is this bot's primary wallet environment. Use "
            f"`{ctx.clean_prefix}wallet testnet` for the separate testnet sandbox."
        )
    return False


class WalletCoreCommands:
    """User-facing wallet commands."""

    async def _wallet_environment(self) -> WalletEnvironment:
        setting = getattr(self.config, "operating_mode", None)
        raw = await setting() if setting is not None else WalletEnvironment.TESTNET.value
        return parse_wallet_environment(raw) or WalletEnvironment.TESTNET

    async def _testnet_path_allowed(
        self, ctx: commands.Context, *, explicit: bool = False
    ) -> bool:
        return await testnet_path_allowed(self, ctx, explicit=explicit)

    async def _invoke_testnet_command(self, ctx, command, *args, **kwargs):
        if not await testnet_path_allowed(self, ctx, explicit=True):
            return
        setattr(ctx, "_cryptowallet_explicit_testnet", True)
        try:
            await ctx.invoke(command, *args, **kwargs)
        finally:
            if hasattr(ctx, "_cryptowallet_explicit_testnet"):
                delattr(ctx, "_cryptowallet_explicit_testnet")

    async def _current_testnet_path_allowed(self, ctx: commands.Context) -> bool:
        return await testnet_path_allowed(
            self, ctx,
            explicit=bool(getattr(ctx, "_cryptowallet_explicit_testnet", False)),
        )

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
        is_mainnet = network is not None and not network.testnet
        environment_label = "mainnet wallet" if is_mainnet else "testnet wallet portfolio"
        embed.description = f"{display_name}’s public {environment_label}."
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
            if (
                network is None
                and item.key not in {BASE_SEPOLIA.key, ETHEREUM_SEPOLIA.key}
                and not native_balance
                and not tokens
            ):
                continue
            balance_lines = []
            if native_balance is None:
                balance_lines.append(f"{item.native_symbol}: Temporarily unavailable")
            else:
                balance_lines.append(
                    f"{item.native_symbol}: **{format_atomic_amount(native_balance, item)}**"
                )
            visible_tokens = tokens if network is not None else tokens[:15]
            for token_asset in visible_tokens:
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
                    f"· `{short_contract}`"
                )
            if len(tokens) > len(visible_tokens):
                balance_lines.append(
                    f"• **+{len(tokens) - len(visible_tokens)} more** · Use "
                    f"`{ctx.clean_prefix}wallet balance {item.key}`"
                )

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
            self._add_wallet_fields(embed, "━━ EVM WALLET ━━", evm_lines)
        if solana_lines:
            self._add_wallet_fields(embed, "━━ SOLANA WALLET ━━", solana_lines)


        if not embed.fields:
            embed.description += (
                " No Base mainnet account is available."
                if is_mainnet else " No enabled testnet accounts are available."
            )
        embed.set_footer(text=(
            "Base mainnet · Real assets · Verify addresses and contracts"
            if is_mainnet else
            "Testnet assets only · Token names may be spoofed; verify contract addresses"
        ))
        return embed

    @staticmethod
    def _add_wallet_fields(
        embed: discord.Embed, heading: str, lines: list[str]
    ) -> None:
        """Split wallet sections at line boundaries without breaking Markdown links."""

        chunks = []
        current = []
        current_length = 0
        for line in lines:
            added_length = len(line) + (1 if current else 0)
            if current and current_length + added_length > 1024:
                chunks.append("\n".join(current))
                current = [line]
                current_length = len(line)
            else:
                current.append(line)
                current_length += added_length
        if current:
            chunks.append("\n".join(current))
        for index, chunk in enumerate(chunks):
            embed.add_field(
                name=heading if index == 0 else f"{heading} continued",
                value=chunk,
                inline=False,
            )

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
        """Show a public wallet profile.

        Shows your wallet or another member's existing public testnet profile.
        """
        explicit_testnet = bool(
            getattr(ctx, "_cryptowallet_explicit_testnet", False)
        )
        environment_resolver = getattr(self, "_wallet_environment", None)
        environment = (
            await environment_resolver()
            if callable(environment_resolver)
            else WalletEnvironment.TESTNET
        )
        if environment is not WalletEnvironment.TESTNET and not explicit_testnet:
            policy_setting = getattr(
                getattr(self, "config", None), "base_mainnet_policy", None
            )
            policy = await policy_setting() if callable(policy_setting) else {}
            code_ready = (
                BASE_MAINNET.supports(NetworkCapability.BALANCE)
                and self.wallet_provider.supports(
                    BASE_MAINNET.key, NetworkCapability.BALANCE
                )
            )
            read_enabled = (
                code_ready
                and isinstance(policy, dict)
                and policy.get("enabled") is True
                and policy.get("paused", True) is False
                and (policy.get("capabilities") or {}).get("balance") is True
            )
            if read_enabled:
                if not await self._wallet_read_allowed(
                    ctx, "summary", WALLET_SUMMARY_COOLDOWN_SECONDS
                ):
                    return
                if await self.config.provider_paused():
                    await ctx.send(
                        "CryptoWallet provider processing is paused by the bot owner."
                    )
                    return
                target = member or ctx.author
                if target.id == ctx.author.id:
                    profile = await self._wallet_profile_or_error(ctx)
                    if profile is None:
                        return
                    try:
                        profile = await self.ensure_mainnet_wallet_profile(
                            target, profile
                        )
                    except (RuntimeError, WalletProviderError) as exc:
                        await ctx.send(str(exc))
                        return
                else:
                    profile = await self.config.user(target).profile()
                    if profile is None:
                        await ctx.send("That member has no public wallet profile.")
                        return
                await ctx.send(embed=await self._wallet_embed(
                    ctx, profile, target, network=BASE_MAINNET
                ))
                return
            terms_current = await self.has_current_cryptowallet_mainnet_terms(
                ctx.author.id
            )
            embed = discord.Embed(
                title="CryptoWallet Mainnet Staging",
                description=(
                    "Base mainnet is selected, but its real-asset read and send "
                    "capabilities remain unavailable."
                ),
                color=discord.Color.orange(),
            )
            embed.add_field(
                name="Your setup",
                value=(
                    "CryptoWallet terms are current. Use "
                    f"`{ctx.clean_prefix}wallet authorize` to review authorization."
                    if terms_current else
                    "Setup required · run "
                    f"`{ctx.clean_prefix}wallet authorize` for the guided terms and "
                    "authorization walkthrough."
                ),
                inline=False,
            )
            embed.add_field(
                name="Execution",
                value=(
                    "Code-disabled or emergency-paused. No mainnet balance or "
                    "transaction provider call was made."
                ),
                inline=False,
            )
            if environment is WalletEnvironment.MAINNET:
                embed.add_field(
                    name="Testnet sandbox",
                    value=f"Use `{ctx.clean_prefix}wallet testnet`.",
                    inline=False,
                )
            embed.set_footer(text="Staging status only · no blockchain state changed")
            await ctx.send(embed=embed)
            return
        if not await testnet_path_allowed(
            self, ctx, explicit=explicit_testnet
        ):
            return
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

    async def _send_wallet_terms_acceptance(self, user) -> int:
        """DM one protected CryptoWallet terms flow and return its expiry."""
        if await self.has_current_cryptowallet_mainnet_terms(user.id):
            raise RuntimeError("CryptoWallet terms are already current for your account.")
        status = await self.recovery_relay_status()
        if not status["configured"]:
            raise RuntimeError("Protected CryptoWallet terms acceptance is not configured on this bot.")
        result_handle = secrets.token_urlsafe(32)
        payload = {
            "product": CRYPTOWALLET_TERMS_PRODUCT,
            "version": CRYPTOWALLET_MAINNET_TERMS_VERSION,
            "result_handle": result_handle,
        }
        try:
            token, expires_at = await self.create_external_companion_handoff(
                user.id, "wallet_terms", payload
            )
            handoff = await self.register_recovery_handoff(
                token, expires_at, purpose="wallet_terms"
            )
            link = f"{status['approval_base_url']}/wallet-terms.html#handoff={quote(handoff, safe='')}"
            view = WalletTermsAcceptanceView(
                self, user.id, result_handle, expires_at, link
            )
            embed = discord.Embed(
                title="Accept CryptoWallet Mainnet Terms",
                description=(
                    "Review the terms on the protected page, submit your acceptance, "
                    "then return here and press **Confirm acceptance**."
                ),
                color=discord.Color.blurple(),
            )
            embed.add_field(name="Terms version", value=f"`{CRYPTOWALLET_MAINNET_TERMS_VERSION}`")
            embed.add_field(name="Link expires", value=f"<t:{expires_at}:R>")
            embed.set_footer(text="Accepting terms does not authorize a transaction or enable mainnet.")
            message = await user.send(embed=embed, view=view)
            view.message = message
        except discord.HTTPException as exc:
            raise RuntimeError("I could not send you a DM. Enable direct messages and try again.") from exc
        return expires_at

    @wallet.command(name="terms")
    async def wallet_terms(self, ctx: commands.Context):
        """Show CryptoWallet terms and protected acceptance controls."""
        base_url = str(await self.config.approval_base_url() or "").rstrip("/")
        current = await self.has_current_cryptowallet_mainnet_terms(ctx.author.id)
        environment = await self._wallet_environment()
        requirement = (
            "Required before mainnet signing is enabled for your account."
            if environment is not WalletEnvironment.TESTNET
            else "Not required for testnet use. Acceptance is offered when this bot switches to mainnet."
        )
        embed = discord.Embed(
            title="CryptoWallet Terms",
            description=(
                f"Acceptance: **{'Current' if current else 'Not accepted'}**\n"
                f"{requirement}"
            ),
            color=discord.Color.green() if current else discord.Color.blurple(),
        )
        embed.add_field(name="Terms version", value=f"`{CRYPTOWALLET_MAINNET_TERMS_VERSION}`")
        embed.add_field(
            name="What acceptance does",
            value="Records only your CryptoWallet terms acceptance. It does not authorize transactions or enable mainnet.",
            inline=False,
        )
        if not base_url:
            embed.add_field(name="Unavailable", value="The protected terms website is not configured.", inline=False)
            await ctx.send(embed=embed)
            return
        view = WalletTermsReferenceView(
            self, ctx.author.id, f"{base_url}/wallet-terms.html", current=current,
            show_accept=environment is not WalletEnvironment.TESTNET
        )
        await ctx.send(embed=embed, view=view)

    @wallet.command(name="balance", aliases=("funds",))
    async def wallet_balance(self, ctx: commands.Context, network_key: str = None):
        """Show wallet balances.

        Shows all testnet assets or details for one enabled testnet.
        """
        explicit_testnet = bool(getattr(ctx, "_cryptowallet_explicit_testnet", False))
        environment = await self._wallet_environment()
        mainnet_path = environment is not WalletEnvironment.TESTNET and not explicit_testnet
        if mainnet_path:
            policy_setting = getattr(
                getattr(self, "config", None), "base_mainnet_policy", None
            )
            policy = await policy_setting() if callable(policy_setting) else {}
            if (
                not BASE_MAINNET.supports(NetworkCapability.BALANCE)
                or not self.wallet_provider.supports(
                    BASE_MAINNET.key, NetworkCapability.BALANCE
                )
            ):
                await ctx.send("Base mainnet balances remain code-disabled pending review.")
                return
            if (
                not isinstance(policy, dict)
                or policy.get("enabled") is not True
                or policy.get("paused", True) is not False
                or (policy.get("capabilities") or {}).get("balance") is not True
            ):
                await ctx.send("Base mainnet balances are disabled or emergency-paused.")
                return
        elif not await testnet_path_allowed(self, ctx, explicit=explicit_testnet):
            return
        if not await self._wallet_read_allowed(
            ctx, "summary", WALLET_SUMMARY_COOLDOWN_SECONDS
        ):
            return
        profile = await self._wallet_profile_or_error(ctx)
        if profile is None:
            return
        network = BASE_MAINNET if mainnet_path else None
        if mainnet_path:
            try:
                profile = await self.ensure_mainnet_wallet_profile(ctx.author, profile)
            except (RuntimeError, WalletProviderError) as exc:
                await ctx.send(str(exc))
                return
        elif network_key is not None:
            network = resolve_network(network_key)
            if network is None or not network.testnet:
                await ctx.send(
                    f"That testnet is unavailable. Use `{ctx.clean_prefix}wallet networks` "
                    "to see enabled networks."
                )
                return
        await ctx.send(embed=await self._wallet_embed(ctx, profile, network=network))

    @wallet.group(name="testnet", invoke_without_command=True)
    async def wallet_testnet(self, ctx: commands.Context):
        """Open the separate testnet sandbox when the bot is mainnet-primary."""
        if not await self._testnet_path_allowed(ctx, explicit=True):
            return
        profile = await self._wallet_profile_or_error(ctx)
        if profile is None:
            return
        await ctx.send(embed=await self._wallet_embed(ctx, profile, ctx.author))

    @wallet_testnet.command(name="balance", aliases=("funds",))
    async def wallet_testnet_balance(self, ctx: commands.Context, network_key: str = None):
        """Show testnet balances through the explicit sandbox."""
        await self._invoke_testnet_command(
            ctx, self.wallet_balance, network_key=network_key
        )

    @wallet_testnet.command(name="networks")
    async def wallet_testnet_networks(self, ctx: commands.Context):
        """List networks available in the explicit testnet sandbox."""
        await self._invoke_testnet_command(ctx, self.wallet_networks)

    @wallet_testnet.command(name="tokens", aliases=("token",))
    async def wallet_testnet_tokens(self, ctx: commands.Context):
        """List tokens available in the explicit testnet sandbox."""
        await self._invoke_testnet_command(ctx, self.wallet_token_list)

    @wallet.command(name="mode", aliases=("environment",))
    async def wallet_mode(self, ctx: commands.Context):
        """Show the bot owner's installation-wide wallet operating mode."""
        current = parse_wallet_environment(await self.config.operating_mode())
        if current is None:
            current = WalletEnvironment.TESTNET
        if current is WalletEnvironment.TESTNET:
            detail = "Only reviewed testnet networks are presented and routed."
        elif current is WalletEnvironment.MAINNET:
            detail = (
                "Mainnet is primary; testnet is available only through explicit "
                "testnet commands."
            )
        else:
            detail = "Mainnet is primary and testnet commands are disabled."
        await ctx.send(
            f"Wallet operating mode: **{current.value}**\n{detail} "
            "Only the bot owner can change the installation mode."
        )

    @wallet.group(name="token", aliases=("tokens",), invoke_without_command=True)
    async def wallet_token(self, ctx: commands.Context):
        """Manage shared tokens.

        Adds or lists tokens shared by this bot installation.
        """
        if not await testnet_path_allowed(
            self, ctx, explicit=bool(getattr(ctx, "_cryptowallet_explicit_testnet", False))
        ):
            return
        await ctx.invoke(self.wallet_token_list)

    @wallet_token.command(name="add")
    async def wallet_token_add(
        self, ctx: commands.Context, network_key: str, contract_address: str
    ):
        """Add a shared token.

        Validates and adds an ERC-20 token for every wallet user.
        """
        if not await testnet_path_allowed(
            self, ctx, explicit=bool(getattr(ctx, "_cryptowallet_explicit_testnet", False))
        ):
            return
        if not await self._wallet_read_allowed(ctx, "token_submission", 30):
            return
        network = NETWORKS.get(network_key.strip().lower())
        if (
            network is None
            or network.family is not ChainFamily.EVM
            or not network.testnet
            or not network.supports(NetworkCapability.BALANCE)
        ):
            await ctx.send(f"Choose an enabled EVM testnet from `{ctx.clean_prefix}wallet networks`.")
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
        """List shared registered tokens available to wallet commands."""
        if not await testnet_path_allowed(
            self, ctx, explicit=bool(getattr(ctx, "_cryptowallet_explicit_testnet", False))
        ):
            return
        registry = await self.config.token_registry()
        visible = []
        for network_key, entries in registry.items():
            network = NETWORKS.get(network_key)
            if network is None:
                continue
            lines = []
            for contract, entry in entries.items():
                state = str(entry.get("status") or "community")
                if state not in {"community", "recognized"}:
                    continue
                marker = "✅ recognized" if state == "recognized" else "community"
                lines.append(
                    f"- **{entry.get('symbol', 'TOKEN')}** · {network.name} · "
                    f"{marker} · `{contract}`"
                )
            if lines:
                visible.append((network, lines))
        if not visible:
            await ctx.send("No shared registered tokens are available yet.")
            return
        embed = discord.Embed(
            title="Shared registered tokens",
            description=(
                "Tokens this bot recognizes for portfolio balances and sends. "
                "Native coins such as ETH and SOL are supported automatically and "
                "are not listed here."
            ),
            color=await ctx.embed_color(),
        )
        for network, lines in visible:
            self._add_wallet_fields(embed, network.name, lines)
        embed.set_footer(text="Token names may be spoofed; verify contract addresses")
        await ctx.send(embed=embed)

    @wallet_token.command(name="default", aliases=("defaults",))
    async def wallet_token_default(
        self, ctx: commands.Context, network_or_action: str = None, asset: str = None
    ):
        """View, set, or reset the asset used by the short wallet send form."""
        if not await testnet_path_allowed(
            self, ctx, explicit=bool(getattr(ctx, "_cryptowallet_explicit_testnet", False))
        ):
            return
        user_config = self.config.user(ctx.author)
        if network_or_action is None:
            current = await user_config.default_send_asset()
            if not isinstance(current, dict):
                await ctx.send("Your default send asset follows the server default network native token.")
                return
            network = NETWORKS.get(str(current.get("network") or ""))
            if network is None:
                await ctx.send(f"Your stored default asset is unavailable. Use `{ctx.clean_prefix}wallet token default reset`.")
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
            await ctx.send(f"Choose a send-enabled testnet from `{ctx.clean_prefix}wallet networks`.")
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
        """Manage confirmation DMs.

        Shows or changes optional wallet transaction confirmation notifications.
        """
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
        """Show wallet security.

        Shows emergency lock status and available wallet protections.
        """
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
        totp_enabled = await self.user_totp_enabled(ctx.author.id)
        totp_status = "enabled" if totp_enabled else "available but not enabled"
        await ctx.send(
            f"**Wallet security: standard; authenticator {totp_status}**\n"
            f"Use `{ctx.clean_prefix}wallet security lock` if your Discord account or "
            "wallet access may be compromised. The lock takes effect immediately and "
            "only the bot owner can remove it. Use the wallet security 2fa subcommands to manage optional authenticator protection."
        )

    @staticmethod
    def _totp_enabled_message(prefix: str) -> str:
        return (
            "**Authenticator protection is enabled for your CryptoWallet account.**\n"
            f"`{prefix}wallet security 2fa verify` — Check your current authenticator\n"
            f"`{prefix}wallet security 2fa replace` — Move to a new app or device\n"
            f"`{prefix}wallet security 2fa disable` — Remove authenticator protection\n"
            f"`{prefix}wallet security 2fa lost` — Start lost-access recovery"
        )

    @wallet_security.group(name="2fa", aliases=("totp",), invoke_without_command=True)
    async def wallet_security_2fa(self, ctx: commands.Context):
        """Show optional authenticator protection status."""

        enabled = await self.user_totp_enabled(ctx.author.id)
        if enabled:
            await ctx.send(WalletCoreCommands._totp_enabled_message(ctx.clean_prefix))
        else:
            await ctx.send(
                "**Authenticator protection is not enabled for your CryptoWallet account.** "
                f"Use `{ctx.clean_prefix}wallet security 2fa setup` to enable it."
            )

    @wallet_security_2fa.command(name="setup", aliases=("enroll",))
    async def wallet_security_2fa_setup(self, ctx: commands.Context):
        """Start protected Authy-compatible TOTP enrollment."""

        if not await self._wallet_sensitive_allowed(ctx):
            return
        if await self.user_totp_enabled(ctx.author.id):
            await ctx.send(WalletCoreCommands._totp_enabled_message(ctx.clean_prefix))
            return
        profile = await self._wallet_profile_or_error(ctx)
        if profile is None:
            return
        status = await self.recovery_relay_status()
        if not status["configured"]:
            await ctx.send(
                "Authenticator setup is unavailable because the protected website relay "
                "is not configured."
            )
            return
        result_handle = secrets.token_urlsafe(32)
        payload = {
            "version": 1,
            "profile_id": str(profile["profile_id"]),
            "result_handle": result_handle,
            "public_jwk": await self.totp_enrollment_public_jwk(),
        }
        try:
            token, expires_at = await self.create_external_companion_handoff(
                ctx.author.id, "totp_enroll", payload
            )
            handoff = await self.register_recovery_handoff(
                token, expires_at, purpose="totp_enroll"
            )
            base_url = status["approval_base_url"]
            encoded_handoff = quote(handoff, safe="")
            link = f"{base_url}/security.html#handoff={encoded_handoff}"
            embed = discord.Embed(
                title="Set Up Wallet Authenticator",
                description=(
                    "Press **Set up authenticator**, scan the QR code or use the manual "
                    "key, then return here and press **Confirm enrollment**."
                ),
                color=discord.Color.blurple(),
            )
            embed.add_field(
                name="Privacy",
                value=(
                    "The setup page generates the seed locally and sends the bot only RSA-OAEP "
                    "ciphertext. Enter the six-digit proof only in the private Discord modal."
                ),
                inline=False,
            )
            embed.add_field(
                name="Link expires", value=f"<t:{expires_at}:R>", inline=True
            )
            view = WalletTotpEnrollmentView(
                self, ctx.author.id, result_handle, expires_at, link
            )
        except (KeyError, RuntimeError, ValueError):
            log.exception("Could not prepare authenticator enrollment for user %s", ctx.author.id)
            await ctx.send(
                "Authenticator setup could not be created because the protected website "
                "relay rejected it. Try again; if this continues, contact the bot owner."
            )
            return
        try:
            message = await ctx.author.send(embed=embed, view=view)
            view.message = message
        except discord.HTTPException:
            await ctx.send("I could not send you a DM. Enable direct messages and try again.")
            return
        await ctx.send(
            f"I sent your protected authenticator setup by DM; it expires <t:{expires_at}:R>."
        )

    async def _send_totp_management(self, ctx: commands.Context, action: str) -> None:
        if not await self._wallet_sensitive_allowed(ctx):
            return
        if not await self.user_totp_enabled(ctx.author.id):
            await ctx.send("Authenticator protection is not enabled for this wallet.")
            return
        title = {
            "replace": "Replace Wallet Authenticator",
            "disable": "Disable Wallet Authenticator",
            "verify": "Check Wallet Authenticator",
        }[action]
        warning = {
            "replace": (
                "After verification, the old factor is removed and authenticator protection "
                "is temporarily off. Immediately run "
                f"`{ctx.clean_prefix}wallet security 2fa setup` to enroll the replacement."
            ),
            "disable": (
                "After verification, future sends will no longer require an authenticator code."
            ),
            "verify": (
                "Enter a current code to confirm that your enrolled authenticator still works. "
                "No transaction or settings change will be made."
            ),
        }[action]
        embed = discord.Embed(
            title=title,
            description=(
                warning + "\n\nPress the button and enter the current code in the private modal. "
                "No code belongs in Discord chat."
            ),
            color=discord.Color.orange(),
        )
        view = WalletTotpManagementView(
            self, ctx.author.id, action, command_prefix=ctx.clean_prefix
        )
        try:
            message = await ctx.author.send(embed=embed, view=view)
            view.message = message
        except discord.HTTPException:
            await ctx.send("Enable direct messages and try again.")
            return
        acknowledgement = {
            "verify": "I sent you a private authenticator check by DM.",
            "disable": "I sent you private authenticator disable controls by DM.",
            "replace": "I sent you private authenticator replacement controls by DM.",
        }[action]
        await ctx.send(acknowledgement)

    @wallet_security_2fa.command(name="verify", aliases=("check", "test"))
    async def wallet_security_2fa_verify(self, ctx: commands.Context):
        """Check the enrolled authenticator without changing wallet settings."""

        await self._send_totp_management(ctx, "verify")

    @wallet_security_2fa.command(name="disable", aliases=("remove",))
    async def wallet_security_2fa_disable(self, ctx: commands.Context):
        """Disable TOTP after current-factor verification."""

        await self._send_totp_management(ctx, "disable")

    @wallet_security_2fa.command(name="replace", aliases=("rotate",))
    async def wallet_security_2fa_replace(self, ctx: commands.Context):
        """Verify the current factor before replacement enrollment."""

        await self._send_totp_management(ctx, "replace")

    @wallet_security_2fa.command(name="lost", aliases=("recovery",))
    async def wallet_security_2fa_lost(self, ctx: commands.Context):
        """Explain lost-authenticator recovery without bypassing identity review."""

        await ctx.send(
            f"Immediately run `{ctx.clean_prefix}wallet security lock`, then contact the bot owner. "
            "The owner may reset authenticator state only while the wallet remains emergency-locked "
            "and after an independent identity review. Unlocking does not restore authorization."
        )

    async def _apply_user_emergency_lock(self, user) -> str:
        """Persist a user emergency lock before attempting provider revocation."""

        user_config = self.config.user(user)
        if await user_config.security_locked():
            return "Your wallet is already emergency-locked."
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
        return (
            "Your wallet is now emergency-locked. " + revocation + " Only the bot owner "
            "can unlock outgoing use; receiving funds and read-only commands still work. "
            "Previously exported keys or recovery material cannot be revoked by this lock."
        )

    @wallet_security.command(name="lock", aliases=("freeze",))
    async def wallet_security_lock(self, ctx: commands.Context, confirmed: bool = False):
        """Review or immediately apply an emergency wallet lock."""

        if await self.config.user(ctx.author).security_locked():
            await ctx.send("Your wallet is already emergency-locked.")
            return
        if confirmed:
            await ctx.send(await self._apply_user_emergency_lock(ctx.author))
            return
        embed = discord.Embed(
            title="Emergency-lock your wallet?",
            description=(
                "**Use this if your Discord or wallet access may be compromised.**\n"
                "Receiving funds and read-only commands will continue to work."
            ),
            color=discord.Color.orange(),
        )
        embed.add_field(
            name="Lock effects",
            value=(
                "Blocks sends, authorization, renewal, and signer export; rejects pending "
                "intents; and attempts to revoke current bot signing authorization."
            ),
            inline=False,
        )
        embed.add_field(
            name="Important limits",
            value=(
                "Only the bot owner can unlock outgoing use. Previously exported keys or "
                "recovery material cannot be revoked by this lock."
            ),
            inline=False,
        )
        embed.set_footer(
            text=f"For an immediate lock, use {ctx.clean_prefix}wallet security lock true"
        )
        await ctx.send(
            embed=embed,
            view=WalletEmergencyLockView(self, ctx.author.id),
        )

    @wallet.command(name="networks")
    async def wallet_networks(self, ctx: commands.Context):
        """List wallet networks.

        Lists the test networks enabled for this prototype.
        """
        if not await testnet_path_allowed(
            self, ctx, explicit=bool(getattr(ctx, "_cryptowallet_explicit_testnet", False))
        ):
            return
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
        """Choose a wallet network.

        Shows or selects the preferred network for network-specific commands.
        """
        if not await testnet_path_allowed(
            self, ctx, explicit=bool(getattr(ctx, "_cryptowallet_explicit_testnet", False))
        ):
            return
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
