import {
  authenticateWithJWT,
  createDelegation,
  getDelegation,
  createEvmKeyExportIframe,
  createSolanaKeyExportIframe,
  initialize,
  isSignedIn,
  revokeDelegation,
  signEvmMessage,
  signOut,
} from "@coinbase/cdp-core";

function safeCdpStageError(stage, error) {
  const status = Number(error?.statusCode);
  const correlationId = typeof error?.correlationId === "string" &&
    /^[A-Za-z0-9_-]{1,128}$/.test(error.correlationId) ? error.correlationId : "";
  let message = `${stage} failed`;
  if (Number.isInteger(status) && status >= 400 && status <= 599) message += ` (HTTP ${status})`;
  if (correlationId) message += `. CDP correlation ID: ${correlationId}`;
  else message += ".";
  return new Error(message, { cause: error });
}

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
  projectId, expectedUserId, expectedAccounts, handoffToken, delegationExpiresAt,
  delegationMaxDays
) {
  const expiresAt = new Date(Number(delegationExpiresAt) * 1000);
  const maxDays = Number(delegationMaxDays);
  if (
    !Number.isSafeInteger(Number(delegationExpiresAt)) ||
    !Number.isSafeInteger(maxDays) || maxDays < 1 || maxDays > 365 ||
    expiresAt.getTime() <= Date.now() ||
    expiresAt.getTime() > Date.now() + maxDays * 24 * 60 * 60 * 1000
  ) {
    throw new Error("Wallet delegation policy is invalid.");
  }
  try {
    let delegationAccounts;
    try {
      const user = await authenticateWallet(projectId, expectedUserId, expectedAccounts, handoffToken);
      delegationAccounts = expectedAccounts.map((account) => ({
        family: account.family,
        address: account.family === "evm"
          ? resolveSmartAccountOwner(user, account.address)
          : account.address,
      }));
    } catch (error) {
      throw safeCdpStageError("Wallet authentication", error);
    }
    const evmOwner = delegationAccounts.find((account) => account.family === "evm");
    if (!evmOwner) {
      throw new Error("EVM wallet signing preflight could not identify the wallet owner.");
    }
    const diagnosticMessage =
      "SickGaming CryptoWallet diagnostic\n" +
      "This signature grants no permission and authorizes no transaction.\n" +
      "Project: " + projectId + "\n" +
      "Wallet user: " + expectedUserId + "\n" +
      "Authorization expiry: " + expiresAt.toISOString();
    try {
      await signEvmMessage({
        evmAccount: evmOwner.address,
        message: diagnosticMessage,
      });
    } catch (error) {
      throw safeCdpStageError("EVM wallet signing preflight", error);
    }
    try {
      const result = await createDelegation({
        expiresAt: expiresAt.toISOString(),
      });
      const verified = await getDelegation();
      if (
        !verified?.expiresAt ||
        new Date(verified.expiresAt).getTime() !== new Date(result.expiresAt).getTime()
      ) {
        await revokeDelegation().catch(() => undefined);
        throw new Error("Coinbase did not verify the wallet-profile delegation.");
      }
      return { expiresAt: expiresAt.toISOString(), scope: "profile" };
    } catch (error) {
      throw safeCdpStageError("Wallet-profile delegation", error);
    }
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

export async function prepareRecoveryExports(
  projectId, expectedUserId, expectedAccounts, handoffToken, targets
) {
  const controls = [];
  try {
    const user = await authenticateWallet(projectId, expectedUserId, expectedAccounts, handoffToken);
    for (const account of expectedAccounts) {
      const target = targets?.[account.family];
      if (!(target instanceof HTMLElement)) {
        throw new Error("A secure wallet export target is missing.");
      }
      const exportAddress = account.family === "evm"
        ? resolveSmartAccountOwner(user, account.address)
        : account.address;
      const createExportIframe = account.family === "evm"
        ? createEvmKeyExportIframe
        : createSolanaKeyExportIframe;
      const control = await createExportIframe({
        address: exportAddress,
        target,
        projectId,
        label: account.family === "evm"
          ? "Copy EVM wallet signer private key"
          : "Copy Solana wallet private key",
        copiedLabel: "Wallet private key copied",
        fullWidth: true,
        onStatusUpdate: (status, message) => {
          const event = new CustomEvent("sickwallet-export-status", {
            detail: { family: account.family, status, message: message || "" },
          });
          window.dispatchEvent(event);
        },
      });
      controls.push(control);
    }
    return {
      cleanup: async () => {
        controls.forEach((control) => control.cleanup());
        await signOut().catch(() => undefined);
      },
    };
  } catch (error) {
    controls.forEach((control) => control.cleanup());
    await signOut().catch(() => undefined);
    throw error;
  }
}
