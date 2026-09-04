from .activity import WalletActivityCommands
from .authorization import WalletAuthorizationCommands
from .core import WalletCoreCommands
from .transactions import WalletTransactionCommands


class WalletCommands(
    WalletAuthorizationCommands,
    WalletActivityCommands,
    WalletTransactionCommands,
    WalletCoreCommands,
):
    """Composed user-facing wallet command set."""
