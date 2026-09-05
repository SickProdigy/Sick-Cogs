import {
  authenticateWithJWT,
  createDelegation,
  createEvmKeyExportIframe,
  createSolanaKeyExportIframe,
  initialize,
  isSignedIn,
  signOut,
} from "@coinbase/cdp-core";

async function authenticateWallet(projectId, expectedUserId, expectedAccounts, handoffToken) {
  if (!projectId || !expectedUserId || !Array.isArray(expectedAccounts) || !expectedAccounts.length || !handoffToken) {
    throw new Error("Wallet authentication configuration is incomplete.");
  }
  await initialize({
    projectId,
    customAuth: { getJwt: async () => handoffToken },
    ethereum: { createOnLogin: "smart" },
    solana: { createOnLogin: false },
    disableAnalytics: true,
  });
  if (await isSignedIn()) await signOut();
  const { user } = await authenticateWithJWT();
  if (user.userId !== expectedUserId) {
    throw new Error("Coinbase returned a different wallet user than requested.");
  }
  const evmAddresses = new Set(
    (user.evmSmartAccountObjects || []).map((account) => account.address.toLowerCase())
  );
  const solanaAddresses = new Set(
    (user.solanaAccountObjects || []).map((account) => account.address)
  );
  const allAccountsMatch = expectedAccounts.every(({ family, address }) => {
    if (!address) return false;
    if (family === "evm") return evmAddresses.has(address.toLowerCase());
    if (family === "solana") return solanaAddresses.has(address);
    return false;
  });
  if (!allAccountsMatch) {
    throw new Error("Coinbase returned a different wallet account set than requested.");
  }
  return user;
}

export async function authorizeWallet(
  projectId, expectedUserId, expectedAccounts, handoffToken, delegationExpiresAt
) {
  const expiresAt = new Date(Number(delegationExpiresAt) * 1000);
  if (
    !Number.isSafeInteger(Number(delegationExpiresAt)) ||
    expiresAt.getTime() <= Date.now() ||
    expiresAt.getTime() > Date.now() + 365 * 24 * 60 * 60 * 1000
  ) {
    throw new Error("Wallet delegation policy is invalid.");
  }
  try {
    await authenticateWallet(projectId, expectedUserId, expectedAccounts, handoffToken);
    const delegation = await createDelegation({
      expiresAt: expiresAt.toISOString(),
    });
    return { expiresAt: delegation.expiresAt };
  } finally {
    await signOut().catch(() => undefined);
  }
}

export function resolveSmartAccountOwner(user, expectedAddress) {
  const smartAccount = (user.evmSmartAccountObjects || []).find(
    (account) => account.address.toLowerCase() === expectedAddress.toLowerCase()
  );
  const ownerAddresses = new Set(
    (smartAccount?.ownerAddresses || []).map((address) => address.toLowerCase())
  );
  const owner = (user.evmAccountObjects || []).find((account) =>
    ownerAddresses.has(account.address.toLowerCase())
  );
  if (!owner) {
    throw new Error("Coinbase did not return an exportable owner for this smart account.");
  }
  return owner.address;
}

export async function prepareRecoveryExport(
  projectId, expectedUserId, expectedAccounts, selectedAccount, handoffToken, target
) {
  try {
    const user = await authenticateWallet(projectId, expectedUserId, expectedAccounts, handoffToken);
    const exportAddress = selectedAccount.family === "evm"
      ? resolveSmartAccountOwner(user, selectedAccount.address)
      : selectedAccount.address;
    const createExportIframe = selectedAccount.family === "evm"
      ? createEvmKeyExportIframe
      : createSolanaKeyExportIframe;
    await createExportIframe({
      address: exportAddress,
      target,
      projectId,
      label: selectedAccount.family === "evm"
        ? "Copy EVM wallet signer private key"
        : "Copy Solana wallet private key",
      copiedLabel: "Wallet private key copied",
      fullWidth: true,
      onStatusUpdate: (status, message) => {
        const event = new CustomEvent("sickwallet-export-status", {
          detail: { status, message: message || "" },
        });
        window.dispatchEvent(event);
        if (["success", "error", "expired"].includes(status)) {
          void signOut().catch(() => undefined);
        }
      },
    });
    return { exportAddress, family: selectedAccount.family };
  } catch (error) {
    await signOut().catch(() => undefined);
    throw error;
  }
}
