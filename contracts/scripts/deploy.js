// Deploys the four Edge Grid contracts and records what it actually cost.
//
// Everything written to deployment.json comes from a mined receipt: addresses,
// gas used per contract, the block it landed in, the chain id reported by the
// node. edgegrid/chain.py refuses to run without this file, so the Python side
// can never quietly talk to a chain nobody deployed to.
//
//   npx hardhat node &
//   npx hardhat run scripts/deploy.js --network localhost

const { ethers, network, artifacts } = require("hardhat");
const fs = require("fs");
const path = require("path");

const env = (k, d) => (process.env[k] === undefined ? d : process.env[k]);

async function deployed(factory, args) {
  const c = await factory.deploy(...args);
  await c.waitForDeployment();
  const receipt = await ethers.provider.getTransactionReceipt(c.deploymentTransaction().hash);
  return { contract: c, receipt };
}

async function main() {
  const [deployer, treasury] = await ethers.getSigners();

  const minStake = ethers.parseEther(env("MIN_STAKE", "10"));
  const unbondingPeriod = BigInt(env("UNBONDING_PERIOD_S", "3600"));
  const challengeWindow = BigInt(env("CHALLENGE_WINDOW_S", "3600"));
  const awardTimeout = BigInt(env("AWARD_TIMEOUT_S", "600"));
  const treasuryAddress = env("TREASURY_ADDRESS", treasury.address);

  const out = { contracts: {}, gasUsed: {}, txHashes: {} };
  const record = (name, { contract, receipt }) => {
    out.contracts[name] = contract.target;
    out.gasUsed[name] = Number(receipt.gasUsed);
    out.txHashes[name] = receipt.hash;
    console.log(`${name.padEnd(22)} ${contract.target}  gas=${receipt.gasUsed}`);
    return contract;
  };

  const registry = record("NodeRegistry", await deployed(
    await ethers.getContractFactory("NodeRegistry"), [minStake, treasuryAddress, unbondingPeriod]));
  const marketplace = record("Marketplace", await deployed(
    await ethers.getContractFactory("Marketplace"), [registry.target, awardTimeout]));
  const verification = record("VerificationContract", await deployed(
    await ethers.getContractFactory("VerificationContract"),
    [registry.target, marketplace.target, challengeWindow]));
  const models = record("ModelRegistry", await deployed(
    await ethers.getContractFactory("ModelRegistry"), []));

  // Wiring: the registry accepts slashes only from the verification contract,
  // and the marketplace accepts state transitions only from the same address.
  const wiring = {};
  for (const [name, tx] of [
    ["setSlasher", await registry.setSlasher(verification.target)],
    ["setVerificationContract", await marketplace.setVerificationContract(verification.target)],
  ]) {
    const r = await tx.wait();
    wiring[name] = { txHash: r.hash, gasUsed: Number(r.gasUsed) };
    console.log(`${name.padEnd(22)} gas=${r.gasUsed}`);
  }

  const block = await ethers.provider.getBlock("latest");
  const payload = {
    network: network.name,
    rpcUrl: network.config.url || "in-process",
    chainId: Number((await ethers.provider.getNetwork()).chainId),
    deployedAt: new Date().toISOString(),
    blockNumber: block.number,
    deployer: deployer.address,
    treasury: treasuryAddress,
    params: {
      minStakeWei: minStake.toString(),
      unbondingPeriodS: Number(unbondingPeriod),
      challengeWindowS: Number(challengeWindow),
      awardTimeoutS: Number(awardTimeout),
      validatorSlashBps: Number(await registry.VALIDATOR_SLASH_BPS()),
      bps: Number(await registry.BPS()),
    },
    ...out,
    wiring,
    totalDeploymentGas: Object.values(out.gasUsed).reduce((a, b) => a + b, 0),
    abis: Object.fromEntries(await Promise.all(
      Object.keys(out.contracts).map(async (n) => [n, (await artifacts.readArtifact(n)).abi]))),
  };

  const file = path.join(__dirname, "..", "deployment.json");
  fs.writeFileSync(file, JSON.stringify(payload, null, 2));
  console.log(`\ntotal deployment gas ${payload.totalDeploymentGas}`);
  console.log(`chainId=${payload.chainId} block=${payload.blockNumber} -> ${file}`);
}

main().catch((e) => { console.error(e); process.exitCode = 1; });
