"""DEPRECATED. Kept only so existing callers keep working.

The real settlement logic now lives in `edgegrid/ledger.py` (escrow state
machine, challenge window, 80/20 slash split, integer accounting, value
conservation) and `edgegrid/chain.py` (the same surface against the deployed
contracts). This module is a thin adapter over `edgegrid.ledger.Ledger` that
reproduces the old three-method API for `verification/run_integration.py`.

Two behaviours are preserved deliberately, and both are why new code should not
use this shim:

  * `settle()` collapses the whole lifecycle - escrow, commitment, challenge
    window, release or slash - into one call, so nothing here exercises the
    challenge window. It advances a `ManualClock` past it internally.
  * the stake floor is a nominal 0.001 GRID rather than `config.MIN_STAKE`, so
    the old insufficient-stake edge case (a provider holding 0.2 GRID) still
    runs. New code gets the real floor.

New code: `from edgegrid.chain import get_backend` and pick a backend explicitly.
"""

from __future__ import annotations

import csv
import os
import sys
import warnings
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from edgegrid.ledger import (  # noqa: F401  - re-exported for old imports
    Ledger,
    ManualClock,
    centralized_cost,
)
from edgegrid.ledger import EscrowExists
from edgegrid.schemas import VerdictKind, sha256_hex

_LEGACY_FIELDS = ["job_id", "provider_peer_id", "amount", "slashed", "slash_amount", "fully_covered"]

# The stand-in for the caller's off-chain judge. It has to hold active stake,
# because `Ledger.submit_verdict` refuses a verdict from an unstaked validator.
_LEGACY_VALIDATOR = "legacy-validator"
_LEGACY_MIN_STAKE = 0.001


class DuplicateSettlementError(Exception):
    """Raised when a job_id is settled more than once."""


class SimulatedLedger:
    """Legacy façade over `edgegrid.ledger.Ledger`."""

    def __init__(self):
        warnings.warn(
            "contracts.simulate.SimulatedLedger is deprecated; use edgegrid.ledger.Ledger "
            "or edgegrid.chain.get_backend()",
            DeprecationWarning, stacklevel=2)
        self._clock = ManualClock(0)
        # A nominal minimum so the stand-in validator can be `is_active`; the
        # legacy API has no stake floor of its own and every caller registers
        # far more than this.
        self._ledger = Ledger(min_stake=_LEGACY_MIN_STAKE, challenge_window_s=1.0,
                              unbonding_period_s=1.0, clock=self._clock)
        self._ledger.stake(_LEGACY_VALIDATOR, _LEGACY_MIN_STAKE)
        self._ledger.register_validator(_LEGACY_VALIDATOR)
        # Which peers arrived through `register_stake`. The legacy `.stakes`
        # meant "provider collateral", and the stand-in validator is ledger
        # bookkeeping rather than a provider, so it is not shown there.
        self._peers: list[str] = []
        self.records: list[dict] = []

    # -- legacy api ------------------------------------------------------

    @property
    def stakes(self) -> dict[str, float]:
        all_stakes = self._ledger.stakes
        return {p: all_stakes.get(p, 0.0) for p in self._peers}

    def register_stake(self, peer_id: str, amount: float) -> None:
        self._ledger.stake(peer_id, amount)
        if peer_id not in self._peers:
            self._peers.append(peer_id)

    def settle(self, job_id: str, provider_peer_id: str, amount: float, verdict: str) -> dict:
        """Open, commit, and resolve one job in a single call.

        `verdict == "fail"` slashes; anything else pays the provider. The slash
        is still capped at the provider's remaining collateral and still split
        80/20, so the numbers this returns now match the contracts.

        A FAIL goes through `submit_verdict`, the oracle path, because that is
        what the caller actually has: an off-chain judge's assertion. It used to
        go through `prove_data_mismatch` with invented blob bytes, which
        produced the same money movement while labelling an oracle ruling a
        cryptographic proof in the ledger's own records.

        Only a repeat `job_id` becomes `DuplicateSettlementError`; every other
        rejection propagates as the `LedgerError` it is, rather than being
        relabelled as a duplicate.
        """
        if verdict not in ("pass", "fail"):
            raise ValueError(
                f"legacy settle() takes 'pass' or 'fail', got {verdict!r}. A judge ERROR must "
                f"not settle at all - leave the escrow open.")
        try:
            self._ledger.open_escrow(job_id, "legacy-requester", provider_peer_id, amount)
        except EscrowExists as e:
            raise DuplicateSettlementError(f"job {job_id} already settled") from e

        # A real digest, not a placeholder: `Ledger.record_commitment` takes the
        # same bytes32 the contract does, so a legacy scenario stays replayable.
        self._ledger.record_commitment(job_id, provider_peer_id,
                                       output_hash=sha256_hex(f"legacy:{job_id}"), blob_ref="")
        if verdict == "fail":
            rec = self._ledger.submit_verdict(job_id, _LEGACY_VALIDATOR, VerdictKind.FAIL,
                                              reason="legacy judge FAIL")
        else:
            self._clock.advance_s(2.0)
            rec = self._ledger.release(job_id)

        row = {k: getattr(rec, k) for k in _LEGACY_FIELDS}
        self.records.append(row)
        return row

    def total_cost(self) -> float:
        """Sum of amounts actually paid out (excludes slashed jobs)."""
        return self._ledger.total_paid_to_providers()

    def check_invariants(self) -> dict:
        return self._ledger.check_invariants()

    def export_csv(self, path: str) -> None:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=_LEGACY_FIELDS)
            w.writeheader()
            w.writerows(self.records)


def run_demo_scenario() -> None:
    """3 peers, a mix of pass/fail jobs, and the cost comparison."""
    ledger = SimulatedLedger()
    for peer_id in ["peer-1", "peer-2", "peer-3"]:
        ledger.register_stake(peer_id, 10.0)

    jobs = [
        ("job-1", "peer-1", 0.5, "pass"),
        ("job-2", "peer-2", 0.5, "pass"),
        ("job-3", "peer-1", 0.5, "fail"),
        ("job-4", "peer-3", 0.5, "pass"),
    ]
    for job_id, peer_id, amount, verdict in jobs:
        print(ledger.settle(job_id, peer_id, amount, verdict))

    print("\nfinal stakes:", ledger.stakes)
    print("total paid to providers:", ledger.total_cost())
    print("accounting:", ledger.check_invariants())
    print("centralized baseline for the same workload:",
          centralized_cost(num_jobs=len(jobs), tokens_per_job=256, price_per_1k_tokens=0.002))


def run_edge_case_checks() -> None:
    """Insufficient-stake slashing and duplicate-settlement rejection."""
    ledger = SimulatedLedger()
    ledger.register_stake("peer-low-stake", 0.2)

    record = ledger.settle("job-underfunded", "peer-low-stake", amount=0.5, verdict="fail")
    print("insufficient stake case:", record)
    assert record["slash_amount"] == 0.2
    assert record["fully_covered"] is False
    assert ledger.stakes["peer-low-stake"] == 0.0

    ledger.register_stake("peer-2", 10.0)
    ledger.settle("job-x", "peer-2", amount=0.5, verdict="pass")
    try:
        ledger.settle("job-x", "peer-2", amount=0.5, verdict="pass")
        raise AssertionError("expected DuplicateSettlementError")
    except DuplicateSettlementError as e:
        print("duplicate settlement correctly rejected:", e)


if __name__ == "__main__":
    run_demo_scenario()
    print("\n--- edge case checks ---")
    run_edge_case_checks()
