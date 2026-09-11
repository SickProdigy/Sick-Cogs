"use strict";

document.documentElement.dataset.walletUi = "ready";

const statusElement = document.querySelector("#session-status");
const detailsElement = document.querySelector("#session-details");
const authorizationControls = document.querySelector("#authorization-controls");
const authorizationButton = document.querySelector("#authorize-wallet");
const authorizationStatus = document.querySelector("#authorization-status");
const authorizationDays = document.querySelector("#authorization-days");
const authorizationDurationHelp = document.querySelector("#authorization-duration-help");
let handoffToken = null;

function addDetail(label, value) {
  const term = document.createElement("dt");
  const detail = document.createElement("dd");
  term.textContent = label;
  detail.textContent = value;
  detailsElement.append(term, detail);
}

function decodeHandoff() {
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  handoffToken = fragment.get("handoff");
  history.replaceState(null, "", `${window.location.pathname}${window.location.search}`);
  if (!handoffToken) throw new Error("This wallet authorization link is missing its handoff token.");
  const parts = handoffToken.split(".");
  if (parts.length !== 3) throw new Error("This wallet authorization link is malformed.");
  const encoded = parts[1].replace(/-/g, "+").replace(/_/g, "/");
  const claims = JSON.parse(atob(encoded.padEnd(Math.ceil(encoded.length / 4) * 4, "=")));
  if (claims.sickwallet_purpose !== "authorize" || !claims.sub || !claims.aud) {
    throw new Error("This handoff is not valid for wallet authorization.");
  }
  if (!Array.isArray(claims.sickwallet_accounts) || !claims.sickwallet_accounts.length || Number(claims.exp) * 1000 <= Date.now()) {
    throw new Error("This wallet authorization link has expired or is incomplete.");
  }
  const delegationDefaultDays = Number(claims.sickwallet_delegation_default_days || 0);
  const delegationMaxDays = Number(claims.sickwallet_delegation_max_days || 0);
  if (
    !Number.isSafeInteger(delegationDefaultDays) ||
    !Number.isSafeInteger(delegationMaxDays) ||
    delegationDefaultDays < 1 ||
    delegationDefaultDays > delegationMaxDays ||
    delegationMaxDays > 365
  ) {
    throw new Error("This wallet authorization link has an invalid delegation policy.");
  }
  return {
    purpose: claims.sickwallet_purpose,
    expires_at: Number(claims.exp),
    wallet: { accounts: claims.sickwallet_accounts },
    cdp: { project_id: claims.aud, user_id: claims.sub },
    delegation_default_days: delegationDefaultDays,
    delegation_max_days: delegationMaxDays,
  };
}

function configureAuthorization(session) {
  if (
    session.purpose !== "authorize" ||
    !authorizationControls ||
    !authorizationButton ||
    !authorizationStatus ||
    !authorizationDays ||
    !authorizationDurationHelp
  ) return;
  authorizationControls.hidden = false;
  if (!session.wallet?.accounts?.length || !session.cdp?.project_id) {
    authorizationButton.disabled = true;
    authorizationStatus.textContent = "Wallet authorization is not completely configured.";
    return;
  }
  authorizationDays.min = "1";
  authorizationDays.max = String(session.delegation_max_days);
  authorizationDays.value = String(session.delegation_default_days);
  authorizationDurationHelp.textContent =
    `Enter 1 through ${session.delegation_max_days} days. The server recommends ${session.delegation_default_days} days for fewer approvals. ` +
    "You can revoke access anytime with wallet revoke.";
  authorizationButton.addEventListener("click", async () => {
    authorizationButton.disabled = true;
    authorizationStatus.textContent = "Authenticating this wallet with Coinbase…";
    try {
      const selectedDays = Number(authorizationDays.value);
      if (
        !Number.isSafeInteger(selectedDays) || selectedDays < 1 ||
        selectedDays > session.delegation_max_days
      ) throw new Error(`Choose a whole number from 1 through ${session.delegation_max_days} days.`);
      const { authorizeWallet } = await import("./cdp-wallet.js");
      const result = await authorizeWallet(
        session.cdp.project_id,
        session.cdp.user_id,
        session.wallet.accounts,
        handoffToken,
        selectedDays,
        session.delegation_max_days
      );
      handoffToken = null;
      authorizationStatus.textContent = `Wallet delegated until ${new Date(result.expiresAt).toLocaleString()}.`;
      authorizationButton.textContent = "Wallet authorized";
    } catch (error) {
      authorizationStatus.textContent =
        error instanceof Error ? error.message : "Wallet authorization failed.";
      authorizationButton.disabled = false;
    }
  });
}

if (statusElement && detailsElement) {
  Promise.resolve().then(decodeHandoff)
    .then((session) => {
      statusElement.textContent = "Protected wallet handoff loaded.";
      addDetail("Purpose", session.purpose);
      addDetail("Approval link expires", new Date(session.expires_at * 1000).toLocaleString());
      if (session.wallet?.accounts?.length) {
        for (const account of session.wallet.accounts) {
          addDetail(account.family === "solana" ? "Solana account" : "EVM smart account", account.address);
        }
        addDetail("Authorization scope", "All wallet accounts");
      }
      if (session.transaction) {
        addDetail("Network", `${session.transaction.network_name} (${session.transaction.chain_id})`);
        addDetail("From", session.transaction.from_address);
        addDetail("To", session.transaction.to_address);
        addDetail("Value (wei)", session.transaction.value_wei);
        addDetail("Intent", session.transaction.intent_id);
      }
      detailsElement.hidden = false;
      configureAuthorization(session);
    })
    .catch((error) => {
      statusElement.textContent = error.message;
    });
}
