// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Owned, ReentrancyGuard} from "./Auth.sol";
import {EscrowState, IMarketplace, INodeRegistry} from "./Interfaces.sol";

/// @title VerificationContract
/// @notice Records what a provider claims it produced, and is the only address
/// allowed to slash that provider or refund its requester.
///
/// There are two ways fraud gets confirmed here and they carry very different
/// trust assumptions. Both are on chain and neither is silent about which one
/// fired:
///
///  * `proveDataMismatch` is trustless and permissionless. The challenger
///    reveals the DA blob and a Merkle path; the EVM recomputes the block root
///    and the blob hash itself. If the data actually sitting under the
///    provider's committed root does not hash to the output hash the provider
///    put on chain, fraud is proven by arithmetic and nobody has to be trusted.
///  * `submitVerdict` is an oracle. It carries the off-chain LLM judge's
///    ruling and is restricted to allow-listed, staked validators, because a
///    verdict is an assertion rather than a proof. The earlier sketch let any
///    address write any verdict; that is the hole this closes.
///
/// The Merkle scheme is byte-compatible with `edgegrid/da.py`: leaves are
/// sha256(0x00 || data), internal nodes sha256(0x01 || left || right), and an
/// odd tail is duplicated. Sibling ordering is derived from the committed leaf
/// index rather than taken from the caller, so a challenger cannot substitute
/// some other job's blob from the same DA block.
contract VerificationContract is Owned, ReentrancyGuard {
    // -- errors ------------------------------------------------------------

    error CommitmentExists(bytes32 jobId);
    error NoCommitment(bytes32 jobId);
    error NotAwardedProvider(bytes32 jobId, address caller, address provider);
    error EscrowNotOpen(bytes32 jobId, EscrowState state);
    error AlreadyResolved(bytes32 jobId);
    error ChallengeWindowClosed(bytes32 jobId, uint64 deadline, uint64 nowTs);
    error BadInclusionProof(bytes32 expectedRoot, bytes32 computedRoot);
    error NoMismatch(bytes32 jobId, bytes32 outputHash);
    error NotValidator(address caller);
    error ValidatorNotStaked(address caller);
    error ZeroWindow();

    // -- types -------------------------------------------------------------

    /// @notice Mirrors `edgegrid.schemas.VerdictKind`. ERROR is a first-class
    /// outcome: a judge outage is never recorded as a pass or as fraud.
    enum VerdictKind {
        NONE,
        PASS,
        FAIL,
        ERROR
    }

    /// @notice How a resolution was reached, so a settlement row can always say
    /// whether it rests on a proof or on an oracle.
    enum ResolutionKind {
        NONE,
        DATA_MISMATCH_PROOF,
        VALIDATOR_VERDICT
    }

    struct Commitment {
        address provider;
        bytes32 outputHash;
        bytes32 merkleRoot;
        uint32 leafIndex;
        uint64 recordedAt;
        uint64 challengeDeadline;
        bool resolved;
        ResolutionKind resolution;
        string blobRef;
    }

    // -- storage -----------------------------------------------------------

    INodeRegistry public immutable registry;
    IMarketplace public immutable marketplace;

    /// @notice Seconds a commitment stays challengeable. Immutable so it cannot
    /// be shortened out from under an in-flight challenge.
    uint64 public immutable challengeWindow;

    mapping(bytes32 => Commitment) public commitments;
    mapping(bytes32 => VerdictKind) public verdicts;
    mapping(address => bool) public isValidator;

    uint256 public totalCommitments;
    uint256 public totalFraudConfirmed;

    // -- events ------------------------------------------------------------

    event CommitmentRecorded(
        bytes32 indexed jobId,
        address indexed provider,
        bytes32 outputHash,
        bytes32 merkleRoot,
        uint32 leafIndex,
        string blobRef,
        uint64 challengeDeadline
    );
    event VerdictRecorded(
        bytes32 indexed jobId,
        address indexed validator,
        VerdictKind verdict,
        uint8 qualityScore,
        bytes32 reasonHash
    );
    event FraudConfirmed(
        bytes32 indexed jobId,
        address indexed provider,
        address indexed reporter,
        ResolutionKind kind,
        uint256 slashed,
        uint256 validatorReward,
        uint256 treasuryAmount,
        bool fullyCovered
    );
    event ValidatorUpdated(address indexed validator, bool allowed);

    // -- construction ------------------------------------------------------

    constructor(address registry_, address marketplace_, uint64 challengeWindow_) Owned(msg.sender) {
        if (registry_ == address(0) || marketplace_ == address(0)) revert ZeroAddress();
        if (challengeWindow_ == 0) revert ZeroWindow();
        registry = INodeRegistry(registry_);
        marketplace = IMarketplace(marketplace_);
        challengeWindow = challengeWindow_;
    }

    modifier onlyValidator() {
        if (!isValidator[msg.sender]) revert NotValidator(msg.sender);
        if (!registry.isActive(msg.sender)) revert ValidatorNotStaked(msg.sender);
        _;
    }

    // -- admin -------------------------------------------------------------

    /// @notice Add or remove a validator from the verdict allow-list. A
    /// validator must additionally hold active stake to submit a verdict.
    function setValidator(address validator, bool allowed) external onlyOwner {
        if (validator == address(0)) revert ZeroAddress();
        isValidator[validator] = allowed;
        emit ValidatorUpdated(validator, allowed);
    }

    // -- commitment --------------------------------------------------------

    /// @notice Bind the provider to one output for `jobId` and start the
    /// challenge window.
    /// @dev Only the provider the marketplace actually awarded may commit, and
    /// only while the escrow is OPEN. `outputHash` is asserted to be the sha256
    /// of the DA blob stored at `leafIndex` under `merkleRoot`; that assertion
    /// is exactly what `proveDataMismatch` can later refute on chain.
    /// @param jobId Job identifier, keccak256 of the off-chain job_id string.
    /// @param outputHash sha256 of the DA blob bytes.
    /// @param merkleRoot Root of the DA block that includes the blob.
    /// @param leafIndex Position of the blob within that DA block.
    /// @param blobRef DA blob id, for off-chain retrieval.
    function recordCommitment(
        bytes32 jobId,
        bytes32 outputHash,
        bytes32 merkleRoot,
        uint32 leafIndex,
        string calldata blobRef
    ) external {
        if (commitments[jobId].provider != address(0)) revert CommitmentExists(jobId);

        address provider = marketplace.escrowProvider(jobId);
        EscrowState state = marketplace.escrowState(jobId);
        if (state != EscrowState.OPEN) revert EscrowNotOpen(jobId, state);
        if (msg.sender != provider) revert NotAwardedProvider(jobId, msg.sender, provider);

        uint64 deadline = uint64(block.timestamp) + challengeWindow;
        commitments[jobId] = Commitment({
            provider: provider,
            outputHash: outputHash,
            merkleRoot: merkleRoot,
            leafIndex: leafIndex,
            recordedAt: uint64(block.timestamp),
            challengeDeadline: deadline,
            resolved: false,
            resolution: ResolutionKind.NONE,
            blobRef: blobRef
        });
        totalCommitments += 1;

        marketplace.beginVerification(jobId, deadline);
        emit CommitmentRecorded(jobId, provider, outputHash, merkleRoot, leafIndex, blobRef, deadline);
    }

    // -- fraud proof (trustless) -------------------------------------------

    /// @notice Refute a commitment by revealing the DA blob it actually points
    /// at. Permissionless - correctness is checked by the EVM, not asserted.
    /// @dev Reverts with `NoMismatch` when the revealed blob does hash to the
    /// committed output hash, so an honest provider cannot be slashed by a
    /// well-formed but truthful challenge.
    /// @param jobId The job being challenged.
    /// @param blobData The raw DA blob bytes.
    /// @param siblings Merkle sibling path, leaf-to-root, as produced by
    /// `edgegrid.da.merkle_proof`.
    function proveDataMismatch(bytes32 jobId, bytes calldata blobData, bytes32[] calldata siblings)
        external
        nonReentrant
    {
        Commitment storage c = _challengeable(jobId);

        bytes32 computed = _computeRoot(sha256(abi.encodePacked(bytes1(0x00), blobData)), c.leafIndex, siblings);
        if (computed != c.merkleRoot) revert BadInclusionProof(c.merkleRoot, computed);

        if (sha256(blobData) == c.outputHash) revert NoMismatch(jobId, c.outputHash);

        _confirmFraud(jobId, c, msg.sender, ResolutionKind.DATA_MISMATCH_PROOF);
    }

    // -- verdict (oracle) ---------------------------------------------------

    /// @notice Record an off-chain judge's ruling. FAIL confirms fraud and
    /// slashes; PASS and ERROR are recorded and leave the escrow to settle
    /// normally when the window closes.
    /// @param qualityScore 1-5 rubric score, 0 when not applicable.
    /// @param reasonHash keccak256 of the judge's free-text reason, so the
    /// off-chain record can be tied to this transaction.
    function submitVerdict(bytes32 jobId, VerdictKind verdict, uint8 qualityScore, bytes32 reasonHash)
        external
        onlyValidator
        nonReentrant
    {
        Commitment storage c = _challengeable(jobId);
        verdicts[jobId] = verdict;
        emit VerdictRecorded(jobId, msg.sender, verdict, qualityScore, reasonHash);

        if (verdict == VerdictKind.FAIL) {
            _confirmFraud(jobId, c, msg.sender, ResolutionKind.VALIDATOR_VERDICT);
        }
    }

    // -- internals ---------------------------------------------------------

    function _challengeable(bytes32 jobId) private view returns (Commitment storage c) {
        c = commitments[jobId];
        if (c.provider == address(0)) revert NoCommitment(jobId);
        if (c.resolved) revert AlreadyResolved(jobId);
        if (block.timestamp > c.challengeDeadline) {
            revert ChallengeWindowClosed(jobId, c.challengeDeadline, uint64(block.timestamp));
        }
    }

    /// @dev Slash the provider for the escrowed amount (capped at its remaining
    /// collateral), split 80/20 to reporter and treasury, and return the escrow
    /// to the requester. Effects are written before either external call and
    /// both callees are the trusted sibling contracts.
    function _confirmFraud(bytes32 jobId, Commitment storage c, address reporter, ResolutionKind kind) private {
        c.resolved = true;
        c.resolution = kind;
        totalFraudConfirmed += 1;

        uint256 amount = marketplace.escrowAmount(jobId);
        (uint256 slashed, uint256 validatorReward, uint256 treasuryAmount, bool fullyCovered) =
            registry.slash(c.provider, amount, reporter);
        marketplace.refundOnFraud(jobId);

        emit FraudConfirmed(
            jobId, c.provider, reporter, kind, slashed, validatorReward, treasuryAmount, fullyCovered
        );
    }

    /// @dev Fold a sibling path into a root. Direction comes from `leafIndex`,
    /// never from the caller; the duplicated odd tail hashes a node with itself
    /// exactly as `edgegrid.da.merkle_root` does.
    function _computeRoot(bytes32 leaf, uint256 index, bytes32[] calldata siblings)
        private
        pure
        returns (bytes32 h)
    {
        h = leaf;
        uint256 idx = index;
        for (uint256 i = 0; i < siblings.length; i++) {
            h = (idx & 1) == 0
                ? sha256(abi.encodePacked(bytes1(0x01), h, siblings[i]))
                : sha256(abi.encodePacked(bytes1(0x01), siblings[i], h));
            idx >>= 1;
        }
    }

    // -- views -------------------------------------------------------------

    function commitmentOf(bytes32 jobId) external view returns (Commitment memory) {
        return commitments[jobId];
    }

    function isResolved(bytes32 jobId) external view returns (bool) {
        return commitments[jobId].resolved;
    }
}
