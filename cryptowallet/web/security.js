"use strict";

const statusElement = document.querySelector("#security-status");
const controlsElement = document.querySelector("#security-controls");
const secretElement = document.querySelector("#totp-secret");
const copyButton = document.querySelector("#copy-totp-secret");
const copyStatus = document.querySelector("#copy-totp-status");
let secretText = "";

function decodeClaims(token) {
  const parts = token.split(".");
  if (parts.length !== 3) throw new Error("This enrollment handoff is malformed.");
  const encoded = parts[1].replace(/-/g, "+").replace(/_/g, "/");
  return JSON.parse(atob(encoded.padEnd(Math.ceil(encoded.length / 4) * 4, "=")));
}

function base32(bytes) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let output = "";
  let buffer = 0;
  let bits = 0;
  for (const value of bytes) {
    buffer = (buffer << 8) | value;
    bits += 8;
    while (bits >= 5) {
      output += alphabet[(buffer >>> (bits - 5)) & 31];
      bits -= 5;
    }
  }
  if (bits) output += alphabet[(buffer << (5 - bits)) & 31];
  return output;
}

function base64url(bytes) {
  let binary = "";
  for (const value of bytes) binary += String.fromCharCode(value);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

async function consumeHandoff() {
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const handle = fragment.get("handoff");
  history.replaceState(null, "", window.location.pathname + window.location.search);
  if (!handle || !/^[A-Za-z0-9_-]{32,128}$/.test(handle)) {
    throw new Error("This enrollment link is invalid, expired, or already used.");
  }
  const response = await fetch("./api/recovery-handoff.php", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ operation: "consume", handoff: handle }),
  });
  const result = await response.json().catch(() => null);
  if (!response.ok || result?.status !== "consumed" || typeof result.jwt !== "string") {
    throw new Error(result?.error?.message || "This enrollment link is invalid, expired, or already used.");
  }
  const claims = decodeClaims(result.jwt);
  const enrollment = claims.sickwallet_totp;
  if (
    claims.sickwallet_purpose !== "totp_enroll"
    || Number(claims.exp) * 1000 <= Date.now()
    || !enrollment
    || enrollment.version !== 1
    || typeof enrollment.profile_id !== "string"
    || !/^[A-Za-z0-9_-]{32,128}$/.test(enrollment.result_handle || "")
    || enrollment.public_jwk?.kty !== "RSA"
    || enrollment.public_jwk?.alg !== "RSA-OAEP-256"
  ) {
    throw new Error("This enrollment handoff is expired or incomplete.");
  }
  return enrollment;
}

async function createAndSubmitEnrollment(enrollment) {
  const key = await crypto.subtle.importKey(
    "jwk",
    enrollment.public_jwk,
    { name: "RSA-OAEP", hash: "SHA-256" },
    false,
    ["encrypt"]
  );
  const secretBytes = crypto.getRandomValues(new Uint8Array(20));
  const generatedSecret = base32(secretBytes);
  const plaintext = new TextEncoder().encode(generatedSecret);
  const label = new TextEncoder().encode("cryptowallet-totp-enrollment-v1");
  const encrypted = await crypto.subtle.encrypt(
    { name: "RSA-OAEP", label },
    key,
    plaintext
  );
  plaintext.fill(0);
  secretBytes.fill(0);
  const response = await fetch("./api/totp-enrollment.php", {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({
      operation: "submit",
      handoff: enrollment.result_handle,
      ciphertext: base64url(new Uint8Array(encrypted)),
    }),
  });
  const result = await response.json().catch(() => null);
  if (!response.ok || result?.status !== "submitted") {
    throw new Error(result?.error?.message || "Encrypted enrollment could not be submitted.");
  }
  secretText = generatedSecret;
  secretElement.textContent = secretText.match(/.{1,4}/g).join(" ");
  controlsElement.hidden = false;
  statusElement.textContent = "Encrypted setup is ready. Add the key to your authenticator, then finish in Discord.";
}

copyButton.addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(secretText);
    copyStatus.textContent = "Setup key copied. Clear your clipboard after adding it.";
  } catch {
    copyStatus.textContent = "Copy failed. Select the setup key manually.";
  }
});

window.addEventListener("pagehide", () => {
  secretText = "";
  secretElement.textContent = "";
});

Promise.resolve()
  .then(consumeHandoff)
  .then(createAndSubmitEnrollment)
  .catch((error) => {
    statusElement.textContent = error instanceof Error
      ? error.message
      : "Authenticator setup is unavailable.";
  });
