const { expect } = require("chai");
const { ethers } = require("hardhat");
const { loadFixture, time } = require("@nomicfoundation/hardhat-network-helpers");
const { deployAll, MIN_STAKE, UNBONDING } = require("./helpers");

describe("NodeRegistry", function () {
  describe("staking", function () {
    it("accepts a stake at or above the minimum and marks the node active", async function () {
      const { registry, provider } = await loadFixture(deployAll);
      await expect(registry.connect(provider).stake({ value: MIN_STAKE }))
        .to.emit(registry, "Staked")
        .withArgs(provider.address, MIN_STAKE, MIN_STAKE);
      expect(await registry.stakeOf(provider.address)).to.equal(MIN_STAKE);
      expect(await registry.isActive(provider.address)).to.equal(true);
      expect(await registry.totalStaked()).to.equal(MIN_STAKE);
    });

    it("rejects a stake below the minimum", async function () {
      const { registry, provider } = await loadFixture(deployAll);
      const tooSmall = MIN_STAKE - 1n;
      await expect(registry.connect(provider).stake({ value: tooSmall }))
        .to.be.revertedWithCustomError(registry, "BelowMinimumStake")
        .withArgs(tooSmall, MIN_STAKE);
      expect(await registry.isActive(provider.address)).to.equal(false);
    });

    it("rejects a zero stake", async function () {
      const { registry, provider } = await loadFixture(deployAll);
      await expect(registry.connect(provider).stake({ value: 0 }))
        .to.be.revertedWithCustomError(registry, "ZeroAmount");
    });
  });

  describe("unstaking and withdrawal", function () {
    it("timelocks an unstake and pays it out after the unbonding period", async function () {
      const { registry, provider } = await loadFixture(deployAll);
      await registry.connect(provider).stake({ value: MIN_STAKE * 2n });
      await registry.connect(provider).requestUnstake(MIN_STAKE);

      expect(await registry.stakeOf(provider.address)).to.equal(MIN_STAKE);
      expect(await registry.slashableOf(provider.address)).to.equal(MIN_STAKE * 2n);

      await expect(registry.connect(provider).claimUnstake())
        .to.be.revertedWithCustomError(registry, "UnbondingNotReady");

      await time.increase(UNBONDING + 1n);
      await expect(registry.connect(provider).claimUnstake())
        .to.emit(registry, "UnstakeClaimed").withArgs(provider.address, MIN_STAKE);

      const before = await ethers.provider.getBalance(provider.address);
      const rc = await (await registry.connect(provider).withdraw()).wait();
      const after = await ethers.provider.getBalance(provider.address);
      expect(after - before + rc.gasUsed * rc.gasPrice).to.equal(MIN_STAKE);
    });

    it("refuses a partial unstake that would leave the node under-collateralised", async function () {
      const { registry, provider } = await loadFixture(deployAll);
      await registry.connect(provider).stake({ value: MIN_STAKE });
      await expect(registry.connect(provider).requestUnstake(1n))
        .to.be.revertedWithCustomError(registry, "BelowMinimumStake");
      // full exit is allowed
      await expect(registry.connect(provider).requestUnstake(MIN_STAKE)).to.not.be.reverted;
    });

    it("reverts a withdrawal with nothing credited", async function () {
      const { registry, stranger } = await loadFixture(deployAll);
      await expect(registry.connect(stranger).withdraw())
        .to.be.revertedWithCustomError(registry, "NothingToWithdraw");
    });
  });

  describe("access control", function () {
    it("rejects slash from anyone but the verification contract", async function () {
      const { registry, provider, stranger, owner, validator } = await loadFixture(deployAll);
      await registry.connect(provider).stake({ value: MIN_STAKE });
      for (const who of [stranger, owner, validator, provider]) {
        await expect(registry.connect(who).slash(provider.address, 1n, validator.address))
          .to.be.revertedWithCustomError(registry, "NotSlasher")
          .withArgs(who.address);
      }
      expect(await registry.stakeOf(provider.address)).to.equal(MIN_STAKE);
    });

    it("rejects admin calls from a stranger", async function () {
      const { registry, stranger } = await loadFixture(deployAll);
      await expect(registry.connect(stranger).setSlasher(stranger.address))
        .to.be.revertedWithCustomError(registry, "NotOwner").withArgs(stranger.address);
      await expect(registry.connect(stranger).setTreasury(stranger.address))
        .to.be.revertedWithCustomError(registry, "NotOwner");
      await expect(registry.connect(stranger).setMinStake(0))
        .to.be.revertedWithCustomError(registry, "NotOwner");
      await expect(registry.connect(stranger).transferOwnership(stranger.address))
        .to.be.revertedWithCustomError(registry, "NotOwner");
    });
  });
});
