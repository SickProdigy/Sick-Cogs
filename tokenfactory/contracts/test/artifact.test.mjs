import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";


const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const artifact = JSON.parse(
  fs.readFileSync(path.join(root, "build", "SickGamingTokenFactory.json"), "utf8"),
);
const manifest = JSON.parse(
  fs.readFileSync(path.join(root, "candidate-manifest.json"), "utf8"),
);

test("factory artifact exposes only the reviewed deployment surface", () => {
  const functions = artifact.abi
    .filter((item) => item.type === "function")
    .map((item) => item.name)
    .sort();
  assert.deepEqual(functions, [
    "MAX_DECIMALS",
    "MAX_NAME_BYTES",
    "MAX_SYMBOL_BYTES",
    "createFixedSupplyToken",
    "deployment",
  ]);
  assert.ok(!functions.includes("mint"));
  assert.ok(!functions.includes("owner"));
  assert.ok(!functions.includes("upgradeTo"));
});

test("factory build records pinned reproducibility metadata", () => {
  assert.match(artifact.compilerVersion, /^0\.8\.37\+/);
  assert.deepEqual(artifact.optimizer, { enabled: true, runs: 200 });
  assert.equal(artifact.metadataBytecodeHash, "none");
  assert.match(artifact.creationCodeHash, /^0x[0-9a-f]{64}$/);
  assert.match(artifact.runtimeCodeHash, /^0x[0-9a-f]{64}$/);
  assert.equal(manifest.status, "candidate-not-deployed");
  assert.equal(manifest.factoryAddress, null);
  assert.equal(manifest.compiler, artifact.compilerVersion);
  assert.equal(manifest.factoryCreationCodeHash, artifact.creationCodeHash);
  assert.equal(manifest.factoryRuntimeCodeHash, artifact.runtimeCodeHash);
});
