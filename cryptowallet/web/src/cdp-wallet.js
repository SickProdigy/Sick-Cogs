import {
  authenticateWithJWT,
  getAccessToken,
  initialize,
  isSignedIn,
  signOut,
} from "@coinbase/cdp-core";

async function responseData(response, fallback) {
  const body = await response.json();
  if (!response.ok) throw new Error(body.error?.message || fallback);
  return body.data;
}

async function freshJwt() {
  const response = await fetch("api/auth-token.php", {
    method: "POST",
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  const data = await responseData(response, "Wallet authentication is unavailable.");
  return data.token;
}

export async function claimWallet(projectId, expectedAddress) {
  if (!projectId || !expectedAddress) {
    throw new Error("Wallet claim configuration is incomplete.");
  }
  await initialize({
    projectId,
    customAuth: { getJwt: freshJwt },
    ethereum: { createOnLogin: "smart" },
    disableAnalytics: true,
  });
  if (await isSignedIn()) {
    await signOut();
  }
  try {
    const { user } = await authenticateWithJWT();
    const addresses = (user.evmSmartAccountObjects || []).map((account) =>
      account.address.toLowerCase()
    );
    if (!addresses.includes(expectedAddress.toLowerCase())) {
      throw new Error("Coinbase returned a different wallet than the provisioned address.");
    }
    const accessToken = await getAccessToken();
    const response = await fetch("api/claim.php", {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ access_token: accessToken }),
    });
    return await responseData(response, "Wallet control could not be verified.");
  } finally {
    await signOut().catch(() => undefined);
  }
}
