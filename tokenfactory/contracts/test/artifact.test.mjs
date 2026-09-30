import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { getCreate2Address } from "ethers";


const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const artifact = JSON.parse(
  fs.readFileSync(path.join(root, "build", "SickGamingTokenFactory.json"), "utf8"),
);
const manifest = JSON.parse(
  fs.readFileSync(path.join(root, "candidate-manifest.json"), "utf8"),
);
const mainnetManifest = JSON.parse(
  fs.readFileSync(path.join(root, "manifests", "base-mainnet.json"), "utf8"),
);
const source = fs.readFileSync(
  path.join(root, "src", "SickGamingTokenFactory.sol"),
);
const packageLock = fs.readFileSync(path.join(root, "package-lock.json"));

function sha256(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

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
  assert.equal(
    getCreate2Address(
      manifest.singletonFactory,
      manifest.deploymentSalt,
      artifact.creationCodeHash,
    ),
    manifest.predictedFactoryAddress,
  );
});

test("mainnet manifest is reproducible, isolated, and fail-closed", () => {
  assert.equal(mainnetManifest.network, "base-mainnet");
  assert.equal(mainnetManifest.chainId, 8453);
  assert.equal(mainnetManifest.sourceSha256, sha256(source));
  assert.equal(mainnetManifest.packageLockSha256, sha256(packageLock));
  assert.equal(mainnetManifest.compiler, artifact.compilerVersion);
  assert.deepEqual(mainnetManifest.optimizer, artifact.optimizer);
  assert.equal(mainnetManifest.metadataBytecodeHash, "none");
  assert.equal(mainnetManifest.factoryCreationCodeHash, artifact.creationCodeHash);
  assert.equal(mainnetManifest.factoryRuntimeCodeHash, artifact.runtimeCodeHash);
  assert.equal(
    mainnetManifest.predictedFactoryAddress,
    manifest.predictedFactoryAddress,
  );
  assert.equal(mainnetManifest.factoryAddress, null);
  assert.equal(mainnetManifest.deploymentTransaction, null);
  assert.deepEqual(mainnetManifest.authorization, {
    factoryDeployment: false,
    ownerCanary: false,
    memberDeployment: false,
  });
  assert.equal(mainnetManifest.independentAudit.status, "required");
});
