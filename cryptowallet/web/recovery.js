"use strict";

const statusElement = document.querySelector("#recovery-status");
const detailsElement = document.querySelector("#recovery-details");
const controlsElement = document.querySelector("#recovery-controls");
const confirmInput = document.querySelector("#recovery-confirm");
const accountsElement = document.querySelector("#recovery-accounts");
const exportContainer = document.querySelector("#key-export-container");
let handoffToken = null;
let recoverySession = null;
let activeFamily = null;

function addDetail(label, value) {
  const term = document.createElement("dt");
  const detail = document.createElement("dd");
  term.textContent = label;
  detail.textContent = value;
  detailsElement.append(term, detail);
}

function decodeRecoveryToken(token) {
  const parts = token.split(".");
  if (parts.length !== 3) throw new Error("This recovery handoff is malformed.");
  const encoded = parts[1].replace(/-/g, "+").replace(/_/g, "/");
  const claims = JSON.parse(atob(encoded.padEnd(Math.ceil(encoded.length / 4) * 4, "=")));
  if (claims.sickwallet_purpose !== "recovery" || !claims.sub || !claims.aud) {
    throw new Error("This handoff is not valid for wallet recovery.");
  }
  return claims;
}

async function consumeRecoveryHandoff() {
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const handle = fragment.get("handoff");
  history.replaceState(null, "", window.location.pathname + window.location.search);
  if (!handle || !/^[A-Za-z0-9_-]{32,128}$/.test(handle)) {
    throw new Error("This recovery link is invalid, expired, or already used.");
  }
  const response = await fetch("./api/recovery-handoff.php", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ operation: "consume", handoff: handle }),
  });
  const result = await response.json().catch(() => null);
  if (!response.ok || result?.status !== "consumed" || typeof result.jwt !== "string") {
    throw new Error(result?.error?.message || "This recovery link is invalid, expired, or already used.");
  }
  handoffToken = result.jwt;
  const claims = decodeRecoveryToken(handoffToken);
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
  setExportButtonsDisabled(!confirmInput.checked);
});

function setExportButtonsDisabled(disabled) {
  accountsElement.querySelectorAll("button").forEach((button) => {
    button.disabled = disabled;
  });
}

function accountLabel(account) {
  return account.family === "evm" ? "EVM signer" : "Solana account";
}

async function prepareAccountExport(account) {
  setExportButtonsDisabled(true);
  confirmInput.disabled = true;
  activeFamily = account.family;
  exportContainer.replaceChildren();
  statusElement.textContent = `Verifying your ${accountLabel(account)} with Coinbase…`;
  try {
    const { prepareRecoveryExport } = await import("./cdp-wallet.js");
    await prepareRecoveryExport(
      recoverySession.projectId,
      recoverySession.userId,
      recoverySession.accounts,
      account,
      handoffToken,
      exportContainer
    );
    statusElement.textContent = `Verified. Use the secure Coinbase control below to copy the ${accountLabel(account)} key.`;
  } catch (error) {
    statusElement.textContent = error instanceof Error ? error.message : "Secure wallet export could not start.";
    confirmInput.disabled = false;
    setExportButtonsDisabled(!confirmInput.checked);
  }
}

function addAccountControl(account) {
  const card = document.createElement("section");
  card.className = "recovery-account";
  const heading = document.createElement("strong");
  heading.textContent = account.family === "evm" ? "Base Sepolia EVM signer" : "Solana Devnet account";
  const address = document.createElement("code");
  address.textContent = account.address;
  const button = document.createElement("button");
  button.type = "button";
  button.disabled = true;
  button.textContent = account.family === "evm" ? "Export EVM signer key" : "Export Solana account key";
  button.addEventListener("click", () => void prepareAccountExport(account));
  card.append(heading, address, button);
  accountsElement.append(card);
}

window.addEventListener("sickwallet-export-status", (event) => {
  const { status, message } = event.detail || {};
  if (status === "success") {
    statusElement.textContent = `${accountLabel({ family: activeFamily })} key copied. Store it securely and clear your clipboard when finished.`;
    confirmInput.disabled = false;
    setExportButtonsDisabled(!confirmInput.checked);
  } else if (status === "expired") {
    statusElement.textContent = "The secure export session expired. Request a new link from Discord.";
    confirmInput.disabled = true;
    setExportButtonsDisabled(true);
  } else if (status === "error") {
    statusElement.textContent = message || "Coinbase could not export this wallet signer key.";
    confirmInput.disabled = false;
    setExportButtonsDisabled(!confirmInput.checked);
  }
});

Promise.resolve().then(consumeRecoveryHandoff)
  .then((session) => {
    recoverySession = session;
    statusElement.textContent = "Protected wallet recovery handoff loaded. Choose each account you want to back up.";
    session.accounts.forEach(addAccountControl);
    addDetail("Accounts available", String(session.accounts.length));
    addDetail("Expires", new Date(session.expiresAt * 1000).toLocaleString());
    detailsElement.hidden = false;
    controlsElement.hidden = false;
  })
  .catch((error) => {
    statusElement.textContent = error instanceof Error ? error.message : "Wallet recovery is unavailable.";
  });
