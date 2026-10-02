import assert from "node:assert/strict";
import test from "node:test";

import { validateGeoblock, validatePolymarketBinding } from "../src/polymarket-binding.js";

function claims() {
  const signer = "0x" + "1".repeat(40);
  const connectionId = "connection-one";
  return {
    sub: "228710499888398336", exp: 401,
    sickwallet_discord_user: "228710499888398336",
    sickwallet_purpose: "polymarket_connect",
    sickwallet_polymarket: {
      connection_id: connectionId, result_handle: "r".repeat(32),
      discord_user_id: "228710499888398336", signer_address: signer,
      account_wallet_address: signer, wallet_type: "EOA", challenge: "h".repeat(32),
      created_at: 100, expires_at: 400, chain_id: 137, purpose: "polymarket_connect",
    },
  };
}

test("accepts an exact current Polymarket connection binding", () => {
  assert.equal(validatePolymarketBinding(claims(), 150).chain_id, 137);
});

test("rejects field drift, expiry, and inconsistent EOA identity", () => {
  const extra = claims(); extra.sickwallet_polymarket.extra = true;
  assert.throws(() => validatePolymarketBinding(extra, 150), /invalid account binding/);
  assert.throws(() => validatePolymarketBinding(claims(), 400), /expired/);
  const mismatch = claims(); mismatch.sickwallet_polymarket.account_wallet_address = "0x" + "2".repeat(40);
  assert.throws(() => validatePolymarketBinding(mismatch, 150), /invalid account binding/);
});

test("accepts only the exact official geoblock response shape", () => {
  assert.equal(validateGeoblock({ blocked: false, ip: "203.0.113.4", country: "IE", region: "" }).country, "IE");
  assert.throws(() => validateGeoblock({ blocked: false, ip: "x", country: "IR", region: "", extra: 1 }));
  assert.throws(() => validateGeoblock({ blocked: "false", ip: "x", country: "IE", region: "" }));
});
