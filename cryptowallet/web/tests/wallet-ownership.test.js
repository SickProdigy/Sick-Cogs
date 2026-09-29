import assert from "node:assert/strict";
import test from "node:test";

import { resolveSmartAccountOwner } from "../src/wallet-ownership.js";

const smartAddress = "0x7930fB6E9853B3835Cf047f36855993cb82d4387";
const ownerAddress = "0x15095ec8fb1fc9c664b3223459dff43158ace7ad";

function legacyProfileResponse() {
  return {
    evmSmartAccountObjects: [{
      address: smartAddress.toLowerCase(),
      ownerAddresses: [ownerAddress.toUpperCase()],
    }],
    evmAccountObjects: [{ address: ownerAddress }],
  };
}

test("resolves the provider owner for a legacy profile storing only the smart address", () => {
  assert.equal(resolveSmartAccountOwner(legacyProfileResponse(), smartAddress), ownerAddress);
});

test("rejects a response without the expected smart account", () => {
  assert.throws(
    () => resolveSmartAccountOwner({ evmSmartAccountObjects: [], evmAccountObjects: [] }, smartAddress),
    /expected smart account/
  );
});

test("rejects missing, foreign, or ambiguous owners", () => {
  const missing = legacyProfileResponse();
  missing.evmAccountObjects = [];
  assert.throws(() => resolveSmartAccountOwner(missing, smartAddress), /exactly one/);

  const ambiguous = legacyProfileResponse();
  const secondOwner = "0xe85a59c628f7d27878aceb4bf3b35733630083a9";
  ambiguous.evmSmartAccountObjects[0].ownerAddresses.push(secondOwner);
  ambiguous.evmAccountObjects.push({ address: secondOwner });
  assert.throws(() => resolveSmartAccountOwner(ambiguous, smartAddress), /exactly one/);
});
