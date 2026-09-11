"use strict";

const FACTORY = "0xcba30318008035bb5a855a8684cea954d573c2c3";
const CHAIN_ID = 84532;
const status = document.querySelector("#external-status");
const details = document.querySelector("#external-details");
const controls = document.querySelector("#external-controls");
const connectButton = document.querySelector("#connect-wallet");
const deployButton = document.querySelector("#deploy-token");
const recipientInput = document.querySelector("#token-recipient");
const result = document.querySelector("#external-result");
const commandBox = document.querySelector("#verification-command");
const commandText = document.querySelector("#verification-text");
const copyButton = document.querySelector("#copy-verification");
let draft;
let signer;

function padWord(hex) { return hex.replace(/^0x/, "").padStart(64, "0"); }
function encodeText(value) {
  const bytes = new TextEncoder().encode(value);
  const hex = Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
  return padWord(bytes.length.toString(16)) + hex.padEnd(Math.ceil(bytes.length / 32) * 64, "0");
}
function calldata(recipient) {
  const name = encodeText(draft.name);
  const symbol = encodeText(draft.symbol);
  const nameOffset = 6 * 32;
  const symbolOffset = nameOffset + name.length / 2;
  return "0x8b08cf96" +
    padWord(nameOffset.toString(16)) + padWord(symbolOffset.toString(16)) +
    padWord(Number(draft.decimals).toString(16)) +
    padWord(BigInt(draft.supply_atomic).toString(16)) +
    padWord(recipient) + padWord(draft.request_id) + name + symbol;
}
function addDetail(label, value) {
  const dt = document.createElement("dt"); const dd = document.createElement("dd");
  dt.textContent = label; dd.textContent = value; details.append(dt, dd);
}
function decodePart(value) {
  const encoded = value.replace(/-/g, "+").replace(/_/g, "/");
  return JSON.parse(atob(encoded.padEnd(Math.ceil(encoded.length / 4) * 4, "=")));
}
async function verifyHandoff(token) {
  const [headerPart, claimsPart, signaturePart] = token.split(".");
  if (!signaturePart) throw new Error("This deployment link is malformed.");
  const header = decodePart(headerPart); const claims = decodePart(claimsPart);
  const jwks = await fetch("./api/jwks.php", {cache: "no-store"}).then(response => {
    if (!response.ok) throw new Error("The signing key could not be loaded.");
    return response.json();
  });
  const jwk = (jwks.keys || []).find(key => key.kid === header.kid && key.alg === "ES256");
  if (!jwk) throw new Error("This deployment link uses an unknown signing key.");
  const key = await crypto.subtle.importKey("jwk", jwk, {name: "ECDSA", namedCurve: "P-256"}, false, ["verify"]);
  const raw = signaturePart.replace(/-/g, "+").replace(/_/g, "/");
  const signature = Uint8Array.from(atob(raw.padEnd(Math.ceil(raw.length / 4) * 4, "=")), c => c.charCodeAt(0));
  const valid = await crypto.subtle.verify(
    {name: "ECDSA", hash: "SHA-256"}, key, signature,
    new TextEncoder().encode(headerPart + "." + claimsPart)
  );
  if (!valid) throw new Error("This deployment link has an invalid signature.");
  if (claims.sickwallet_purpose !== "tokenfactory_external" ||
      Number(claims.exp) * 1000 <= Date.now()) {
    throw new Error("This external-wallet deployment link is invalid or expired.");
  }
  const value = claims.sickwallet_tokenfactory;
  if (!value || Number(value.chain_id) !== CHAIN_ID ||
      !/^0x[0-9a-fA-F]{64}$/.test(value.request_id)) {
    throw new Error("This deployment link contains invalid factory parameters.");
  }
  return value;
}
async function requireChain() {
  const chain = "0x" + CHAIN_ID.toString(16);
  try {
    await window.ethereum.request({method: "wallet_switchEthereumChain", params: [{chainId: chain}]});
  } catch (error) {
    if (error && error.code === 4902) {
      await window.ethereum.request({method: "wallet_addEthereumChain", params: [{
        chainId: chain, chainName: "Base Sepolia", nativeCurrency: {name: "ETH", symbol: "ETH", decimals: 18},
        rpcUrls: ["https://sepolia.base.org"], blockExplorerUrls: ["https://sepolia.basescan.org"]
      }]});
    } else throw error;
  }
}
function normalizedRecipient() {
  const value = recipientInput.value.trim() || signer;
  if (!/^0x[0-9a-fA-F]{40}$/.test(value) || /^0x0{40}$/i.test(value)) {
    throw new Error("Enter a valid nonzero EVM recipient address.");
  }
  return value;
}

connectButton.addEventListener("click", async () => {
  try {
    if (!window.ethereum) throw new Error("Open this page in MetaMask, Trust Wallet, or another browser wallet.");
    const accounts = await window.ethereum.request({method: "eth_requestAccounts"});
    if (!accounts || !accounts[0]) throw new Error("The wallet did not provide an account.");
    await requireChain(); signer = accounts[0]; recipientInput.placeholder = signer;
    connectButton.textContent = "Connected: " + signer.slice(0, 8) + "…" + signer.slice(-6);
    deployButton.disabled = false; result.textContent = "Signer and gas payer: " + signer;
  } catch (error) { result.textContent = error instanceof Error ? error.message : "Wallet connection failed."; }
});

deployButton.addEventListener("click", async () => {
  deployButton.disabled = true;
  try {
    await requireChain();
    const recipient = normalizedRecipient();
    const ok = window.confirm(
      `Deploy ${draft.name} (${draft.symbol}) with fixed supply ${draft.supply_display}?

` +
      `Signer / gas payer: ${signer}
Recipient: ${recipient}
Network: Base Sepolia`
    );
    if (!ok) { deployButton.disabled = false; return; }
    result.textContent = "Confirm the transaction in your wallet…";
    const txHash = await window.ethereum.request({method: "eth_sendTransaction", params: [{
      from: signer, to: FACTORY, value: "0x0", data: calldata(recipient)
    }]});
    commandText.value = `!tokenfactory deployment ${txHash} ${recipient}`;
    commandBox.hidden = false;
    result.textContent = "Transaction submitted: " + txHash + ". Wait for confirmation, then run the command below in Discord.";
  } catch (error) {
    result.textContent = error instanceof Error ? error.message : "External deployment failed.";
    deployButton.disabled = false;
  }
});
copyButton.addEventListener("click", async () => {
  await navigator.clipboard.writeText(commandText.value);
  copyButton.textContent = "Copied";
});

(async () => {
  try {
    const fragment = new URLSearchParams(location.hash.slice(1));
    const token = fragment.get("handoff"); history.replaceState(null, "", location.pathname);
    if (!token) throw new Error("This deployment link is missing its handoff token.");
    draft = await verifyHandoff(token);
    const scale = 10n ** BigInt(draft.decimals);
    const whole = BigInt(draft.supply_atomic) / scale;
    const remainder = BigInt(draft.supply_atomic) % scale;
    draft.supply_display = String(whole) + (remainder ? "." + String(remainder).padStart(draft.decimals, "0").replace(/0+$/, "") : "");
    addDetail("Token", `${draft.name} (${draft.symbol})`);
    addDetail("Fixed supply", draft.supply_display);
    addDetail("Decimals", String(draft.decimals));
    addDetail("Network", "Base Sepolia (84532)");
    addDetail("Factory", FACTORY);
    addDetail("Recipient", "Signing wallet unless you enter another address");
    details.hidden = false; controls.hidden = false;
    status.textContent = "Protected external-wallet deployment loaded.";
  } catch (error) { status.textContent = error instanceof Error ? error.message : "The deployment link could not be loaded."; }
})();
