const { expect } = require("chai");
const { ethers } = require("hardhat");
const { loadFixture, time } = require("@nomicfoundation/hardhat-network-helpers");
const {
  deployAll, jobId, hex, merkleRoot, merkleProof, sha256,
  MIN_STAKE, UNBONDING, CHALLENGE_WINDOW, EscrowState, VerdictKind, ResolutionKind,
} = require("./helpers");

const PRICE = ethers.parseEther("0.05");
const HONEST = "the capital of France is Paris";
const FRAUD = "the capital of France is Berlin";

async function staked(providerStake = MIN_STAKE) {
  const f = await loadFixture(deployAll);
  await f.registry.connect(f.provider).stake({ value: providerStake });
  await f.registry.connect(f.validator).stake({ value: MIN_STAKE });
  return f;
}

// A DA block of three blobs. `committed` is what the provider claims it
// produced; `stored` is what actually sits in the block at `index`.
function daBlock(stored) {
  const blobs = [Buffer.from("neighbour blob a"), Buffer.from(stored), Buffer.from("neighbour blob b")];
  const index = 1;
  return { blobs, index, root: merkleRoot(blobs), proof: merkleProof(blobs, index).map(hex) };
}

async function open(f, id, amount = PRICE) {
  await f.marketplace.connect(f.requester).openEscrow(id, f.provider.address, { value: amount });
}

describe("VerificationContract", function () {
  describe("commitment", function () {
    it("records a commitment and starts the challenge window", async function () {
      const f = await staked();
      const id = jobId("job-commit");
      await open(f, id);
      const da = daBlock(HONEST);

      const tx = await f.verification.connect(f.provider)
        .recordCommitment(id, hex(sha256(Buffer.from(HONEST))), hex(da.root), da.index, "blob-1");
      const block = await ethers.provider.getBlock((await tx.wait()).blockNumber);
      const deadline = BigInt(block.timestamp) + CHALLENGE_WINDOW;

      await expect(tx).to.emit(f.verification, "CommitmentRecorded")
        .withArgs(id, f.provider.address, hex(sha256(Buffer.from(HONEST))), hex(da.root), da.index, "blob-1", deadline);
      expect(await f.marketplace.challengeDeadlineOf(id)).to.equal(deadline);
      expect(await f.verification.totalCommitments()).to.equal(1n);
    });

    it("refuses a commitment when no escrow was opened", async function () {
      const f = await staked();
      await expect(
        f.verification.connect(f.provider)
          .recordCommitment(jobId("ghost"), ethers.ZeroHash, ethers.ZeroHash, 0, "x")
      ).to.be.revertedWithCustomError(f.verification, "EscrowNotOpen")
        .withArgs(jobId("ghost"), EscrowState.NONE);
    });
  });

  describe("fraud proof (trustless path)", function () {
    it("slashes when the DA blob does not hash to the committed output hash", async function () {
      const f = await staked();
      const id = jobId("job-fraud");
      await open(f, id);
      const da = daBlock(FRAUD); // provider stored FRAUD but will commit the hash of HONEST

      await f.verification.connect(f.provider)
        .recordCommitment(id, hex(sha256(Buffer.from(HONEST))), hex(da.root), da.index, "blob-fraud");

      const reward = (PRICE * 8000n) / 10000n;
      const treasuryCut = PRICE - reward;

      await expect(
        f.verification.connect(f.stranger)
          .proveDataMismatch(id, "0x" + Buffer.from(FRAUD).toString("hex"), da.proof)
      ).to.emit(f.verification, "FraudConfirmed")
        .withArgs(id, f.provider.address, f.stranger.address,
                 ResolutionKind.DATA_MISMATCH_PROOF, PRICE, reward, treasuryCut, true);

      expect(await f.marketplace.escrowState(id)).to.equal(EscrowState.SLASHED);
      expect(await f.marketplace.withdrawable(f.requester.address)).to.equal(PRICE);
      expect(await f.registry.withdrawable(f.stranger.address)).to.equal(reward);
      expect(await f.registry.withdrawable(f.treasury.address)).to.equal(treasuryCut);
      expect(await f.registry.stakeOf(f.provider.address)).to.equal(MIN_STAKE - PRICE);
    });

    it("rejects a truthful reveal - an honest provider cannot be slashed", async function () {
      const f = await staked();
      const id = jobId("job-honest");
      await open(f, id);
      const da = daBlock(HONEST);
      const h = hex(sha256(Buffer.from(HONEST)));
      await f.verification.connect(f.provider).recordCommitment(id, h, hex(da.root), da.index, "blob-ok");

      await expect(
        f.verification.connect(f.stranger)
          .proveDataMismatch(id, "0x" + Buffer.from(HONEST).toString("hex"), da.proof)
      ).to.be.revertedWithCustomError(f.verification, "NoMismatch").withArgs(id, h);
      expect(await f.registry.stakeOf(f.provider.address)).to.equal(MIN_STAKE);
    });

    it("rejects a blob that is not in the committed DA block", async function () {
      const f = await staked();
      const id = jobId("job-badproof");
      await open(f, id);
      const da = daBlock(FRAUD);
      await f.verification.connect(f.provider)
        .recordCommitment(id, hex(sha256(Buffer.from(HONEST))), hex(da.root), da.index, "b");

      await expect(
        f.verification.connect(f.stranger)
          .proveDataMismatch(id, "0x" + Buffer.from("not in the block").toString("hex"), da.proof)
      ).to.be.revertedWithCustomError(f.verification, "BadInclusionProof");
    });

    it("rejects a neighbouring blob replayed at the committed index", async function () {
      const f = await staked();
      const id = jobId("job-neighbour");
      await open(f, id);
      const da = daBlock(HONEST);
      await f.verification.connect(f.provider)
        .recordCommitment(id, hex(sha256(Buffer.from(HONEST))), hex(da.root), da.index, "b");

      // blobs[0] is genuinely in the block, but not at the committed leaf index
      const other = da.blobs[0];
      await expect(
        f.verification.connect(f.stranger)
          .proveDataMismatch(id, "0x" + other.toString("hex"), merkleProof(da.blobs, 0).map(hex))
      ).to.be.revertedWithCustomError(f.verification, "BadInclusionProof");
    });

    it("refuses a challenge after the window has closed", async function () {
      const f = await staked();
      const id = jobId("job-late");
      await open(f, id);
      const da = daBlock(FRAUD);
      await f.verification.connect(f.provider)
        .recordCommitment(id, hex(sha256(Buffer.from(HONEST))), hex(da.root), da.index, "b");

      await time.increase(CHALLENGE_WINDOW + 1n);
      await expect(
        f.verification.connect(f.stranger)
          .proveDataMismatch(id, "0x" + Buffer.from(FRAUD).toString("hex"), da.proof)
      ).to.be.revertedWithCustomError(f.verification, "ChallengeWindowClosed");
      await expect(f.marketplace.release(id)).to.emit(f.marketplace, "EscrowSettled");
    });

    it("rejects a second resolution of the same job", async function () {
      const f = await staked();
      const id = jobId("job-twice");
      await open(f, id);
      const da = daBlock(FRAUD);
      await f.verification.connect(f.provider)
        .recordCommitment(id, hex(sha256(Buffer.from(HONEST))), hex(da.root), da.index, "b");
      const data = "0x" + Buffer.from(FRAUD).toString("hex");

      await f.verification.connect(f.stranger).proveDataMismatch(id, data, da.proof);
      await expect(f.verification.connect(f.stranger).proveDataMismatch(id, data, da.proof))
        .to.be.revertedWithCustomError(f.verification, "AlreadyResolved").withArgs(id);
      await expect(f.marketplace.release(id))
        .to.be.revertedWithCustomError(f.marketplace, "WrongState");
    });
  });

  describe("validator verdict (oracle path)", function () {
    it("rejects a verdict from a non-allow-listed address", async function () {
      const f = await staked();
      const id = jobId("job-verdict-acl");
      await open(f, id);
      const da = daBlock(HONEST);
      await f.verification.connect(f.provider)
        .recordCommitment(id, hex(sha256(Buffer.from(HONEST))), hex(da.root), da.index, "b");

      for (const who of [f.stranger, f.requester, f.owner]) {
        await expect(f.verification.connect(who).submitVerdict(id, VerdictKind.FAIL, 1, ethers.ZeroHash))
          .to.be.revertedWithCustomError(f.verification, "NotValidator").withArgs(who.address);
      }
      expect(await f.registry.stakeOf(f.provider.address)).to.equal(MIN_STAKE);
    });

    it("rejects a verdict from an allow-listed validator with no stake", async function () {
      const f = await staked();
      await f.verification.setValidator(f.stranger.address, true);
      const id = jobId("job-unstaked-validator");
      await open(f, id);
      const da = daBlock(HONEST);
      await f.verification.connect(f.provider)
        .recordCommitment(id, hex(sha256(Buffer.from(HONEST))), hex(da.root), da.index, "b");
      await expect(f.verification.connect(f.stranger).submitVerdict(id, VerdictKind.FAIL, 1, ethers.ZeroHash))
        .to.be.revertedWithCustomError(f.verification, "ValidatorNotStaked").withArgs(f.stranger.address);
    });

    it("records PASS and ERROR without slashing", async function () {
      const f = await staked();
      for (const [name, kind] of [["job-pass", VerdictKind.PASS], ["job-error", VerdictKind.ERROR]]) {
        const id = jobId(name);
        await open(f, id);
        const da = daBlock(HONEST);
        await f.verification.connect(f.provider)
          .recordCommitment(id, hex(sha256(Buffer.from(HONEST))), hex(da.root), da.index, "b");
        await expect(f.verification.connect(f.validator).submitVerdict(id, kind, 4, ethers.ZeroHash))
          .to.emit(f.verification, "VerdictRecorded")
          .withArgs(id, f.validator.address, kind, 4, ethers.ZeroHash);
        expect(await f.verification.verdicts(id)).to.equal(kind);
        expect(await f.verification.isResolved(id)).to.equal(false);
      }
      expect(await f.registry.stakeOf(f.provider.address)).to.equal(MIN_STAKE);
    });

    it("slashes on FAIL and refunds the requester", async function () {
      const f = await staked();
      const id = jobId("job-fail");
      await open(f, id);
      const da = daBlock(HONEST);
      await f.verification.connect(f.provider)
        .recordCommitment(id, hex(sha256(Buffer.from(HONEST))), hex(da.root), da.index, "b");

      await expect(f.verification.connect(f.validator).submitVerdict(id, VerdictKind.FAIL, 1, ethers.ZeroHash))
        .to.emit(f.verification, "FraudConfirmed")
        .withArgs(id, f.provider.address, f.validator.address, ResolutionKind.VALIDATOR_VERDICT,
                 PRICE, (PRICE * 8000n) / 10000n, PRICE - (PRICE * 8000n) / 10000n, true);
      expect(await f.marketplace.escrowState(id)).to.equal(EscrowState.SLASHED);
    });
  });

  describe("slash arithmetic", function () {
    it("splits exactly 80/20 with no dust left over", async function () {
      const f = await staked(ethers.parseEther("5"));
      // deliberately not divisible by 5, so the split has a remainder
      const amount = 1234567890123457n;
      const id = jobId("job-dust");
      await f.marketplace.connect(f.requester).openEscrow(id, f.provider.address, { value: amount });
      const da = daBlock(FRAUD);
      await f.verification.connect(f.provider)
        .recordCommitment(id, hex(sha256(Buffer.from(HONEST))), hex(da.root), da.index, "b");
      await f.verification.connect(f.validator)
        .proveDataMismatch(id, "0x" + Buffer.from(FRAUD).toString("hex"), da.proof);

      const reward = await f.registry.withdrawable(f.validator.address);
      const treasuryCut = await f.registry.withdrawable(f.treasury.address);
      expect(reward).to.equal(987654312098765n);        // floor(amount * 0.8)
      expect(reward + treasuryCut).to.equal(amount);    // value conserved, no dust
      expect((reward * 10000n) / amount).to.equal(7999n); // floor rounding is downward
      expect(await f.registry.totalSlashed()).to.equal(amount);
    });

    it("caps the slash at the remaining stake and reports it as not fully covered", async function () {
      const f = await staked();                       // provider stake = MIN_STAKE = 1 ETH
      const amount = MIN_STAKE * 2n;                  // escrow larger than the collateral
      const id = jobId("job-underfunded");
      await f.marketplace.connect(f.requester).openEscrow(id, f.provider.address, { value: amount });
      const da = daBlock(FRAUD);
      await f.verification.connect(f.provider)
        .recordCommitment(id, hex(sha256(Buffer.from(HONEST))), hex(da.root), da.index, "b");

      const reward = (MIN_STAKE * 8000n) / 10000n;
      await expect(
        f.verification.connect(f.validator)
          .proveDataMismatch(id, "0x" + Buffer.from(FRAUD).toString("hex"), da.proof)
      ).to.emit(f.verification, "FraudConfirmed")
        .withArgs(id, f.provider.address, f.validator.address, ResolutionKind.DATA_MISMATCH_PROOF,
                 MIN_STAKE, reward, MIN_STAKE - reward, false);   // fullyCovered == false
      expect(await f.registry.stakeOf(f.provider.address)).to.equal(0n);
      expect(await f.registry.withdrawable(f.requester.address)).to.equal(0n);
      expect(await f.marketplace.withdrawable(f.requester.address)).to.equal(amount);
    });

    it("reaches collateral that is already unbonding", async function () {
      const f = await staked(MIN_STAKE * 2n);
      await f.registry.connect(f.provider).requestUnstake(MIN_STAKE); // trying to escape
      const id = jobId("job-escape");
      const amount = MIN_STAKE + MIN_STAKE / 2n;
      await f.marketplace.connect(f.requester).openEscrow(id, f.provider.address, { value: amount });
      const da = daBlock(FRAUD);
      await f.verification.connect(f.provider)
        .recordCommitment(id, hex(sha256(Buffer.from(HONEST))), hex(da.root), da.index, "b");
      await f.verification.connect(f.validator)
        .proveDataMismatch(id, "0x" + Buffer.from(FRAUD).toString("hex"), da.proof);

      expect(await f.registry.stakeOf(f.provider.address)).to.equal(0n);
      expect(await f.registry.slashableOf(f.provider.address)).to.equal(MIN_STAKE * 2n - amount);
      expect(await f.registry.totalSlashed()).to.equal(amount);
    });
  });
});
