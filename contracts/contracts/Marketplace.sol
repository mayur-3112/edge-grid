// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Owned, ReentrancyGuard} from "./Auth.sol";
import {EscrowState, INodeRegistry} from "./Interfaces.sol";

/// @title Marketplace
/// @notice Per-job escrow with an explicit state machine:
///
///     OPEN ---(provider commits)---> AWAITING_VERIFICATION
///     AWAITING_VERIFICATION ---(challenge window elapses)---> SETTLED
///     AWAITING_VERIFICATION ---(fraud confirmed)-----------> SLASHED
///     OPEN ---(no commitment before the award timeout)-----> REFUNDED
///
/// The earlier sketch had a single `settled` flag and a world-callable
/// `settle(jobId, slashed)` - any account could declare any job fraudulent and
/// send the escrow back to the requester. Here the only transition an arbitrary
/// caller can trigger is the honest one, and only after the challenge window
/// has actually expired.
///
/// Value never leaves inside a state transition: payouts are credited to
/// `withdrawable` and pulled by their owner, so a malicious requester or
/// provider contract cannot re-enter the settlement path.
contract Marketplace is Owned, ReentrancyGuard {
    // -- errors ------------------------------------------------------------

    error EscrowExists(bytes32 jobId);
    error NoEscrow(bytes32 jobId);
    error WrongState(bytes32 jobId, EscrowState actual, EscrowState expected);
    error NotVerificationContract(address caller);
    error NotRequester(address caller, address requester);
    error ChallengeWindowOpen(uint64 deadline, uint64 nowTs);
    error AwardWindowOpen(uint64 deadline, uint64 nowTs);
    error ProviderNotActive(address provider);
    error ZeroAmount();
    error NothingToWithdraw(address account);
    error TransferFailed(address to, uint256 amount);

    // -- storage -----------------------------------------------------------

    struct Escrow {
        address requester;
        address provider;
        uint256 amount;
        uint64 openedAt;
        uint64 challengeDeadline;
        EscrowState state;
    }

    INodeRegistry public immutable registry;

    /// @notice How long a requester must wait before reclaiming an escrow that
    /// the provider never committed against.
    uint64 public awardTimeout;

    address public verificationContract;

    mapping(bytes32 => Escrow) public escrows;
    mapping(address => uint256) public withdrawable;

    uint256 public totalEscrowed;
    uint256 public totalPaidToProviders;
    uint256 public totalRefundedToRequesters;

    // -- events ------------------------------------------------------------

    event EscrowOpened(bytes32 indexed jobId, address indexed requester, address indexed provider, uint256 amount);
    event VerificationBegun(bytes32 indexed jobId, uint64 challengeDeadline);
    event EscrowSettled(bytes32 indexed jobId, address indexed provider, uint256 amount);
    event EscrowSlashed(bytes32 indexed jobId, address indexed requester, uint256 amount);
    event EscrowRefunded(bytes32 indexed jobId, address indexed requester, uint256 amount);
    event Withdrawn(address indexed account, uint256 amount);
    event VerificationContractUpdated(address indexed previous, address indexed next);
    event AwardTimeoutUpdated(uint64 previous, uint64 next);

    // -- construction ------------------------------------------------------

    constructor(address registry_, uint64 awardTimeout_) Owned(msg.sender) {
        if (registry_ == address(0)) revert ZeroAddress();
        registry = INodeRegistry(registry_);
        awardTimeout = awardTimeout_;
    }

    modifier onlyVerification() {
        if (msg.sender != verificationContract) revert NotVerificationContract(msg.sender);
        _;
    }

    // -- admin -------------------------------------------------------------

    /// @notice Point the marketplace at the VerificationContract permitted to
    /// advance escrows out of OPEN and to refund on confirmed fraud.
    function setVerificationContract(address next) external onlyOwner {
        if (next == address(0)) revert ZeroAddress();
        emit VerificationContractUpdated(verificationContract, next);
        verificationContract = next;
    }

    /// @notice Change how long a requester waits before cancelling an
    /// uncommitted job.
    function setAwardTimeout(uint64 next) external onlyOwner {
        emit AwardTimeoutUpdated(awardTimeout, next);
        awardTimeout = next;
    }

    // -- lifecycle ---------------------------------------------------------

    /// @notice Lock the clearing price for `jobId` against `provider`.
    /// @dev The provider must be an active staked node, otherwise a slash on
    /// confirmed fraud would have nothing to bite.
    function openEscrow(bytes32 jobId, address provider) external payable {
        if (msg.value == 0) revert ZeroAmount();
        if (escrows[jobId].state != EscrowState.NONE) revert EscrowExists(jobId);
        if (!registry.isActive(provider)) revert ProviderNotActive(provider);

        escrows[jobId] = Escrow({
            requester: msg.sender,
            provider: provider,
            amount: msg.value,
            openedAt: uint64(block.timestamp),
            challengeDeadline: 0,
            state: EscrowState.OPEN
        });
        totalEscrowed += msg.value;

        emit EscrowOpened(jobId, msg.sender, provider, msg.value);
    }

    /// @notice Move an escrow to AWAITING_VERIFICATION once the provider has
    /// recorded its output commitment on chain.
    function beginVerification(bytes32 jobId, uint64 challengeDeadline) external onlyVerification {
        Escrow storage e = escrows[jobId];
        if (e.state == EscrowState.NONE) revert NoEscrow(jobId);
        if (e.state != EscrowState.OPEN) revert WrongState(jobId, e.state, EscrowState.OPEN);

        e.state = EscrowState.AWAITING_VERIFICATION;
        e.challengeDeadline = challengeDeadline;

        emit VerificationBegun(jobId, challengeDeadline);
    }

    /// @notice Release the escrow to the provider once the challenge window has
    /// closed with no confirmed fraud. Permissionless: the honest outcome must
    /// not depend on any privileged party staying online.
    function release(bytes32 jobId) external {
        Escrow storage e = escrows[jobId];
        if (e.state == EscrowState.NONE) revert NoEscrow(jobId);
        if (e.state != EscrowState.AWAITING_VERIFICATION) {
            revert WrongState(jobId, e.state, EscrowState.AWAITING_VERIFICATION);
        }
        if (block.timestamp < e.challengeDeadline) {
            revert ChallengeWindowOpen(e.challengeDeadline, uint64(block.timestamp));
        }

        e.state = EscrowState.SETTLED;
        uint256 amount = e.amount;
        withdrawable[e.provider] += amount;
        totalPaidToProviders += amount;

        emit EscrowSettled(jobId, e.provider, amount);
    }

    /// @notice Return the escrow to the requester after fraud is confirmed.
    /// Callable only by the VerificationContract.
    function refundOnFraud(bytes32 jobId) external onlyVerification {
        Escrow storage e = escrows[jobId];
        if (e.state == EscrowState.NONE) revert NoEscrow(jobId);
        if (e.state != EscrowState.AWAITING_VERIFICATION) {
            revert WrongState(jobId, e.state, EscrowState.AWAITING_VERIFICATION);
        }

        e.state = EscrowState.SLASHED;
        uint256 amount = e.amount;
        withdrawable[e.requester] += amount;
        totalRefundedToRequesters += amount;

        emit EscrowSlashed(jobId, e.requester, amount);
    }

    /// @notice Reclaim an escrow whose provider never produced a commitment.
    function cancel(bytes32 jobId) external {
        Escrow storage e = escrows[jobId];
        if (e.state == EscrowState.NONE) revert NoEscrow(jobId);
        if (e.state != EscrowState.OPEN) revert WrongState(jobId, e.state, EscrowState.OPEN);
        if (msg.sender != e.requester) revert NotRequester(msg.sender, e.requester);

        uint64 deadline = e.openedAt + awardTimeout;
        if (block.timestamp < deadline) revert AwardWindowOpen(deadline, uint64(block.timestamp));

        e.state = EscrowState.REFUNDED;
        uint256 amount = e.amount;
        withdrawable[e.requester] += amount;
        totalRefundedToRequesters += amount;

        emit EscrowRefunded(jobId, e.requester, amount);
    }

    // -- withdrawal --------------------------------------------------------

    /// @notice Pull a settled payout or refund.
    function withdraw() external nonReentrant {
        uint256 amount = withdrawable[msg.sender];
        if (amount == 0) revert NothingToWithdraw(msg.sender);
        withdrawable[msg.sender] = 0;
        (bool ok, ) = msg.sender.call{value: amount}("");
        if (!ok) revert TransferFailed(msg.sender, amount);
        emit Withdrawn(msg.sender, amount);
    }

    // -- views -------------------------------------------------------------

    function escrowAmount(bytes32 jobId) external view returns (uint256) {
        return escrows[jobId].amount;
    }

    function escrowProvider(bytes32 jobId) external view returns (address) {
        return escrows[jobId].provider;
    }

    function escrowState(bytes32 jobId) external view returns (EscrowState) {
        return escrows[jobId].state;
    }

    function challengeDeadlineOf(bytes32 jobId) external view returns (uint64) {
        return escrows[jobId].challengeDeadline;
    }
}
