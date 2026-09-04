import {
  authenticateWithJWT,
  createDelegation,
  createEvmKeyExportIframe,
  initialize,
  isSignedIn,
  signOut,
} from "@coinbase/cdp-core";

const DELEGATION_DURATION_MS = 365 * 24 * 60 * 60 * 1000;

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

export async function authorizeWallet(projectId, expectedUserId, expectedAccounts, handoffToken) {
  try {
    await authenticateWallet(projectId, expectedUserId, expectedAccounts, handoffToken);
    const delegation = await createDelegation({
      expiresAt: new Date(Date.now() + DELEGATION_DURATION_MS).toISOString(),
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
  projectId, expectedUserId, expectedAddress, handoffToken, target
) {
  try {
    const user = await authenticateWallet(
      projectId, expectedUserId, [{ family: "evm", address: expectedAddress }], handoffToken
    );
    const ownerAddress = resolveSmartAccountOwner(user, expectedAddress);
    await createEvmKeyExportIframe({
      address: ownerAddress,
      target,
      projectId,
      label: "Copy wallet signer private key",
      copiedLabel: "Wallet signer private key copied",
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
    return { ownerAddress };
  } catch (error) {
    await signOut().catch(() => undefined);
    throw error;
  }
}
