"use strict";

import { validateEligibilityBinding, validateGeoblock } from "./polymarket-binding.js";

const HANDLE = /^[A-Za-z0-9_-]{32,128}$/;
const statusElement = document.querySelector("#polymarket-status");
const controlsElement = document.querySelector("#polymarket-controls");
const confirmButton = document.querySelector("#confirm-polymarket-eligibility");
let binding = null;
let eligibility = null;

function decodeClaims(token) {
  const parts = token.split(".");
  if (parts.length !== 3) throw new Error("This Polymarket handoff is malformed.");
  const encoded = parts[1].replace(/-/g, "+").replace(/_/g, "/");
  return JSON.parse(atob(encoded.padEnd(Math.ceil(encoded.length / 4) * 4, "=")));
}

async function consumeHandoff() {
  const handle = new URLSearchParams(window.location.hash.slice(1)).get("handoff");
  history.replaceState(null, "", window.location.pathname + window.location.search);
  if (!handle || !HANDLE.test(handle)) throw new Error("This eligibility link is invalid, expired, or already used.");
  const response = await fetch("./api/recovery-handoff.php", {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ operation: "consume", handoff: handle }),
  });
  const result = await response.json().catch(() => null);
  if (!response.ok || result?.status !== "consumed" || typeof result.jwt !== "string") {
    throw new Error(result?.error?.message || "This eligibility link is invalid, expired, or already used.");
  }
  return validateEligibilityBinding(decodeClaims(result.jwt), Math.floor(Date.now() / 1000));
}

async function checkEligibility() {
  const response = await fetch("https://polymarket.com/api/geoblock", {
    method: "GET", mode: "cors", credentials: "omit", cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new Error("Polymarket eligibility could not be verified.");
  return validateGeoblock(await response.json());
}

async function submitResult() {
  const response = await fetch("./api/polymarket-eligibility.php", {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({
      operation: "submit", handoff: binding.result_handle, blocked: eligibility.blocked,
      ip: eligibility.ip, country: eligibility.country, region: eligibility.region,
      checked_at: Math.floor(Date.now() / 1000),
    }),
  });
  const result = await response.json().catch(() => null);
  if (!response.ok || result?.status !== "submitted") {
    throw new Error(result?.error?.message || "Eligibility could not be submitted.");
  }
}

confirmButton.addEventListener("click", async () => {
  if (!binding || !eligibility) return;
  confirmButton.disabled = true;
  try {
    await submitResult();
    controlsElement.hidden = true;
    statusElement.textContent = eligibility.blocked
      ? "This location is unavailable for Polymarket trading."
      : "Eligibility confirmed. Return to Discord to continue.";
  } catch (error) {
    confirmButton.disabled = false;
    statusElement.textContent = error instanceof Error ? error.message : "Eligibility is unavailable.";
  }
});

Promise.resolve().then(consumeHandoff).then(async (value) => {
  binding = value;
  document.querySelector("#polymarket-action").textContent = binding.action === "deploy" ? "Create Deposit Wallet"
    : binding.action === "rotate" ? "Renew session authorization"
      : binding.action === "deposit" ? "Fund Polymarket account"
      : binding.action === "buy" ? "Check buy eligibility"
        : binding.action === "sell" ? "Check sell eligibility"
          : binding.action === "claim" ? "Check claim eligibility"
            : binding.action === "withdraw" ? "Check withdrawal eligibility"
              : "Set up session authorization";
  document.querySelector("#polymarket-signer").textContent = binding.signer_address;
  document.querySelector("#polymarket-wallet").textContent = binding.account_wallet_address;
  eligibility = await checkEligibility();
  controlsElement.hidden = false;
  statusElement.textContent = eligibility.blocked
    ? `Polymarket reports this location (${eligibility.country}${eligibility.region ? "-" + eligibility.region : ""}) as unavailable. Confirm to return the result.`
    : "Eligibility passed. Confirm this result to continue in Discord.";
}).catch((error) => {
  statusElement.textContent = error instanceof Error ? error.message : "Eligibility is unavailable.";
});
