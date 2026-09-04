import {
  authenticateWithJWT,
  createDelegationForAccount,
  createEvmKeyExportIframe,
  initialize,
  isSignedIn,
  signOut,
} from "@coinbase/cdp-core";

async function authenticateWallet(projectId, expectedUserId, expectedAddress, handoffToken) {
  if (!projectId || !expectedUserId || !expectedAddress || !handoffToken) {
    throw new Error("Wallet authentication configuration is incomplete.");
  }
  await initialize({
    projectId,
    customAuth: { getJwt: async () => handoffToken },
    ethereum: { createOnLogin: "smart" },
    disableAnalytics: true,
  });
  if (await isSignedIn()) await signOut();
  const { user } = await authenticateWithJWT();
  if (user.userId !== expectedUserId) {
    throw new Error("Coinbase returned a different wallet user than requested.");
  }
  const addresses = (user.evmSmartAccountObjects || []).map((account) =>
    account.address.toLowerCase()
  );
  if (!addresses.includes(expectedAddress.toLowerCase())) {
    throw new Error("Coinbase returned a different wallet than requested.");
  }
  return user;
}

export async function authorizeWallet(projectId, expectedUserId, expectedAddress, handoffToken) {
  try {
    await authenticateWallet(projectId, expectedUserId, expectedAddress, handoffToken);
    const delegation = await createDelegationForAccount({
      address: expectedAddress,
      expiresAt: new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString(),
    });
    return { address: expectedAddress, expiresAt: delegation.expiresAt };
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
      projectId, expectedUserId, expectedAddress, handoffToken
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
