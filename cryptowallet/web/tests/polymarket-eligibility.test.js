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

test("accepts exact public buy and sell intent bindings", () => {
  const trade = {
    session_address: "0x" + "3".repeat(40),
    market_path: "/markets/slug/example", outcome: "yes",
  };
  const buy = validateEligibilityBinding(claims({
    ...trade, action: "buy", max_spend_pusd: "10", max_price: "0.55",
  }), now);
  assert.equal(buy.max_spend_pusd, "10");
  const sell = validateEligibilityBinding(claims({
    ...trade, action: "sell", shares: "4.25", min_price: "0.45",
  }), now);
  assert.equal(sell.shares, "4.25");
  for (const invalid of [
    claims({ ...trade, action: "buy", max_spend_pusd: "10" }),
    claims({ ...trade, action: "sell", shares: "4", min_price: "1" }),
    claims({ ...trade, action: "sell", shares: "4", min_price: "0.4", private_key: "no" }),
    claims({ ...trade, action: "sell", shares: "9".repeat(400), min_price: "0.4" }),
  ]) assert.throws(() => validateEligibilityBinding(invalid, now), /invalid binding/);
});

test("accepts exact claim binding without session or amount authority", () => {
  const claim = validateEligibilityBinding(claims({
    action: "claim", market_path: "/markets/slug/example", outcome: "yes",
  }), now);
  assert.equal(claim.action, "claim");
  assert.equal(claim.outcome, "yes");
  for (const invalid of [
    claims({
      action: "claim", market_path: "/markets/slug/example", outcome: "yes",
      session_address: "0x" + "3".repeat(40),
    }),
    claims({
      action: "claim", market_path: "/markets/slug/example", outcome: "yes",
      shares: "4",
    }),
    claims({ action: "claim", market_path: "/events/example", outcome: "yes" }),
  ]) assert.throws(() => validateEligibilityBinding(invalid, now), /invalid binding/);
});


test("accepts exact public withdrawal binding", () => {
  const withdrawal = {
    action: "withdraw", amount_pusd: "25", destination_chain_id: 8453,
    destination_token_address: "0x" + "4".repeat(40),
    recipient_address: "0x" + "3".repeat(40),
  };
  assert.equal(validateEligibilityBinding(claims(withdrawal), now).amount_pusd, "25");
  for (const invalid of [
    claims({ ...withdrawal, amount_pusd: "0" }),
    claims({ ...withdrawal, destination_chain_id: "8453" }),
    claims({ ...withdrawal, recipient_address: "0x" + "2".repeat(40) }),
    claims({ ...withdrawal, private_key: "no" }),
  ]) assert.throws(() => validateEligibilityBinding(invalid, now), /invalid binding/);
});

test("rejects drift, extra fields, wrong identity, and expiry", () => {
  for (const input of [
    claims({ action: "trade" }), claims({ chain_id: 1 }),
    claims({ extra: true }), claims({ account_wallet_address: "0x" + "1".repeat(40) }),
    { ...claims(), sub: "654321" }, { ...claims(), exp: now },
  ]) assert.throws(() => validateEligibilityBinding(input, now), /invalid binding/);
});
