const { expect } = require("chai");
const { ethers } = require("hardhat");
const { loadFixture } = require("@nomicfoundation/hardhat-network-helpers");
const { deployAll } = require("./helpers");

const MODEL = ethers.keccak256(ethers.toUtf8Bytes("qwen3-vl:2b-instruct"));
const HASH_A = ethers.keccak256(ethers.toUtf8Bytes("weights-v1"));
const HASH_B = ethers.keccak256(ethers.toUtf8Bytes("weights-v2"));

describe("ModelRegistry", function () {
  it("registers a model and returns its content hash", async function () {
    const { models, provider } = await loadFixture(deployAll);
    await expect(models.connect(provider).registerModel(MODEL, HASH_A, "ollama://qwen3-vl:2b-instruct"))
      .to.emit(models, "ModelRegistered")
      .withArgs(MODEL, provider.address, HASH_A, "ollama://qwen3-vl:2b-instruct");
    expect(await models.contentHashOf(MODEL)).to.equal(HASH_A);
    expect(await models.modelCount()).to.equal(1n);
    expect(await models.modelIdAt(0)).to.equal(MODEL);
  });

  it("rejects a duplicate id and a zero content hash", async function () {
    const { models, provider, stranger } = await loadFixture(deployAll);
    await models.connect(provider).registerModel(MODEL, HASH_A, "u");
    await expect(models.connect(stranger).registerModel(MODEL, HASH_B, "u"))
      .to.be.revertedWithCustomError(models, "ModelExists").withArgs(MODEL);
    await expect(models.connect(provider).registerModel(ethers.ZeroHash, ethers.ZeroHash, "u"))
      .to.be.revertedWithCustomError(models, "ZeroHash");
  });

  it("lets only the publisher update, and bumps the version", async function () {
    const { models, provider, stranger } = await loadFixture(deployAll);
    await models.connect(provider).registerModel(MODEL, HASH_A, "u");
    await expect(models.connect(stranger).updateModel(MODEL, HASH_B, "u2"))
      .to.be.revertedWithCustomError(models, "NotPublisher")
      .withArgs(MODEL, stranger.address, provider.address);

    await expect(models.connect(provider).updateModel(MODEL, HASH_B, "u2"))
      .to.emit(models, "ModelUpdated").withArgs(MODEL, HASH_A, HASH_B, 2);
    expect((await models.models(MODEL)).version).to.equal(2n);
    expect(await models.contentHashOf(MODEL)).to.equal(HASH_B);
  });

  it("distinguishes an unknown model from a revoked one", async function () {
    const { models, provider } = await loadFixture(deployAll);
    const unknown = ethers.keccak256(ethers.toUtf8Bytes("nope"));
    await expect(models.contentHashOf(unknown))
      .to.be.revertedWithCustomError(models, "NoModel").withArgs(unknown);

    await models.connect(provider).registerModel(MODEL, HASH_A, "u");
    await models.connect(provider).revokeModel(MODEL);
    await expect(models.contentHashOf(MODEL))
      .to.be.revertedWithCustomError(models, "ModelRevoked").withArgs(MODEL);
    await expect(models.connect(provider).updateModel(MODEL, HASH_B, "u"))
      .to.be.revertedWithCustomError(models, "ModelRevoked");
  });

  it("transfers publishing rights", async function () {
    const { models, provider, stranger } = await loadFixture(deployAll);
    await models.connect(provider).registerModel(MODEL, HASH_A, "u");
    await expect(models.connect(provider).transferPublisher(MODEL, stranger.address))
      .to.emit(models, "PublisherTransferred").withArgs(MODEL, provider.address, stranger.address);
    await expect(models.connect(provider).updateModel(MODEL, HASH_B, "u"))
      .to.be.revertedWithCustomError(models, "NotPublisher");
    await expect(models.connect(stranger).updateModel(MODEL, HASH_B, "u")).to.not.be.reverted;
  });
});
