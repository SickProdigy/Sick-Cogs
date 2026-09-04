from .account import WalletAccountCommands
from .activity import WalletActivityCommands
from .authorization import WalletAuthorizationCommands
from .core import WalletCoreCommands
from .transactions import WalletTransactionCommands


class WalletCommands(
    WalletAccountCommands,
    WalletAuthorizationCommands,
    WalletActivityCommands,
    WalletTransactionCommands,
    WalletCoreCommands,
):
    """Composed user-facing wallet command set."""
