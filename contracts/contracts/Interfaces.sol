// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @notice The escrow lifecycle. Index order is part of the cross-language
/// contract: `edgegrid.ledger.EscrowState` mirrors these values exactly, and
/// `NONE` is the zero value meaning "no escrow was ever opened for this job".
enum EscrowState {
    NONE,
    OPEN,
    AWAITING_VERIFICATION,
    SETTLED,
    SLASHED,
    REFUNDED
}

interface INodeRegistry {
    function isActive(address node) external view returns (bool);
    function stakeOf(address node) external view returns (uint256);
    function slashableOf(address node) external view returns (uint256);
    function slash(address node, uint256 amount, address reporter)
        external
        returns (uint256 slashed, uint256 validatorReward, uint256 treasuryAmount, bool fullyCovered);
}

interface IMarketplace {
    function escrowAmount(bytes32 jobId) external view returns (uint256);
    function escrowProvider(bytes32 jobId) external view returns (address);
    function escrowState(bytes32 jobId) external view returns (EscrowState);
    function beginVerification(bytes32 jobId, uint64 challengeDeadline) external;
    function refundOnFraud(bytes32 jobId) external;
}
