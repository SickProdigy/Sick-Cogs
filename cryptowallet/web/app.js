"use strict";

document.documentElement.dataset.walletUi = "ready";

const PRODUCT = "cryptowallet";
const TERMS_VERSION = "2026-09-30.1";
const statusElement = document.querySelector("#session-status");
const detailsElement = document.querySelector("#session-details");
const authorizationControls = document.querySelector("#authorization-controls");
const authorizationButton = document.querySelector("#authorize-wallet");
const authorizationStatus = document.querySelector("#authorization-status");
const authorizationDays = document.querySelector("#authorization-days");
const authorizationDurationHelp = document.querySelector("#authorization-duration-help");
const mainnetSetupControls = document.querySelector("#mainnet-setup-controls");
const mainnetTermsUnderstood = document.querySelector("#mainnet-terms-understood");
let handoffToken = null;
let termsSubmitted = false;

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
  if (!Number.isSafeInteger(delegationDefaultDays) || !Number.isSafeInteger(delegationMaxDays)
      || delegationDefaultDays < 1 || delegationDefaultDays > delegationMaxDays
      || delegationMaxDays > 365) {
    throw new Error("This wallet authorization link has an invalid delegation policy.");
  }
  let terms = null;
  if (claims.sickwallet_terms !== undefined) {
    terms = claims.sickwallet_terms;
    if (!terms || Object.keys(terms).sort().join(",") !== "product,result_handle,version"
        || terms.product !== PRODUCT || terms.version !== TERMS_VERSION
        || !/^[A-Za-z0-9_-]{32,128}$/.test(terms.result_handle || "")) {
      throw new Error("This mainnet setup has an invalid terms binding.");
    }
  }
  return {
    purpose: claims.sickwallet_purpose,
    expires_at: Number(claims.exp),
    wallet: { accounts: claims.sickwallet_accounts },
    cdp: { project_id: claims.aud, user_id: claims.sub },
    delegation_default_days: delegationDefaultDays,
    delegation_max_days: delegationMaxDays,
    terms,
  };
}

async function loadSession() {
  if (!window.location.hash.includes("handoff=")) {
    throw new Error("This protected wallet authorization link is missing its handoff token.");
  }
  const candidate = new URLSearchParams(window.location.hash.slice(1)).get("handoff") || "";
  if (candidate.split(".").length === 3) return decodeHandoff();
  throw new Error("Protected external-wallet handoff loading below.");
}

async function submitTerms(binding) {
  if (termsSubmitted) return;
  const response = await fetch("./api/wallet-terms.php", {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({
      operation: "submit", handoff: binding.result_handle,
      product: PRODUCT, version: TERMS_VERSION,
    }),
  });
  const result = await response.json().catch(() => null);
  if (!response.ok || result?.status !== "submitted") {
    throw new Error(result?.error?.message || "Terms acceptance could not be submitted.");
  }
  termsSubmitted = true;
  mainnetTermsUnderstood.disabled = true;
}

function configureAuthorization(session) {
  if (session.purpose !== "authorize" || !authorizationControls || !authorizationButton
      || !authorizationStatus || !authorizationDays || !authorizationDurationHelp) return;
  authorizationControls.hidden = false;
  if (!session.wallet?.accounts?.length || !session.cdp?.project_id) {
    authorizationButton.disabled = true;
    authorizationStatus.textContent = "Wallet authorization is not completely configured.";
    return;
  }
  if (session.terms) {
    mainnetSetupControls.hidden = false;
    authorizationButton.textContent = "Continue mainnet setup";
    authorizationButton.disabled = true;
    mainnetTermsUnderstood.addEventListener("change", () => {
      authorizationButton.disabled = !mainnetTermsUnderstood.checked;
    });
  }
  authorizationDays.min = "1";
  authorizationDays.max = String(session.delegation_max_days);
  authorizationDays.value = String(session.delegation_default_days);
  authorizationDurationHelp.textContent =
    `Enter 1 through ${session.delegation_max_days} days. The server recommends ${session.delegation_default_days} days for fewer approvals. ` +
    "You can revoke access anytime with wallet revoke.";
  authorizationButton.addEventListener("click", async () => {
    authorizationButton.disabled = true;
    authorizationStatus.textContent = session.terms
      ? "Recording terms acceptance and authorizing this wallet…"
      : "Authenticating this wallet with Coinbase…";
    try {
      if (session.terms && !mainnetTermsUnderstood.checked && !termsSubmitted) {
        throw new Error("Review and accept the CryptoWallet terms first.");
      }
      const selectedDays = Number(authorizationDays.value);
      if (!Number.isSafeInteger(selectedDays) || selectedDays < 1
          || selectedDays > session.delegation_max_days) {
        throw new Error(`Choose a whole number from 1 through ${session.delegation_max_days} days.`);
      }
      if (session.terms) await submitTerms(session.terms);
      const { authorizeWallet } = await import("./cdp-wallet.js");
      const result = await authorizeWallet(
        session.cdp.project_id, session.cdp.user_id, session.wallet.accounts,
        handoffToken, selectedDays, session.delegation_max_days
      );
      handoffToken = null;
      authorizationStatus.textContent = session.terms
        ? `Mainnet wallet setup submitted. Authorization lasts until ${new Date(result.expiresAt).toLocaleString()}. Return to Discord and press Confirm setup.`
        : `Wallet delegated until ${new Date(result.expiresAt).toLocaleString()}.`;
      authorizationButton.textContent = session.terms ? "Mainnet setup submitted" : "Wallet authorized";
    } catch (error) {
      authorizationStatus.textContent = error instanceof Error ? error.message : "Wallet authorization failed.";
      authorizationButton.disabled = false;
    }
  });
}

if (statusElement && detailsElement) {
  Promise.resolve().then(loadSession).then((session) => {
    statusElement.textContent = session.terms
      ? "Protected mainnet wallet setup loaded."
      : "Protected wallet authorization loaded.";
    addDetail("Purpose", session.terms ? "Mainnet wallet setup" : session.purpose);
    addDetail("Approval link expires", new Date(session.expires_at * 1000).toLocaleString());
    if (session.terms) addDetail("CryptoWallet terms", session.terms.version);
    if (session.wallet?.accounts?.length) {
      for (const account of session.wallet.accounts) {
        addDetail(account.family === "solana" ? "Solana account" : "EVM smart account", account.address);
      }
      addDetail("Authorization scope", "All wallet accounts");
    }
    detailsElement.hidden = false;
    configureAuthorization(session);
  }).catch((error) => { statusElement.textContent = error.message; });
}
