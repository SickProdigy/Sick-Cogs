import {
  authenticateWithJWT,
  createDelegationForAccount,
  initialize,
  isSignedIn,
  signOut,
} from "@coinbase/cdp-core";

export async function authorizeWallet(projectId, expectedUserId, expectedAddress, handoffToken) {
  if (!projectId || !expectedUserId || !expectedAddress || !handoffToken) {
    throw new Error("Wallet authorization configuration is incomplete.");
  }
  await initialize({
    projectId,
    customAuth: { getJwt: async () => handoffToken },
    ethereum: { createOnLogin: "smart" },
    disableAnalytics: true,
  });
  if (await isSignedIn()) {
    await signOut();
  }
  try {
    const { user } = await authenticateWithJWT();
    if (user.userId !== expectedUserId) {
      throw new Error("Coinbase returned a different wallet user than the handoff requested.");
    }
    const addresses = (user.evmSmartAccountObjects || []).map((account) =>
      account.address.toLowerCase()
    );
    if (!addresses.includes(expectedAddress.toLowerCase())) {
      throw new Error("Coinbase returned a different wallet than the provisioned address.");
    }
    const delegation = await createDelegationForAccount({
      address: expectedAddress,
      expiresAt: new Date(Date.now() + 24 * 60 * 60 * 1000).toISOString(),
    });
    return { address: expectedAddress, expiresAt: delegation.expiresAt };
  } finally {
    await signOut().catch(() => undefined);
  }
}
