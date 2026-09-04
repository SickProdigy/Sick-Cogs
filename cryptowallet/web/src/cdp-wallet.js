import {
  authenticateWithJWT,
  createDelegationForAccount,
  initialize,
  isSignedIn,
  linkEmail,
  signOut,
  verifyEmailOTP,
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

export async function beginRecoveryEnrollment(
  projectId, expectedUserId, expectedAddress, handoffToken, email
) {
  try {
    await authenticateWallet(projectId, expectedUserId, expectedAddress, handoffToken);
    const result = await linkEmail(email);
    return { flowId: result.flowId };
  } catch (error) {
    await signOut().catch(() => undefined);
    throw error;
  }
}

export async function completeRecoveryEnrollment(flowId, otp, expectedUserId) {
  try {
    const { user } = await verifyEmailOTP({ flowId, otp });
    if (user.userId !== expectedUserId) {
      throw new Error("Coinbase verified a different wallet user than requested.");
    }
    return { userId: user.userId };
  } finally {
    await signOut().catch(() => undefined);
  }
}
