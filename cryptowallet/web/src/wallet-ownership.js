export function resolveSmartAccountOwner(user, expectedAddress) {
  const normalizedExpected = String(expectedAddress || "").toLowerCase();
  const smartAccount = (user?.evmSmartAccountObjects || []).find(
    (account) => String(account?.address || "").toLowerCase() === normalizedExpected
  );
  if (!smartAccount) {
    throw new Error("Coinbase did not return the expected smart account.");
  }
  const ownerAddresses = new Set(
    (smartAccount.ownerAddresses || []).map((address) => String(address).toLowerCase())
  );
  const owners = (user?.evmAccountObjects || []).filter((account) =>
    ownerAddresses.has(String(account?.address || "").toLowerCase())
  );
  if (owners.length !== 1) {
    throw new Error("Coinbase did not return exactly one exportable owner for this smart account.");
  }
  return owners[0].address;
}
