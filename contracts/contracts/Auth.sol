// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @title Owned
/// @notice Minimal two-step-free ownership. Kept in-repo rather than pulling
/// OpenZeppelin so the contract set compiles with no npm dependency beyond
/// Hardhat itself; the surface used here is small enough that the tradeoff is
/// worth the reproducibility.
abstract contract Owned {
    error NotOwner(address caller);
    error ZeroAddress();

    address public owner;

    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    constructor(address initialOwner) {
        if (initialOwner == address(0)) revert ZeroAddress();
        owner = initialOwner;
        emit OwnershipTransferred(address(0), initialOwner);
    }

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner(msg.sender);
        _;
    }

    /// @notice Hand administrative control to `newOwner`.
    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert ZeroAddress();
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }
}

/// @title ReentrancyGuard
/// @notice Single-slot mutex. Every function that moves ETH out of a contract
/// in this set is both pull-payment based and guarded, because the guard alone
/// is not a substitute for checks-effects-interactions.
abstract contract ReentrancyGuard {
    error Reentrant();

    uint256 private constant _UNLOCKED = 1;
    uint256 private constant _LOCKED = 2;
    uint256 private _status = _UNLOCKED;

    modifier nonReentrant() {
        if (_status == _LOCKED) revert Reentrant();
        _status = _LOCKED;
        _;
        _status = _UNLOCKED;
    }
}
