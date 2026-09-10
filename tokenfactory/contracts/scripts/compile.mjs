import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";
import solc from "solc";
import { keccak256 } from "ethers";


const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const sourceName = "src/SickGamingTokenFactory.sol";
const sourcePath = path.join(root, sourceName);

function findImport(importPath) {
  const resolved = path.join(root, "node_modules", importPath);
  if (!fs.existsSync(resolved)) {
    return { error: `Import not found: ${importPath}` };
  }
  return { contents: fs.readFileSync(resolved, "utf8") };
}

const input = {
  language: "Solidity",
  sources: {
    [sourceName]: { content: fs.readFileSync(sourcePath, "utf8") },
  },
  settings: {
    optimizer: { enabled: true, runs: 200 },
    metadata: { bytecodeHash: "none", appendCBOR: false },
    outputSelection: {
      "*": { "*": ["abi", "evm.bytecode.object", "evm.deployedBytecode.object"] },
    },
  },
};

const output = JSON.parse(solc.compile(JSON.stringify(input), { import: findImport }));
const errors = (output.errors ?? []).filter((item) => item.severity === "error");
if (errors.length) {
  for (const error of errors) console.error(error.formattedMessage);
  process.exit(1);
}

const buildDirectory = path.join(root, "build");
fs.mkdirSync(buildDirectory, { recursive: true });
for (const [name, contract] of Object.entries(output.contracts[sourceName])) {
  const bytecode = `0x${contract.evm.bytecode.object}`;
  const runtimeBytecode = `0x${contract.evm.deployedBytecode.object}`;
  const artifact = {
    contractName: name,
    compilerVersion: solc.version(),
    optimizer: { enabled: true, runs: 200 },
    metadataBytecodeHash: "none",
    source: sourceName,
    abi: contract.abi,
    bytecode,
    runtimeBytecode,
    creationCodeHash: keccak256(bytecode),
    runtimeCodeHash: keccak256(runtimeBytecode),
  };
  fs.writeFileSync(
    path.join(buildDirectory, `${name}.json`),
    `${JSON.stringify(artifact, null, 2)}\n`,
  );
  console.log(`${name} runtime code hash: ${artifact.runtimeCodeHash}`);
}
