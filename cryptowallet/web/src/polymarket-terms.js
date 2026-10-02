"use strict";

const PRODUCT = "polymarket";
const VERSION = "2026-10-02.1";
const statusElement = document.querySelector("#terms-status");
const controlsElement = document.querySelector("#terms-controls");
const checkbox = document.querySelector("#terms-understood");
const acceptButton = document.querySelector("#accept-terms");
let binding = null;

function decodeClaims(token) {
  const parts = token.split(".");
  if (parts.length !== 3) throw new Error("This terms handoff is malformed.");
  const encoded = parts[1].replace(/-/g, "+").replace(/_/g, "/");
  return JSON.parse(atob(encoded.padEnd(Math.ceil(encoded.length / 4) * 4, "=")));
}

async function consumeHandoff() {
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const handle = fragment.get("handoff");
  history.replaceState(null, "", window.location.pathname + window.location.search);
  if (!handle) {
    statusElement.textContent = "This is a reference copy. Viewing it does not accept the terms.";
    return null;
  }
  if (!/^[A-Za-z0-9_-]{32,128}$/.test(handle)) {
    throw new Error("This terms link is invalid, expired, or already used.");
  }
  const response = await fetch("./api/recovery-handoff.php", {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ operation: "consume", handoff: handle }),
  });
  const result = await response.json().catch(() => null);
  if (!response.ok || result?.status !== "consumed" || typeof result.jwt !== "string") {
    throw new Error(result?.error?.message || "This terms link is invalid, expired, or already used.");
  }
  const claims = decodeClaims(result.jwt);
  const terms = claims.sickwallet_polymarket_terms;
  const userId = String(claims.sickwallet_discord_user || "");
  if (claims.sickwallet_purpose !== "polymarket_terms"
      || Number(claims.exp) * 1000 <= Date.now()
      || String(claims.sub) !== userId || !/^[1-9][0-9]{5,24}$/.test(userId)
      || !terms || Object.keys(terms).sort().join(",") !== "product,result_handle,version"
      || terms.product !== PRODUCT || terms.version !== VERSION
      || !/^[A-Za-z0-9_-]{32,128}$/.test(terms.result_handle || "")) {
    throw new Error("This terms handoff is expired or has an invalid account binding.");
  }
  return terms;
}

acceptButton.addEventListener("click", async () => {
  if (!binding || !checkbox.checked) return;
  acceptButton.disabled = true;
  try {
    const response = await fetch("./api/polymarket-terms.php", {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ operation: "submit", handoff: binding.result_handle,
        product: PRODUCT, version: VERSION }),
    });
    const result = await response.json().catch(() => null);
    if (!response.ok || result?.status !== "submitted") {
      throw new Error(result?.error?.message || "Acceptance could not be submitted.");
    }
    checkbox.disabled = true;
    statusElement.textContent = "Acceptance submitted. Return to Discord and press `poly termsconfirm`.";
  } catch (error) {
    acceptButton.disabled = false;
    statusElement.textContent = error instanceof Error ? error.message : "Acceptance is unavailable.";
  }
});
checkbox.addEventListener("change", () => { acceptButton.disabled = !checkbox.checked; });

Promise.resolve().then(consumeHandoff).then((terms) => {
  if (!terms) return;
  binding = terms;
  controlsElement.hidden = false;
  statusElement.textContent = "Review the terms, check the acknowledgment, and submit your acceptance.";
}).catch((error) => {
  statusElement.textContent = error instanceof Error ? error.message : "Acceptance is unavailable.";
});
