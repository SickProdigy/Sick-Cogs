"use strict";

const statusElement = document.querySelector("#recovery-status");
const detailsElement = document.querySelector("#recovery-details");
const emailForm = document.querySelector("#recovery-email-form");
const emailInput = document.querySelector("#recovery-email");
const codeForm = document.querySelector("#recovery-code-form");
const codeInput = document.querySelector("#recovery-code");
let handoffToken = null;
let recoverySession = null;
let flowId = null;

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
  if (!claims.sickwallet_address || Number(claims.exp) * 1000 <= Date.now()) {
    throw new Error("This wallet recovery link has expired or is incomplete.");
  }
  return {
    expiresAt: Number(claims.exp),
    projectId: claims.aud,
    userId: claims.sub,
    address: claims.sickwallet_address,
  };
}

emailForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = emailForm.querySelector("button");
  button.disabled = true;
  statusElement.textContent = "Authenticating your wallet and requesting a verification code…";
  try {
    const { beginRecoveryEnrollment } = await import("./cdp-wallet.js");
    const result = await beginRecoveryEnrollment(
      recoverySession.projectId,
      recoverySession.userId,
      recoverySession.address,
      handoffToken,
      emailInput.value.trim()
    );
    handoffToken = null;
    flowId = result.flowId;
    emailForm.hidden = true;
    codeForm.hidden = false;
    codeInput.focus();
    statusElement.textContent = "Coinbase sent a verification code. Enter it below.";
  } catch (error) {
    statusElement.textContent = error instanceof Error ? error.message : "Recovery enrollment could not start.";
    button.disabled = false;
  }
});

codeForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = codeForm.querySelector("button");
  button.disabled = true;
  statusElement.textContent = "Verifying the recovery email…";
  try {
    const { completeRecoveryEnrollment } = await import("./cdp-wallet.js");
    await completeRecoveryEnrollment(flowId, codeInput.value.trim(), recoverySession.userId);
    codeInput.value = "";
    flowId = null;
    codeForm.hidden = true;
    statusElement.textContent = "Recovery email verified. You can now use it as an independent sign-in method for this Coinbase wallet.";
  } catch (error) {
    statusElement.textContent = error instanceof Error ? error.message : "The recovery code could not be verified.";
    button.disabled = false;
  }
});

Promise.resolve().then(decodeRecoveryHandoff)
  .then((session) => {
    recoverySession = session;
    statusElement.textContent = "Protected recovery handoff loaded.";
    addDetail("Wallet", session.address);
    addDetail("Expires", new Date(session.expiresAt * 1000).toLocaleString());
    detailsElement.hidden = false;
    emailForm.hidden = false;
  })
  .catch((error) => {
    statusElement.textContent = error instanceof Error ? error.message : "Wallet recovery is unavailable.";
  });
