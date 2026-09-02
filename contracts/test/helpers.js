const { ethers } = require("hardhat");
const crypto = require("crypto");

// Merkle scheme byte-compatible with edgegrid/da.py: domain-separated leaves
// and nodes, odd tail duplicated. Reimplemented here so the Solidity verifier is
// tested against an independent implementation rather than against itself.
const sha256 = (buf) => crypto.createHash("sha256").update(buf).digest();
const leafHash = (data) => sha256(Buffer.concat([Buffer.from([0x00]), data]));
const nodeHash = (a, b) => sha256(Buffer.concat([Buffer.from([0x01]), a, b]));

function merkleRoot(leaves) {
  if (leaves.length === 0) return sha256(Buffer.alloc(0));
  let level = leaves.map(leafHash);
  while (level.length > 1) {
    const next = [];
    for (let i = 0; i < level.length; i += 2) {
      const left = level[i];
      const right = i + 1 < level.length ? level[i + 1] : left;
      next.push(nodeHash(left, right));
    }
    level = next;
  }
  return level[0];
}

function merkleProof(leaves, index) {
  let level = leaves.map(leafHash);
  const path = [];
  let idx = index;
  while (level.length > 1) {
    let sib = idx ^ 1;
    if (sib >= level.length) sib = idx;
    path.push(level[sib]);
    const next = [];
    for (let i = 0; i < level.length; i += 2) {
      const left = level[i];
      const right = i + 1 < level.length ? level[i + 1] : left;
      next.push(nodeHash(left, right));
    }
    level = next;
    idx = Math.floor(idx / 2);
  }
  return path;
}

const hex = (buf) => "0x" + buf.toString("hex");
const jobId = (s) => ethers.keccak256(ethers.toUtf8Bytes(s));

const MIN_STAKE = ethers.parseEther("1");
const UNBONDING = 3600n;
const CHALLENGE_WINDOW = 3600n;
const AWARD_TIMEOUT = 600n;

async function deployAll() {
  const [owner, requester, provider, validator, stranger, treasury] = await ethers.getSigners();

  const NodeRegistry = await ethers.getContractFactory("NodeRegistry");
  const registry = await NodeRegistry.deploy(MIN_STAKE, treasury.address, UNBONDING);

  const Marketplace = await ethers.getContractFactory("Marketplace");
  const marketplace = await Marketplace.deploy(await registry.getAddress(), AWARD_TIMEOUT);

  const Verification = await ethers.getContractFactory("VerificationContract");
  const verification = await Verification.deploy(
    await registry.getAddress(),
    await marketplace.getAddress(),
    CHALLENGE_WINDOW
  );

  const ModelRegistry = await ethers.getContractFactory("ModelRegistry");
  const models = await ModelRegistry.deploy();

  await registry.setSlasher(await verification.getAddress());
  await marketplace.setVerificationContract(await verification.getAddress());
  await verification.setValidator(validator.address, true);

  return { owner, requester, provider, validator, stranger, treasury, registry, marketplace, verification, models };
}

module.exports = {
  deployAll, merkleRoot, merkleProof, leafHash, sha256, hex, jobId,
  MIN_STAKE, UNBONDING, CHALLENGE_WINDOW, AWARD_TIMEOUT,
  EscrowState: { NONE: 0n, OPEN: 1n, AWAITING_VERIFICATION: 2n, SETTLED: 3n, SLASHED: 4n, REFUNDED: 5n },
  VerdictKind: { NONE: 0, PASS: 1, FAIL: 2, ERROR: 3 },
  ResolutionKind: { NONE: 0n, DATA_MISMATCH_PROOF: 1n, VALIDATOR_VERDICT: 2n },
};
