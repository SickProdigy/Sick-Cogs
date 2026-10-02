"use strict";

import { createWalletClient, custom } from "viem";
import { polygon } from "viem/chains";
import { validateGeoblock, validatePolymarketBinding } from "./polymarket-binding.js";

const statusElement = document.querySelector("#polymarket-status");
const controlsElement = document.querySelector("#polymarket-controls");
const connectButton = document.querySelector("#connect-polymarket");
const signerElement = document.querySelector("#polymarket-signer");
const walletElement = document.querySelector("#polymarket-wallet");
const typeElement = document.querySelector("#polymarket-wallet-type");
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
  if (!handle || !/^[A-Za-z0-9_-]{32,128}$/.test(handle)) {
    throw new Error("This Polymarket link is invalid, expired, or already used.");
  }
  const response = await fetch("./api/recovery-handoff.php", {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ operation: "consume", handoff: handle }),
  });
  const result = await response.json().catch(() => null);
  if (!response.ok || result?.status !== "consumed" || typeof result.jwt !== "string") {
    throw new Error(result?.error?.message || "This Polymarket link is invalid, expired, or already used.");
  }
  return validatePolymarketBinding(decodeClaims(result.jwt), Math.floor(Date.now() / 1000));
}

async function checkEligibility() {
  const response = await fetch("https://polymarket.com/api/geoblock", {
    method: "GET", mode: "cors", credentials: "omit", cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new Error("Polymarket eligibility could not be verified.");
  return validateGeoblock(await response.json());
}

async function authNonce(challenge) {
  const digest = new Uint8Array(await crypto.subtle.digest(
    "SHA-256", new TextEncoder().encode(challenge),
  ));
  return BigInt("0x" + Array.from(digest, (value) => value.toString(16).padStart(2, "0")).join(""));
}

async function submitResult(signature) {
  const response = await fetch("./api/polymarket-connect.php", {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({
      operation: "submit", handoff: binding.result_handle, signature,
      blocked: eligibility.blocked, ip: eligibility.ip,
      country: eligibility.country, region: eligibility.region,
      checked_at: Math.floor(Date.now() / 1000),
    }),
  });
  const result = await response.json().catch(() => null);
  if (!response.ok || result?.status !== "submitted") {
    throw new Error(result?.error?.message || "The Polymarket result could not be submitted.");
  }
}

connectButton.addEventListener("click", async () => {
  if (!binding || !eligibility || eligibility.blocked) return;
  connectButton.disabled = true;
  try {
    if (!window.ethereum) throw new Error("No compatible browser wallet was found.");
    const walletClient = createWalletClient({ chain: polygon, transport: custom(window.ethereum) });
    await walletClient.switchChain({ id: polygon.id });
    const [account] = await walletClient.requestAddresses();
    if (!account || account.toLowerCase() !== binding.signer_address) {
      throw new Error("The connected wallet is not the requested Polymarket signer.");
    }
    const signature = await walletClient.signTypedData({
      account,
      domain: { name: "ClobAuthDomain", version: "1", chainId: 137 },
      types: { ClobAuth: [
        { name: "address", type: "address" }, { name: "timestamp", type: "string" },
        { name: "nonce", type: "uint256" }, { name: "message", type: "string" },
      ] },
      primaryType: "ClobAuth",
      message: {
        address: binding.signer_address, timestamp: String(binding.created_at),
        nonce: await authNonce(binding.challenge),
        message: "This message attests that I control the given wallet",
      },
    });
    await submitResult(signature);
    controlsElement.hidden = true;
    statusElement.textContent = "Account proof submitted. Return to Discord and confirm the connection.";
  } catch (error) {
    connectButton.disabled = false;
    statusElement.textContent = error instanceof Error ? error.message : "Polymarket connection is unavailable.";
  }
});

Promise.resolve().then(consumeHandoff).then(async (value) => {
  binding = value;
  signerElement.textContent = binding.signer_address;
  walletElement.textContent = binding.account_wallet_address;
  typeElement.textContent = binding.wallet_type;
  eligibility = await checkEligibility();
  if (eligibility.blocked) {
    await submitResult(null);
    statusElement.textContent = `Polymarket reports this location (${eligibility.country}${eligibility.region ? "-" + eligibility.region : ""}) as unavailable.`;
    return;
  }
  controlsElement.hidden = false;
  statusElement.textContent = "Eligibility confirmed. Connect the exact signer wallet to prove account control.";
}).catch((error) => {
  statusElement.textContent = error instanceof Error ? error.message : "Polymarket connection is unavailable.";
});
