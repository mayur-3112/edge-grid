// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {Owned, ReentrancyGuard} from "./Auth.sol";

/// @title NodeRegistry
/// @notice Holds provider collateral for The Edge Grid and is the only place
/// where stake is created or destroyed.
///
/// Two properties matter here and neither existed in the earlier sketch:
///
///  1. Slashing is callable by exactly one address - the VerificationContract.
///     Anyone else calling `slash` reverts. The previous version had no access
///     control at all, so any account could confiscate any provider's stake.
///  2. Stake stays slashable while it is unbonding. A provider who sees a fraud
///     proof coming cannot escape it by unstaking, because `requestUnstake`
///     moves collateral into a timelocked bucket that `slash` still reaches.
///
/// All value leaves through `withdraw` (pull payment), never through a push in
/// the middle of another state transition.
contract NodeRegistry is Owned, ReentrancyGuard {
    // -- errors ------------------------------------------------------------

    error BelowMinimumStake(uint256 resulting, uint256 required);
    error NotSlasher(address caller);
    error InsufficientStake(address node, uint256 requested, uint256 available);
    error NothingToWithdraw(address account);
    error NothingUnbonding(address account);
    error UnbondingNotReady(uint64 readyAt, uint64 nowTs);
    error TransferFailed(address to, uint256 amount);
    error ZeroAmount();
    error BadShare(uint256 bps);

    // -- constants ---------------------------------------------------------

    uint256 public constant BPS = 10_000;

    /// @notice Share of every slash paid to the account that reported the
    /// fraud. The remainder goes to the DAO treasury, so the two always sum to
    /// the slashed amount with no dust left behind.
    uint256 public constant VALIDATOR_SLASH_BPS = 8_000;

    // -- storage -----------------------------------------------------------

    struct Unbonding {
        uint256 amount;
        uint64 readyAt;
    }

    uint256 public minStake;
    uint64 public unbondingPeriod;

    /// @notice The VerificationContract. The only address permitted to slash.
    address public slasher;
    address public treasury;

    mapping(address => uint256) public stakeOf;
    mapping(address => Unbonding) public unbonding;
    mapping(address => uint256) public withdrawable;

    uint256 public totalStaked;
    uint256 public totalUnbonding;
    uint256 public totalSlashed;

    // -- events ------------------------------------------------------------

    event Staked(address indexed node, uint256 amount, uint256 newStake);
    event UnstakeRequested(address indexed node, uint256 amount, uint64 readyAt);
    event UnstakeClaimed(address indexed node, uint256 amount);
    event Slashed(
        address indexed node,
        address indexed reporter,
        uint256 slashed,
        uint256 validatorReward,
        uint256 treasuryAmount,
        bool fullyCovered
    );
    event Withdrawn(address indexed account, uint256 amount);
    event SlasherUpdated(address indexed previous, address indexed next);
    event TreasuryUpdated(address indexed previous, address indexed next);
    event MinStakeUpdated(uint256 previous, uint256 next);
    event UnbondingPeriodUpdated(uint64 previous, uint64 next);

    // -- construction ------------------------------------------------------

    constructor(uint256 minStake_, address treasury_, uint64 unbondingPeriod_) Owned(msg.sender) {
        if (treasury_ == address(0)) revert ZeroAddress();
        minStake = minStake_;
        treasury = treasury_;
        unbondingPeriod = unbondingPeriod_;
    }

    modifier onlySlasher() {
        if (msg.sender != slasher) revert NotSlasher(msg.sender);
        _;
    }

    // -- admin -------------------------------------------------------------

    /// @notice Point the registry at the VerificationContract allowed to slash.
    function setSlasher(address next) external onlyOwner {
        if (next == address(0)) revert ZeroAddress();
        emit SlasherUpdated(slasher, next);
        slasher = next;
    }

    /// @notice Update the DAO treasury that receives the non-reporter share.
    function setTreasury(address next) external onlyOwner {
        if (next == address(0)) revert ZeroAddress();
        emit TreasuryUpdated(treasury, next);
        treasury = next;
    }

    /// @notice Change the collateral floor for an active provider.
    function setMinStake(uint256 next) external onlyOwner {
        emit MinStakeUpdated(minStake, next);
        minStake = next;
    }

    /// @notice Change how long withdrawn collateral stays slashable.
    function setUnbondingPeriod(uint64 next) external onlyOwner {
        emit UnbondingPeriodUpdated(unbondingPeriod, next);
        unbondingPeriod = next;
    }

    // -- staking -----------------------------------------------------------

    /// @notice Post collateral. The resulting balance must clear `minStake`,
    /// so a node cannot sit half-collateralised and still be biddable.
    function stake() external payable {
        if (msg.value == 0) revert ZeroAmount();
        uint256 next = stakeOf[msg.sender] + msg.value;
        if (next < minStake) revert BelowMinimumStake(next, minStake);
        stakeOf[msg.sender] = next;
        totalStaked += msg.value;
        emit Staked(msg.sender, msg.value, next);
    }

    /// @notice Begin withdrawing `amount`. The collateral remains slashable
    /// until `unbondingPeriod` has elapsed and `claimUnstake` is called, which
    /// is what stops a provider from front-running a fraud proof.
    /// @dev The remaining active stake must be either zero or still above the
    /// minimum; partial withdrawals cannot leave a node under-collateralised.
    function requestUnstake(uint256 amount) external {
        if (amount == 0) revert ZeroAmount();
        uint256 current = stakeOf[msg.sender];
        if (amount > current) revert InsufficientStake(msg.sender, amount, current);
        uint256 remaining = current - amount;
        if (remaining != 0 && remaining < minStake) revert BelowMinimumStake(remaining, minStake);

        stakeOf[msg.sender] = remaining;
        totalStaked -= amount;

        Unbonding storage u = unbonding[msg.sender];
        u.amount += amount;
        u.readyAt = uint64(block.timestamp) + unbondingPeriod;
        totalUnbonding += amount;

        emit UnstakeRequested(msg.sender, amount, u.readyAt);
    }

    /// @notice Move matured unbonding collateral into the withdrawable balance.
    function claimUnstake() external {
        Unbonding storage u = unbonding[msg.sender];
        uint256 amount = u.amount;
        if (amount == 0) revert NothingUnbonding(msg.sender);
        if (block.timestamp < u.readyAt) revert UnbondingNotReady(u.readyAt, uint64(block.timestamp));

        u.amount = 0;
        u.readyAt = 0;
        totalUnbonding -= amount;
        withdrawable[msg.sender] += amount;

        emit UnstakeClaimed(msg.sender, amount);
    }

    // -- slashing ----------------------------------------------------------

    /// @notice Confiscate up to `amount` of `node`'s collateral and split it
    /// 80/20 between `reporter` and the treasury.
    /// @dev Callable only by the VerificationContract. Active stake is consumed
    /// before unbonding collateral. If the node has less than `amount` left the
    /// slash is capped at the balance and `fullyCovered` comes back false -
    /// under-collateralisation is reported, never silently rounded away.
    /// @return slashed The amount actually confiscated.
    /// @return validatorReward The reporter's 80% share.
    /// @return treasuryAmount The remainder credited to the treasury.
    /// @return fullyCovered False when the node could not cover `amount`.
    function slash(address node, uint256 amount, address reporter)
        external
        onlySlasher
        returns (uint256 slashed, uint256 validatorReward, uint256 treasuryAmount, bool fullyCovered)
    {
        if (reporter == address(0)) revert ZeroAddress();

        uint256 active = stakeOf[node];
        Unbonding storage u = unbonding[node];
        uint256 available = active + u.amount;

        slashed = amount > available ? available : amount;
        fullyCovered = slashed == amount;

        if (slashed > 0) {
            uint256 fromActive = slashed > active ? active : slashed;
            if (fromActive > 0) {
                stakeOf[node] = active - fromActive;
                totalStaked -= fromActive;
            }
            uint256 fromUnbonding = slashed - fromActive;
            if (fromUnbonding > 0) {
                u.amount -= fromUnbonding;
                totalUnbonding -= fromUnbonding;
            }

            validatorReward = (slashed * VALIDATOR_SLASH_BPS) / BPS;
            treasuryAmount = slashed - validatorReward;
            withdrawable[reporter] += validatorReward;
            withdrawable[treasury] += treasuryAmount;
            totalSlashed += slashed;
        }

        emit Slashed(node, reporter, slashed, validatorReward, treasuryAmount, fullyCovered);
    }

    // -- withdrawal --------------------------------------------------------

    /// @notice Pull the caller's credited balance: matured unstakes, validator
    /// rewards, treasury share.
    function withdraw() external nonReentrant {
        uint256 amount = withdrawable[msg.sender];
        if (amount == 0) revert NothingToWithdraw(msg.sender);
        withdrawable[msg.sender] = 0;
        (bool ok, ) = msg.sender.call{value: amount}("");
        if (!ok) revert TransferFailed(msg.sender, amount);
        emit Withdrawn(msg.sender, amount);
    }

    // -- views -------------------------------------------------------------

    /// @notice True when the node holds at least the minimum active stake.
    function isActive(address node) external view returns (bool) {
        return stakeOf[node] > 0 && stakeOf[node] >= minStake;
    }

    /// @notice Everything a slash can still reach: active plus unbonding.
    function slashableOf(address node) external view returns (uint256) {
        return stakeOf[node] + unbonding[node].amount;
    }
}
