"use strict";

const statusElement = document.querySelector("#recovery-status");
const detailsElement = document.querySelector("#recovery-details");
const controlsElement = document.querySelector("#recovery-controls");
const confirmInput = document.querySelector("#recovery-confirm");
const accountSelect = document.querySelector("#recovery-account");
const exportButton = document.querySelector("#prepare-export");
const exportContainer = document.querySelector("#key-export-container");
let handoffToken = null;
let recoverySession = null;

function addDetail(label, value) {
  const term = document.createElement("dt");
  const detail = document.createElement("dd");
  term.textContent = label;
  detail.textContent = value;
  detailsElement.append(term, detail);
}

function decodeRecoveryHandoff() {
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  handoffToken = fragment.get("handoff");
  history.replaceState(null, "", window.location.pathname + window.location.search);
  if (!handoffToken) throw new Error("This recovery link is missing its protected handoff.");
  const parts = handoffToken.split(".");
  if (parts.length !== 3) throw new Error("This recovery link is malformed.");
  const encoded = parts[1].replace(/-/g, "+").replace(/_/g, "/");
  const claims = JSON.parse(atob(encoded.padEnd(Math.ceil(encoded.length / 4) * 4, "=")));
  if (claims.sickwallet_purpose !== "recovery" || !claims.sub || !claims.aud) {
    throw new Error("This handoff is not valid for wallet recovery.");
  }
  const accounts = Array.isArray(claims.sickwallet_accounts)
    ? claims.sickwallet_accounts.filter(({ family, address }) =>
        (family === "evm" || family === "solana") && typeof address === "string" && address
      )
    : [];
  if (!accounts.length || Number(claims.exp) * 1000 <= Date.now()) {
    throw new Error("This wallet recovery link has expired or is incomplete.");
  }
  return {
    expiresAt: Number(claims.exp),
    projectId: claims.aud,
    userId: claims.sub,
    accounts,
  };
}

confirmInput.addEventListener("change", () => {
  exportButton.disabled = !confirmInput.checked;
});

exportButton.addEventListener("click", async () => {
  exportButton.disabled = true;
  confirmInput.disabled = true;
  statusElement.textContent = "Verifying your wallet signer with Coinbase…";
  try {
    const { prepareRecoveryExport } = await import("./cdp-wallet.js");
    const selectedAccount = recoverySession.accounts[Number(accountSelect.value)];
    if (!selectedAccount) throw new Error("Select a wallet account to export.");
    const result = await prepareRecoveryExport(
      recoverySession.projectId,
      recoverySession.userId,
      recoverySession.accounts,
      selectedAccount,
      handoffToken,
      exportContainer
    );
    handoffToken = null;
    addDetail(
      result.family === "evm" ? "Wallet signer address" : "Solana wallet address",
      result.exportAddress
    );
    exportButton.hidden = true;
    statusElement.textContent = "Verified. Use the secure Coinbase control below to copy the wallet signer key.";
  } catch (error) {
    statusElement.textContent = error instanceof Error ? error.message : "Secure wallet export could not start.";
    confirmInput.disabled = false;
    exportButton.disabled = !confirmInput.checked;
  }
});

window.addEventListener("sickwallet-export-status", (event) => {
  const { status, message } = event.detail || {};
  if (status === "success") {
    statusElement.textContent = "Wallet signer key copied. Store it securely and clear your clipboard when finished.";
  } else if (status === "expired") {
    statusElement.textContent = "The secure export session expired. Request a new link from Discord.";
  } else if (status === "error") {
    statusElement.textContent = message || "Coinbase could not export this wallet signer key.";
  }
});

Promise.resolve().then(decodeRecoveryHandoff)
  .then((session) => {
    recoverySession = session;
    statusElement.textContent = "Protected wallet signer handoff loaded.";
    session.accounts.forEach((account, index) => {
      const option = document.createElement("option");
      option.value = String(index);
      option.textContent = account.family === "evm"
        ? `Base Sepolia smart account — ${account.address}`
        : `Solana Devnet account — ${account.address}`;
      accountSelect.append(option);
    });
    addDetail("Accounts available", String(session.accounts.length));
    addDetail("Expires", new Date(session.expiresAt * 1000).toLocaleString());
    detailsElement.hidden = false;
    controlsElement.hidden = false;
  })
  .catch((error) => {
    statusElement.textContent = error instanceof Error ? error.message : "Wallet recovery is unavailable.";
  });
