import test from "node:test";
import assert from "node:assert/strict";
import { validateEligibilityBinding } from "../src/polymarket-binding.js";

const now = 1_800_000_000;
function claims(changes = {}) {
  const value = {
    request_id: "q".repeat(32), result_handle: "r".repeat(32),
    discord_user_id: "123456", action: "provision",
    signer_address: "0x" + "1".repeat(40),
    account_wallet_address: "0x" + "2".repeat(40),
    created_at: now, expires_at: now + 300, chain_id: 137,
    purpose: "polymarket_eligibility", ...changes,
  };
  return {
    sub: "123456", exp: now + 300, sickwallet_discord_user: "123456",
    sickwallet_purpose: "polymarket_eligibility",
    sickwallet_polymarket_eligibility: value,
  };
}

test("accepts exact user-bound eligibility request", () => {
  const result = validateEligibilityBinding(claims(), now);
  assert.equal(result.action, "provision");
  assert.equal(validateEligibilityBinding(claims({ action: "deploy" }), now).action, "deploy");
  assert.equal(validateEligibilityBinding(claims({ action: "deposit" }), now).action, "deposit");
  assert.ok(Object.isFrozen(result));
});

test("rejects drift, extra fields, wrong identity, and expiry", () => {
  for (const input of [
    claims({ action: "trade" }), claims({ chain_id: 1 }),
    claims({ extra: true }), claims({ account_wallet_address: "0x" + "1".repeat(40) }),
    { ...claims(), sub: "654321" }, { ...claims(), exp: now },
  ]) assert.throws(() => validateEligibilityBinding(input, now), /invalid binding/);
});
