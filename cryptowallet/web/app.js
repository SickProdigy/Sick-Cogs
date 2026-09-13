"use strict";

document.documentElement.dataset.walletUi = "ready";

const statusElement = document.querySelector("#session-status");
const detailsElement = document.querySelector("#session-details");
const authorizationControls = document.querySelector("#authorization-controls");
const authorizationButton = document.querySelector("#authorize-wallet");
const authorizationStatus = document.querySelector("#authorization-status");
const authorizationDays = document.querySelector("#authorization-days");
const authorizationDurationHelp = document.querySelector("#authorization-duration-help");
const clankerControls = document.querySelector("#clanker-controls");
const clankerApprove = document.querySelector("#approve-clanker");
const clankerReject = document.querySelector("#reject-clanker");
const clankerStatus = document.querySelector("#clanker-status");
const noticeElement = document.querySelector("#session-notice");
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

async function loadSession() {
  if (window.location.hash.includes("handoff=")) return decodeHandoff();
  const response = await fetch("api/session.php", {
    method: "GET",
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok || !body.data) {
    throw new Error(body.error?.message || "This protected wallet session is unavailable.");
  }
  return body.data;
}

function formatWei(value) {
  try {
    const wei = BigInt(value);
    const whole = wei / 1000000000000000000n;
    let fraction = (wei % 1000000000000000000n).toString().padStart(18, "0");
    while (fraction.endsWith("0")) fraction = fraction.slice(0, -1);
    return fraction ? String(whole) + "." + fraction + " ETH" : String(whole) + " ETH";
  } catch {
    return String(value) + " wei";
  }
}

function renderClanker(clanker) {
  addDetail("Network", clanker.network_name + " (" + clanker.chain_id + ")");
  addDetail("Signing wallet", clanker.wallet_address);
  addDetail("Clanker factory", clanker.factory);
  addDetail("Token", clanker.token.name + " ($" + clanker.token.symbol + ")");
  addDetail("Token administrator", clanker.token.admin);
  addDetail("Image", clanker.token.image || "None");
  addDetail("Metadata", JSON.stringify(clanker.token.metadata || {}));
  addDetail("Context", JSON.stringify(clanker.token.context || {}));
  addDetail("Pool pair", clanker.pool.paired_token);
  addDetail("Pool ticks", "start " + clanker.pool.tick_if_token0_is_clanker + "; spacing " + clanker.pool.tick_spacing);
  addDetail("Pool positions", clanker.pool.positions.map((item) =>
    item.tick_lower + " to " + item.tick_upper + ": " + item.position_bps + " bps"
  ).join("; "));
  addDetail("Pool fees", clanker.pool.fees.type + "; token " + clanker.pool.fees.clanker_bps + " bps; pair " + clanker.pool.fees.paired_bps + " bps");
  clanker.rewards.forEach((reward, index) => {
    addDetail("Reward " + (index + 1), reward.bps + " bps in " + reward.token + "; recipient " + reward.recipient + "; admin " + reward.admin);
  });
  addDetail("Vault", clanker.vault ? clanker.vault.percentage + "% to " + clanker.vault.recipient + "; lock " + clanker.vault.lockup_seconds + "s; vest " + clanker.vault.vesting_seconds + "s" : "None");
  if (clanker.airdrop) {
    addDetail("Airdrop", clanker.airdrop.amount_tokens + " tokens; admin " + clanker.airdrop.admin + "; lock " + clanker.airdrop.lockup_seconds + "s; vest " + clanker.airdrop.vesting_seconds + "s");
    addDetail("Airdrop Merkle root", clanker.airdrop.merkle_root);
  } else {
    addDetail("Airdrop", "None");
  }
  addDetail("Expected native value", formatWei(clanker.expected_native_value_wei));
  addDetail("Estimated gas fee", formatWei(clanker.estimated_gas_fee_wei));
  addDetail("Payload hash", clanker.payload_hash);
  addDetail("Intent", clanker.intent_id);
  addDetail("Intent expires", new Date(clanker.expires_at * 1000).toLocaleString());
  if (noticeElement) {
    noticeElement.textContent = "Review every field carefully. This Base Sepolia prototype view does not submit a transaction by itself.";
  }
}

async function clankerAction(action) {
  const response = await fetch("api/clanker.php", {method: "POST", credentials: "same-origin",
    headers: {"Content-Type": "application/json", Accept: "application/json"},
    body: JSON.stringify({action})});
  const body = await response.json().catch(() => ({}));
  if (!response.ok || !body.data) throw new Error(body.error?.message || "The Clanker operation could not be processed.");
  return body.data;
}
function configureClanker(session) {
  if (!session.clanker || !clankerControls || !clankerApprove || !clankerReject || !clankerStatus) return;
  clankerControls.hidden = false;
  clankerApprove.addEventListener("click", async () => {
    clankerApprove.disabled = true; clankerReject.disabled = true; clankerStatus.textContent = "Submitting the exact reviewed operation…";
    try { const value = await clankerAction("approve"); clankerStatus.textContent = "Clanker deployment status: " + value.status + (value.transaction_hash ? "; transaction " + value.transaction_hash : ""); }
    catch (error) { clankerStatus.textContent = error instanceof Error ? error.message : "Clanker approval failed."; }
  });
  clankerReject.addEventListener("click", async () => {
    clankerApprove.disabled = true; clankerReject.disabled = true;
    try { await clankerAction("reject"); clankerStatus.textContent = "Clanker launch rejected. No transaction was submitted."; }
    catch (error) { clankerStatus.textContent = error instanceof Error ? error.message : "Clanker rejection failed."; }
  });
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
  Promise.resolve().then(loadSession)
    .then((session) => {
      statusElement.textContent = session.clanker ? "Protected Clanker deployment review loaded." : "Protected wallet handoff loaded.";
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
      if (session.clanker) renderClanker(session.clanker);
      configureClanker(session);
      detailsElement.hidden = false;
      configureAuthorization(session);
    })
    .catch((error) => {
      statusElement.textContent = error.message;
    });
}
