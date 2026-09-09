"use strict";

const statusElement = document.querySelector("#recovery-status");
const detailsElement = document.querySelector("#recovery-details");
const controlsElement = document.querySelector("#recovery-controls");
const confirmInput = document.querySelector("#recovery-confirm");
const accountsElement = document.querySelector("#recovery-accounts");
let handoffToken = null;
let recoverySession = null;
let exportSession = null;
const exportTargets = new Map();

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

function accountLabel(account) {
  return account.family === "evm" ? "EVM signer" : "Solana account";
}

function addAccountControl(account) {
  const card = document.createElement("section");
  card.className = "recovery-account";
  const heading = document.createElement("strong");
  heading.textContent = account.family === "evm" ? "Base Sepolia EVM signer" : "Solana Devnet account";
  const address = document.createElement("code");
  address.textContent = account.address;
  const target = document.createElement("div");
  target.className = "recovery-account-export";
  target.setAttribute("aria-label", `Coinbase ${accountLabel(account)} key export control`);
  const waiting = document.createElement("span");
  waiting.className = "recovery-account-waiting";
  waiting.textContent = "Confirm the private-key safety notice to enable Coinbase export.";
  target.append(waiting);
  exportTargets.set(account.family, target);
  card.append(heading, address, target);
  accountsElement.append(card);
}

async function mountCoinbaseExportControls() {
  if (!confirmInput.checked || !recoverySession || exportSession) return;
  confirmInput.disabled = true;
  statusElement.textContent = "Verifying both wallet accounts with Coinbase…";
  exportTargets.forEach((target) => target.replaceChildren());
  try {
    const { prepareRecoveryExports } = await import("./cdp-wallet.js");
    exportSession = await prepareRecoveryExports(
      recoverySession.projectId,
      recoverySession.userId,
      recoverySession.accounts,
      handoffToken,
      Object.fromEntries(exportTargets)
    );
    statusElement.textContent = "Verified. Use the secure Coinbase button in each account card to copy that private key.";
  } catch (error) {
    statusElement.textContent = error instanceof Error ? error.message : "Secure wallet export could not start.";
    confirmInput.checked = false;
    confirmInput.disabled = false;
    recoverySession.accounts.forEach((account) => {
      const target = exportTargets.get(account.family);
      if (!target) return;
      const retry = document.createElement("span");
      retry.className = "recovery-account-waiting";
      retry.textContent = "Confirm the safety notice to retry Coinbase export.";
      target.replaceChildren(retry);
    });
  }
}

confirmInput.addEventListener("change", () => {
  if (confirmInput.checked) void mountCoinbaseExportControls();
});

window.addEventListener("sickwallet-export-status", (event) => {
  const { status, message, family } = event.detail || {};
  if (status === "success") {
    statusElement.textContent = `${accountLabel({ family })} key copied. Store it securely and clear your clipboard when finished.`;
  } else if (status === "expired") {
    statusElement.textContent = "The secure export session expired. Request a new link from Discord.";
    confirmInput.disabled = true;
  } else if (status === "error") {
    statusElement.textContent = message || `Coinbase could not export the ${accountLabel({ family })} key.`;
  }
});

window.addEventListener("pagehide", () => {
  if (exportSession?.cleanup) void exportSession.cleanup();
});

Promise.resolve().then(consumeRecoveryHandoff)
  .then((session) => {
    recoverySession = session;
    statusElement.textContent = "Protected wallet recovery handoff loaded. Confirm the safety notice to enable Coinbase export.";
    session.accounts.forEach(addAccountControl);
    addDetail("Accounts available", String(session.accounts.length));
    addDetail("Expires", new Date(session.expiresAt * 1000).toLocaleString());
    detailsElement.hidden = false;
    controlsElement.hidden = false;
  })
  .catch((error) => {
    statusElement.textContent = error instanceof Error ? error.message : "Wallet recovery is unavailable.";
  });
