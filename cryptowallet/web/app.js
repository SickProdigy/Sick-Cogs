"use strict";

document.documentElement.dataset.walletUi = "ready";

const statusElement = document.querySelector("#session-status");
const detailsElement = document.querySelector("#session-details");
const claimControls = document.querySelector("#claim-controls");
const claimButton = document.querySelector("#claim-wallet");
const claimStatus = document.querySelector("#claim-status");

function addDetail(label, value) {
  const term = document.createElement("dt");
  const detail = document.createElement("dd");
  term.textContent = label;
  detail.textContent = value;
  detailsElement.append(term, detail);
}

async function loadSession() {
  const response = await fetch("api/session.php", {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error?.message || "Wallet session unavailable.");
  return body.data;
}

function configureClaim(session) {
  if (session.purpose !== "claim" || !claimControls || !claimButton || !claimStatus) return;
  claimControls.hidden = false;
  if (!session.wallet?.address || !session.cdp?.project_id) {
    claimButton.disabled = true;
    claimStatus.textContent = "Wallet claiming is not completely configured.";
    return;
  }
  if (session.wallet.claimed) {
    claimStatus.textContent = "This wallet was already claimed. You may verify control again.";
  }
  claimButton.addEventListener("click", async () => {
    claimButton.disabled = true;
    claimStatus.textContent = "Authenticating this wallet with Coinbase…";
    try {
      const { claimWallet } = await import("./cdp-wallet.js");
      const result = await claimWallet(session.cdp.project_id, session.wallet.address);
      claimStatus.textContent = `Wallet control verified for ${result.address}.`;
      claimButton.textContent = "Wallet claimed";
    } catch (error) {
      claimStatus.textContent =
        error instanceof Error ? error.message : "Wallet claiming failed.";
      claimButton.disabled = false;
    }
  });
}

if (statusElement && detailsElement) {
  loadSession()
    .then((session) => {
      statusElement.textContent = "Discord identity verified.";
      addDetail("Purpose", session.purpose);
      addDetail("Expires", new Date(session.expires_at * 1000).toLocaleString());
      if (session.wallet?.address) {
        addDetail("Wallet", session.wallet.address);
        addDetail("Claim status", session.wallet.claimed ? "Claimed" : "Not claimed");
      }
      if (session.transaction) {
        addDetail("Network", `${session.transaction.network_name} (${session.transaction.chain_id})`);
        addDetail("From", session.transaction.from_address);
        addDetail("To", session.transaction.to_address);
        addDetail("Value (wei)", session.transaction.value_wei);
        addDetail("Intent", session.transaction.intent_id);
      }
      detailsElement.hidden = false;
      configureClaim(session);
    })
    .catch((error) => {
      statusElement.textContent = error.message;
    });
}
