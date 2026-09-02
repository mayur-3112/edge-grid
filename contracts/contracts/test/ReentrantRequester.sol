// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @notice Test-only attacker: a requester contract that tries to re-enter
/// `Marketplace.withdraw` from its receive hook. Exists so the reentrancy
/// property is asserted against a real adversary rather than assumed.
interface IMarketplaceLike {
    function openEscrow(bytes32 jobId, address provider) external payable;
    function cancel(bytes32 jobId) external;
    function withdraw() external;
}

contract ReentrantRequester {
    IMarketplaceLike public immutable marketplace;
    uint256 public reentryAttempts;
    bool public reentryReverted;
    bool public armed;

    constructor(address marketplace_) {
        marketplace = IMarketplaceLike(marketplace_);
    }

    function open(bytes32 jobId, address provider) external payable {
        marketplace.openEscrow{value: msg.value}(jobId, provider);
    }

    function cancel(bytes32 jobId) external {
        marketplace.cancel(jobId);
    }

    function attack() external {
        armed = true;
        marketplace.withdraw();
        armed = false;
    }

    receive() external payable {
        if (!armed) return;
        reentryAttempts += 1;
        armed = false; // one attempt is enough to prove the guard fires
        try marketplace.withdraw() {
            reentryReverted = false;
        } catch {
            reentryReverted = true;
        }
    }
}
