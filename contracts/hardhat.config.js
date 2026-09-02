require("@nomicfoundation/hardhat-ethers");
require("@nomicfoundation/hardhat-chai-matchers");

// `localhost` points at the standalone node started with `npx hardhat node`.
// Deployments against it are what produce the real gas numbers and tx hashes in
// deployment.json; the in-process `hardhat` network is used by the test suite.
module.exports = {
  solidity: { version: "0.8.24", settings: { optimizer: { enabled: true, runs: 200 } } },
  networks: {
    hardhat: { chainId: 31337 },
    localhost: { url: process.env.RPC_URL || "http://127.0.0.1:8545", chainId: 31337 },
  },
};
