"use strict";

const ADDRESS = /^0x[0-9a-f]{40}$/;
const IDENTIFIER = /^[A-Za-z0-9_-]{1,128}$/;
const HANDLE = /^[A-Za-z0-9_-]{32,128}$/;
const WALLET_TYPES = new Set(["EOA", "POLY_PROXY", "GNOSIS_SAFE", "DEPOSIT_WALLET"]);
const EXPECTED_KEYS = [
  "account_wallet_address", "chain_id", "challenge", "connection_id", "created_at",
  "discord_user_id", "expires_at", "purpose", "result_handle", "signer_address", "wallet_type",
];

export function validatePolymarketBinding(claims, nowSeconds) {
  const binding = claims?.sickwallet_polymarket;
  const userId = String(claims?.sickwallet_discord_user || "");
  if (claims?.sickwallet_purpose !== "polymarket_connect"
      || String(claims?.sub) !== userId || !/^[1-9][0-9]{5,24}$/.test(userId)
      || Number(claims?.exp) <= nowSeconds || !binding
      || Object.keys(binding).sort().join(",") !== EXPECTED_KEYS.join(",")
      || binding.purpose !== "polymarket_connect" || binding.chain_id !== 137
      || String(binding.discord_user_id) !== userId
      || !IDENTIFIER.test(binding.connection_id) || !HANDLE.test(binding.result_handle)
      || !HANDLE.test(binding.challenge) || !WALLET_TYPES.has(binding.wallet_type)
      || !ADDRESS.test(binding.signer_address) || !ADDRESS.test(binding.account_wallet_address)
      || !Number.isSafeInteger(binding.created_at) || !Number.isSafeInteger(binding.expires_at)
      || binding.expires_at !== binding.created_at + 300 || binding.expires_at <= nowSeconds
      || (binding.wallet_type === "EOA") !== (binding.signer_address === binding.account_wallet_address)) {
    throw new Error("This Polymarket handoff is expired or has an invalid account binding.");
  }
  return Object.freeze({ ...binding });
}

export function validateGeoblock(result) {
  if (!result || Object.keys(result).sort().join(",") !== "blocked,country,ip,region"
      || typeof result.blocked !== "boolean"
      || typeof result.ip !== "string" || result.ip.length > 64
      || !/^[A-Z]{2}$/.test(result.country)
      || typeof result.region !== "string" || !/^[A-Z0-9-]{0,16}$/.test(result.region)) {
    throw new Error("Polymarket eligibility could not be verified.");
  }
  return Object.freeze({ ...result });
}
