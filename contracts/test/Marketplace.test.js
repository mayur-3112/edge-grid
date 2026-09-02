const { expect } = require("chai");
const { ethers } = require("hardhat");
const { loadFixture, time } = require("@nomicfoundation/hardhat-network-helpers");
const {
  deployAll, jobId, hex, merkleRoot, merkleProof, sha256,
  MIN_STAKE, CHALLENGE_WINDOW, AWARD_TIMEOUT, EscrowState,
} = require("./helpers");

const PRICE = ethers.parseEther("0.05");

async function staked() {
  const f = await loadFixture(deployAll);
  await f.registry.connect(f.provider).stake({ value: MIN_STAKE });
  await f.registry.connect(f.validator).stake({ value: MIN_STAKE });
  return f;
}

// Commit an honest output: the DA blob at leafIndex hashes to the committed
// output hash.
async function commitHonest(f, id, output = "the answer is 42") {
  const blobs = [Buffer.from("other job"), Buffer.from(output), Buffer.from("third job")];
  const index = 1;
  const root = merkleRoot(blobs);
  await f.verification.connect(f.provider)
    .recordCommitment(id, hex(sha256(blobs[index])), hex(root), index, "blob-abc");
  return { blobs, index, root, proof: merkleProof(blobs, index) };
}

describe("Marketplace escrow lifecycle", function () {
  it("walks OPEN -> AWAITING_VERIFICATION -> SETTLED and pays the provider", async function () {
    const f = await staked();
    const id = jobId("job-happy");

    await expect(f.marketplace.connect(f.requester).openEscrow(id, f.provider.address, { value: PRICE }))
      .to.emit(f.marketplace, "EscrowOpened")
      .withArgs(id, f.requester.address, f.provider.address, PRICE);
    expect(await f.marketplace.escrowState(id)).to.equal(EscrowState.OPEN);

    await commitHonest(f, id);
    expect(await f.marketplace.escrowState(id)).to.equal(EscrowState.AWAITING_VERIFICATION);

    await time.increase(CHALLENGE_WINDOW + 1n);
    await expect(f.marketplace.release(id))
      .to.emit(f.marketplace, "EscrowSettled").withArgs(id, f.provider.address, PRICE);
    expect(await f.marketplace.escrowState(id)).to.equal(EscrowState.SETTLED);

    expect(await f.marketplace.withdrawable(f.provider.address)).to.equal(PRICE);
    const before = await ethers.provider.getBalance(f.provider.address);
    const rc = await (await f.marketplace.connect(f.provider).withdraw()).wait();
    const after = await ethers.provider.getBalance(f.provider.address);
    expect(after - before + rc.gasUsed * rc.gasPrice).to.equal(PRICE);
    expect(await ethers.provider.getBalance(await f.marketplace.getAddress())).to.equal(0n);
  });

  it("refuses an escrow against a provider with no stake", async function () {
    const f = await loadFixture(deployAll);
    await expect(
      f.marketplace.connect(f.requester).openEscrow(jobId("j"), f.provider.address, { value: PRICE })
    ).to.be.revertedWithCustomError(f.marketplace, "ProviderNotActive").withArgs(f.provider.address);
  });

  it("rejects a duplicate escrow for the same job id", async function () {
    const f = await staked();
    const id = jobId("job-dup");
    await f.marketplace.connect(f.requester).openEscrow(id, f.provider.address, { value: PRICE });
    await expect(f.marketplace.connect(f.requester).openEscrow(id, f.provider.address, { value: PRICE }))
      .to.be.revertedWithCustomError(f.marketplace, "EscrowExists").withArgs(id);
  });

  describe("challenge window", function () {
    it("reverts an early release and succeeds once the window closes", async function () {
      const f = await staked();
      const id = jobId("job-window");
      await f.marketplace.connect(f.requester).openEscrow(id, f.provider.address, { value: PRICE });
      await commitHonest(f, id);

      const deadline = await f.marketplace.challengeDeadlineOf(id);
      await expect(f.marketplace.release(id))
        .to.be.revertedWithCustomError(f.marketplace, "ChallengeWindowOpen");

      await time.increaseTo(deadline - 2n);
      await expect(f.marketplace.release(id))
        .to.be.revertedWithCustomError(f.marketplace, "ChallengeWindowOpen");

      await time.increaseTo(deadline);
      await expect(f.marketplace.release(id)).to.emit(f.marketplace, "EscrowSettled");
    });

    it("refuses a release before any commitment exists", async function () {
      const f = await staked();
      const id = jobId("job-nocommit");
      await f.marketplace.connect(f.requester).openEscrow(id, f.provider.address, { value: PRICE });
      await expect(f.marketplace.release(id))
        .to.be.revertedWithCustomError(f.marketplace, "WrongState")
        .withArgs(id, EscrowState.OPEN, EscrowState.AWAITING_VERIFICATION);
    });
  });

  describe("double settlement", function () {
    it("rejects a second release of the same escrow", async function () {
      const f = await staked();
      const id = jobId("job-double");
      await f.marketplace.connect(f.requester).openEscrow(id, f.provider.address, { value: PRICE });
      await commitHonest(f, id);
      await time.increase(CHALLENGE_WINDOW + 1n);
      await f.marketplace.release(id);
      await expect(f.marketplace.release(id))
        .to.be.revertedWithCustomError(f.marketplace, "WrongState")
        .withArgs(id, EscrowState.SETTLED, EscrowState.AWAITING_VERIFICATION);
      expect(await f.marketplace.withdrawable(f.provider.address)).to.equal(PRICE);
    });

    it("rejects a second commitment for the same job", async function () {
      const f = await staked();
      const id = jobId("job-recommit");
      await f.marketplace.connect(f.requester).openEscrow(id, f.provider.address, { value: PRICE });
      await commitHonest(f, id);
      await expect(commitHonest(f, id))
        .to.be.revertedWithCustomError(f.verification, "CommitmentExists").withArgs(id);
    });
  });

  describe("access control", function () {
    it("stops a stranger settling, refunding, or advancing an escrow", async function () {
      const f = await staked();
      const id = jobId("job-acl");
      await f.marketplace.connect(f.requester).openEscrow(id, f.provider.address, { value: PRICE });

      for (const who of [f.stranger, f.owner, f.requester, f.provider]) {
        await expect(f.marketplace.connect(who).refundOnFraud(id))
          .to.be.revertedWithCustomError(f.marketplace, "NotVerificationContract").withArgs(who.address);
        await expect(f.marketplace.connect(who).beginVerification(id, 1))
          .to.be.revertedWithCustomError(f.marketplace, "NotVerificationContract").withArgs(who.address);
      }
      expect(await f.marketplace.escrowState(id)).to.equal(EscrowState.OPEN);
    });

    it("stops anyone but the awarded provider recording the commitment", async function () {
      const f = await staked();
      const id = jobId("job-commit-acl");
      await f.marketplace.connect(f.requester).openEscrow(id, f.provider.address, { value: PRICE });
      await expect(
        f.verification.connect(f.stranger).recordCommitment(id, ethers.ZeroHash, ethers.ZeroHash, 0, "x")
      ).to.be.revertedWithCustomError(f.verification, "NotAwardedProvider")
        .withArgs(id, f.stranger.address, f.provider.address);
    });

    it("lets only the requester cancel, and only after the award timeout", async function () {
      const f = await staked();
      const id = jobId("job-cancel");
      await f.marketplace.connect(f.requester).openEscrow(id, f.provider.address, { value: PRICE });

      await expect(f.marketplace.connect(f.requester).cancel(id))
        .to.be.revertedWithCustomError(f.marketplace, "AwardWindowOpen");
      await time.increase(AWARD_TIMEOUT + 1n);
      await expect(f.marketplace.connect(f.stranger).cancel(id))
        .to.be.revertedWithCustomError(f.marketplace, "NotRequester");
      await expect(f.marketplace.connect(f.requester).cancel(id))
        .to.emit(f.marketplace, "EscrowRefunded").withArgs(id, f.requester.address, PRICE);
      expect(await f.marketplace.escrowState(id)).to.equal(EscrowState.REFUNDED);
      expect(await f.marketplace.withdrawable(f.requester.address)).to.equal(PRICE);
    });
  });

  describe("reentrancy", function () {
    it("blocks a requester contract re-entering withdraw from its receive hook", async function () {
      const f = await staked();
      const Attacker = await ethers.getContractFactory("ReentrantRequester");
      const attacker = await Attacker.deploy(await f.marketplace.getAddress());
      const id = jobId("job-reentrant");

      await attacker.open(id, f.provider.address, { value: PRICE });
      await time.increase(AWARD_TIMEOUT + 1n);
      await attacker.cancel(id);
      expect(await f.marketplace.withdrawable(await attacker.getAddress())).to.equal(PRICE);

      await attacker.attack();

      expect(await attacker.reentryAttempts()).to.equal(1n);
      expect(await attacker.reentryReverted()).to.equal(true);
      expect(await ethers.provider.getBalance(await attacker.getAddress())).to.equal(PRICE);
      expect(await ethers.provider.getBalance(await f.marketplace.getAddress())).to.equal(0n);
      expect(await f.marketplace.withdrawable(await attacker.getAddress())).to.equal(0n);
    });
  });
});
