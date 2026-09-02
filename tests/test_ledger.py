"""Tests for the settlement ledger.

The property test at the bottom is the one that matters: the previous ledger
looked correct on the four jobs its demo happened to run and silently failed to
move any value at all. Randomised sequences with the conservation invariant
checked after every single operation - accepted or rejected - is the cheapest
way to make that class of bug impossible to reintroduce.
"""

from __future__ import annotations

import hashlib
import itertools
import random
from types import SimpleNamespace

import pytest

from edgegrid import da
from edgegrid.ledger import (
    AlreadyResolved,
    AwardWindowOpen,
    BadInclusionProof,
    BelowMinimumStake,
    ChallengeWindowClosed,
    ChallengeWindowOpen,
    CommitmentExists,
    EscrowExists,
    InsufficientStake,
    InvariantViolation,
    Ledger,
    LedgerError,
    ManualClock,
    MissingInclusionProof,
    NoEscrow,
    NotAwardedProvider,
    NotRequester,
    NoMismatch,
    NotValidator,
    NothingToWithdraw,
    ProviderNotActive,
    UnbondingNotReady,
    WrongState,
    from_wei,
    to_wei,
)
from edgegrid.schemas import EscrowState, VerdictKind

PRICE = 0.05
STAKE = 10.0


@pytest.fixture
def clock():
    return ManualClock(0)


@pytest.fixture
def ledger(clock):
    L = Ledger(min_stake=STAKE, challenge_window_s=60, award_timeout_s=30,
               unbonding_period_s=60, clock=clock)
    L.stake("provider", STAKE)
    L.stake("validator", STAKE)
    L.register_validator("validator")
    return L


@pytest.fixture
def commit(tmp_path):
    """Record a commitment bound to a *real* DA block and hand back everything a
    challenger would need.

    Every commitment in these tests goes through a genuine three-blob Merkle
    block because `prove_data_mismatch` now requires the inclusion proof the
    EVM requires. Committing to a bare hash with no root, as these tests used
    to, exercised a path the chain does not have.
    """
    counter = itertools.count()

    def make(ledger, job_id, *, provider="provider",
             stored=b"the capital of France is Paris", claimed=None,
             fillers=(b"neighbour one", b"neighbour two")):
        layer = da.DALayer(root_dir=tmp_path / f"da-{next(counter)}")
        layer.submit_blob(fillers[0], seal=False)
        blob = layer.submit_blob(stored, seal=False)
        for f in fillers[1:]:
            layer.submit_blob(f, seal=False)
        layer.seal_block()
        meta = layer.get_blob_meta(blob.blob_id)
        proof, root = layer.inclusion_proof(blob.blob_id)
        output_hash = meta.commitment if claimed is None else claimed
        entry = ledger.record_commitment(job_id, provider, output_hash,
                                         blob_ref=meta.blob_id, merkle_root=root,
                                         leaf_index=meta.index)
        return SimpleNamespace(entry=entry, data=meta.data, proof=proof, root=root,
                               blob_id=meta.blob_id, index=meta.index,
                               output_hash=output_hash, layer=layer)

    return make


WRONG = hashlib.sha256(b"what the provider claimed instead").hexdigest()


# --------------------------------------------------------------------------
# units
# --------------------------------------------------------------------------

class TestUnits:
    def test_round_trips_through_wei(self):
        for grid in (0.0, 0.05, 1.0, 10.0, 1234.5678):
            assert from_wei(to_wei(grid)) == pytest.approx(grid)

    def test_rejects_negative_and_non_finite(self):
        with pytest.raises(ValueError):
            to_wei(-1.0)
        with pytest.raises(ValueError):
            to_wei(float("nan"))


# --------------------------------------------------------------------------
# staking
# --------------------------------------------------------------------------

class TestStaking:
    def test_below_minimum_is_rejected(self, ledger):
        with pytest.raises(BelowMinimumStake):
            ledger.stake("newcomer", STAKE - 0.1)
        assert ledger.is_active("newcomer") is False

    def test_unstake_is_timelocked_and_stays_slashable(self, ledger, clock):
        ledger.stake("provider", STAKE)             # 20 total
        ledger.request_unstake("provider", STAKE)
        assert ledger.stakes["provider"] == STAKE
        assert ledger.slashable_of("provider") == 2 * STAKE
        with pytest.raises(UnbondingNotReady):
            ledger.claim_unstake("provider")
        clock.advance_s(61)
        assert ledger.claim_unstake("provider") == STAKE
        assert ledger.withdraw("provider") == STAKE
        ledger.check_invariants()

    def test_partial_unstake_cannot_drop_below_the_floor(self, ledger):
        with pytest.raises(BelowMinimumStake):
            ledger.request_unstake("provider", 0.1)
        with pytest.raises(InsufficientStake):
            ledger.request_unstake("provider", STAKE * 2)
        ledger.request_unstake("provider", STAKE)   # full exit is allowed
        ledger.check_invariants()

    def test_withdraw_with_no_balance_raises(self, ledger):
        with pytest.raises(NothingToWithdraw):
            ledger.withdraw("nobody")


# --------------------------------------------------------------------------
# escrow lifecycle
# --------------------------------------------------------------------------

class TestEscrowLifecycle:
    def test_happy_path_pays_the_provider(self, ledger, clock, commit):
        ledger.open_escrow("j1", "requester", "provider", PRICE)
        assert ledger.escrows["j1"].state is EscrowState.OPEN
        commit(ledger, "j1")
        assert ledger.escrows["j1"].state is EscrowState.AWAITING_VERIFICATION

        clock.advance_s(61)
        rec = ledger.release("j1")
        assert rec.state is EscrowState.SETTLED
        assert rec.provider_payout == PRICE
        assert rec.requester_refund == 0.0
        assert rec.slashed is False
        assert ledger.withdraw("provider") == PRICE
        ledger.check_invariants()

    def test_provider_must_be_actively_staked(self, ledger):
        with pytest.raises(ProviderNotActive):
            ledger.open_escrow("j", "requester", "unstaked", PRICE)

    def test_duplicate_escrow_is_rejected(self, ledger):
        ledger.open_escrow("j1", "requester", "provider", PRICE)
        with pytest.raises(EscrowExists):
            ledger.open_escrow("j1", "requester", "provider", PRICE)

    def test_only_the_awarded_provider_can_commit(self, ledger):
        ledger.open_escrow("j1", "requester", "provider", PRICE)
        with pytest.raises(NotAwardedProvider):
            ledger.record_commitment("j1", "someone-else", WRONG, "blob")

    def test_double_commitment_is_rejected(self, ledger, commit):
        ledger.open_escrow("j1", "requester", "provider", PRICE)
        commit(ledger, "j1")
        with pytest.raises(CommitmentExists):
            commit(ledger, "j1")

    def test_unknown_job_raises(self, ledger):
        with pytest.raises(NoEscrow):
            ledger.release("never-existed")

    def test_a_commitment_must_carry_real_digests(self, ledger):
        """The contracts take bytes32. A commitment the simulation accepts but
        the ABI would reject is a scenario that cannot be replayed on chain."""
        ledger.open_escrow("j1", "requester", "provider", PRICE)
        with pytest.raises(ValueError, match="32-byte hex"):
            ledger.record_commitment("j1", "provider", "not-a-digest", "blob")
        with pytest.raises(ValueError, match="not hex"):
            ledger.record_commitment("j1", "provider", "z" * 64, "blob")
        with pytest.raises(ValueError, match="merkle_root"):
            ledger.record_commitment("j1", "provider", WRONG, "blob", merkle_root="abc")
        assert "j1" not in ledger.commitments


class TestChallengeWindow:
    def test_release_before_the_deadline_raises(self, ledger, clock, commit):
        ledger.open_escrow("j1", "requester", "provider", PRICE)
        c = commit(ledger, "j1").entry
        with pytest.raises(ChallengeWindowOpen):
            ledger.release("j1")
        clock.advance_s(59)
        with pytest.raises(ChallengeWindowOpen):
            ledger.release("j1")
        clock.t = c.challenge_deadline_ms
        assert ledger.release("j1").state is EscrowState.SETTLED

    def test_challenge_after_the_window_raises(self, ledger, clock, commit):
        ledger.open_escrow("j1", "requester", "provider", PRICE)
        c = commit(ledger, "j1", claimed=WRONG)
        clock.advance_s(61)
        with pytest.raises(ChallengeWindowClosed):
            ledger.prove_data_mismatch("j1", c.data, "watcher", c.proof)

    def test_double_settlement_is_rejected(self, ledger, clock, commit):
        ledger.open_escrow("j1", "requester", "provider", PRICE)
        commit(ledger, "j1")
        clock.advance_s(61)
        ledger.release("j1")
        with pytest.raises(WrongState):
            ledger.release("j1")
        assert len(ledger.records) == 1
        ledger.check_invariants()

    def test_cancel_needs_the_requester_and_the_timeout(self, ledger, clock):
        ledger.open_escrow("j1", "requester", "provider", PRICE)
        with pytest.raises(AwardWindowOpen):
            ledger.cancel("j1", "requester")
        clock.advance_s(31)
        with pytest.raises(NotRequester):
            ledger.cancel("j1", "impostor")
        rec = ledger.cancel("j1", "requester")
        assert rec.state is EscrowState.REFUNDED
        assert rec.requester_refund == PRICE
        ledger.check_invariants()


# --------------------------------------------------------------------------
# fraud and slashing
# --------------------------------------------------------------------------

class TestFraud:
    def test_mismatch_slashes_and_splits_80_20(self, ledger, commit):
        ledger.open_escrow("j1", "requester", "provider", PRICE)
        c = commit(ledger, "j1", claimed=WRONG)
        rec = ledger.prove_data_mismatch("j1", c.data, "watcher", c.proof)

        assert rec.state is EscrowState.SLASHED
        assert rec.slashed is True
        assert rec.slash_amount == PRICE
        assert rec.validator_reward == pytest.approx(PRICE * 0.8)
        assert rec.treasury_amount == pytest.approx(PRICE * 0.2)
        assert rec.requester_refund == PRICE
        assert rec.provider_payout == 0.0
        assert rec.remaining_stake == pytest.approx(STAKE - PRICE)
        assert ledger.withdraw("watcher") == pytest.approx(PRICE * 0.8)
        assert ledger.withdraw("treasury") == pytest.approx(PRICE * 0.2)
        ledger.check_invariants()

    def test_a_truthful_reveal_cannot_slash_an_honest_provider(self, ledger, commit):
        ledger.open_escrow("j1", "requester", "provider", PRICE)
        c = commit(ledger, "j1")                     # committed hash == the stored blob
        with pytest.raises(NoMismatch, match="no fraud to prove"):
            ledger.prove_data_mismatch("j1", c.data, "watcher", c.proof)
        assert ledger.stakes["provider"] == STAKE
        ledger.check_invariants()

    def test_a_blob_outside_the_committed_root_is_rejected(self, ledger, commit):
        ledger.open_escrow("j1", "requester", "provider", PRICE)
        c = commit(ledger, "j1", stored=b"the real output", claimed=WRONG)

        with pytest.raises(BadInclusionProof, match="not included"):
            ledger.prove_data_mismatch("j1", b"never in the block", "watcher", c.proof)

        rec = ledger.prove_data_mismatch("j1", c.data, "watcher", c.proof)
        assert rec.slashed is True
        ledger.check_invariants()

    def test_a_neighbouring_blob_cannot_be_replayed_at_the_committed_index(self, ledger, commit):
        """The sibling direction comes from the committed leaf index, exactly as
        in `VerificationContract._computeRoot`, so a challenger cannot swap in a
        different blob from the same DA block."""
        ledger.open_escrow("j1", "requester", "provider", PRICE)
        c = commit(ledger, "j1", stored=b"the real output", claimed=WRONG)
        neighbour = c.layer.get_blob(c.layer._blocks[c.layer.height].blob_ids[0])
        with pytest.raises(BadInclusionProof):
            ledger.prove_data_mismatch("j1", neighbour, "watcher", c.proof)
        assert ledger.stakes["provider"] == STAKE

    def test_an_unproven_mismatch_cannot_slash(self, ledger, commit):
        """No Merkle proof, no slash. This is the hole the chain never had: the
        EVM stores bytes32(0) for a rootless commitment and no sibling path
        folds to zero, so `proveDataMismatch` always reverts there. Skipping the
        check here let anyone burn a provider's collateral with arbitrary bytes.
        """
        ledger.open_escrow("j1", "requester", "provider", PRICE)
        c = commit(ledger, "j1", claimed=WRONG)
        with pytest.raises(MissingInclusionProof, match="proof is required"):
            ledger.prove_data_mismatch("j1", b"arbitrary bytes from nowhere", "attacker")
        assert ledger.stakes["provider"] == STAKE
        assert ledger.escrows["j1"].state is EscrowState.AWAITING_VERIFICATION
        # the honest challenge still works afterwards
        assert ledger.prove_data_mismatch("j1", c.data, "watcher", c.proof).slashed is True
        ledger.check_invariants()

    def test_a_commitment_with_no_da_root_cannot_be_challenged(self, ledger):
        ledger.open_escrow("j1", "requester", "provider", PRICE)
        ledger.record_commitment("j1", "provider", WRONG, blob_ref="blob-1")
        with pytest.raises(MissingInclusionProof, match="no DA Merkle root"):
            ledger.prove_data_mismatch("j1", b"anything at all", "attacker", proof=[])
        assert ledger.stakes["provider"] == STAKE

    def test_slash_is_capped_at_the_remaining_stake(self, clock, commit):
        L = Ledger(min_stake=1.0, challenge_window_s=60, clock=clock)
        L.stake("provider", 1.0)
        L.open_escrow("j1", "requester", "provider", 3.0)
        c = commit(L, "j1", claimed=WRONG)
        rec = L.prove_data_mismatch("j1", c.data, "watcher", c.proof)

        assert rec.slash_amount == 1.0
        assert rec.fully_covered is False
        assert rec.requester_refund == 3.0        # the requester is still made whole
        assert L.stakes["provider"] == 0.0
        assert rec.validator_reward + rec.treasury_amount == pytest.approx(1.0)
        L.check_invariants()

    def test_slash_reaches_unbonding_collateral(self, clock, commit):
        L = Ledger(min_stake=1.0, challenge_window_s=60, unbonding_period_s=60, clock=clock)
        L.stake("provider", 4.0)
        L.request_unstake("provider", 3.0)         # trying to escape a pending challenge
        L.open_escrow("j1", "requester", "provider", 2.0)
        c = commit(L, "j1", claimed=WRONG)
        rec = L.prove_data_mismatch("j1", c.data, "watcher", c.proof)

        assert rec.slash_amount == 2.0
        assert L.stakes["provider"] == 0.0
        assert L.slashable_of("provider") == 2.0   # 4 staked - 2 slashed
        L.check_invariants()

    def test_double_resolution_is_rejected(self, ledger, commit):
        ledger.open_escrow("j1", "requester", "provider", PRICE)
        c = commit(ledger, "j1", claimed=WRONG)
        ledger.prove_data_mismatch("j1", c.data, "watcher", c.proof)
        with pytest.raises(AlreadyResolved):
            ledger.prove_data_mismatch("j1", c.data, "watcher", c.proof)

    def test_split_arithmetic_has_no_dust(self, clock, commit):
        """A slash that does not divide by five must still conserve exactly."""
        L = Ledger(min_stake=1.0, challenge_window_s=60, clock=clock)
        L.stake("provider", 100.0)
        amount = 0.001234567890123457          # deliberately awkward
        L.open_escrow("j1", "requester", "provider", amount)
        c = commit(L, "j1", claimed=WRONG)
        rec = L.prove_data_mismatch("j1", c.data, "watcher", c.proof)

        slashed_wei = to_wei(rec.slash_amount)
        reward_wei = to_wei(rec.validator_reward)
        treasury_wei = to_wei(rec.treasury_amount)
        assert reward_wei == (slashed_wei * 8000) // 10000
        assert reward_wei + treasury_wei == slashed_wei      # exact, in wei
        L.check_invariants()


# --------------------------------------------------------------------------
# validator verdicts
# --------------------------------------------------------------------------

class TestVerdicts:
    def test_only_an_allow_listed_staked_validator_can_rule(self, ledger, commit):
        ledger.open_escrow("j1", "requester", "provider", PRICE)
        commit(ledger, "j1")
        with pytest.raises(NotValidator):
            ledger.submit_verdict("j1", "random-peer", VerdictKind.FAIL)

        ledger.register_validator("broke-validator")
        with pytest.raises(ProviderNotActive):
            ledger.submit_verdict("j1", "broke-validator", VerdictKind.FAIL)
        assert ledger.stakes["provider"] == STAKE

    def test_fail_slashes_pass_and_error_do_not(self, ledger, clock, commit):
        for job, verdict in (("j-pass", VerdictKind.PASS), ("j-error", VerdictKind.ERROR)):
            ledger.open_escrow(job, "requester", "provider", PRICE)
            commit(ledger, job)
            assert ledger.submit_verdict(job, "validator", verdict, 4, "looks fine") is None
            assert ledger.verdicts[job] is verdict
        assert ledger.stakes["provider"] == STAKE

        ledger.open_escrow("j-fail", "requester", "provider", PRICE)
        commit(ledger, "j-fail")
        rec = ledger.submit_verdict("j-fail", "validator", VerdictKind.FAIL, 1, "fabricated")
        assert rec.state is EscrowState.SLASHED
        assert rec.validator_reward == pytest.approx(PRICE * 0.8)
        ledger.check_invariants()

        row = [r for r in ledger.rows() if r["job_id"] == "j-fail"][0]
        assert row["resolution"] == "validator_verdict"     # an oracle, not a proof
        assert row["reporter"] == "validator"
        assert row["verdict"] == "fail" and row["quality_score"] == 1

    def test_a_judge_error_still_lets_the_escrow_settle(self, ledger, clock, commit):
        ledger.open_escrow("j1", "requester", "provider", PRICE)
        commit(ledger, "j1")
        ledger.submit_verdict("j1", "validator", VerdictKind.ERROR)
        clock.advance_s(61)
        assert ledger.release("j1").state is EscrowState.SETTLED
        ledger.check_invariants()


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

class TestReporting:
    def test_rows_are_tagged_with_the_backend(self, ledger, clock, commit):
        ledger.open_escrow("j1", "requester", "provider", PRICE)
        commit(ledger, "j1")
        clock.advance_s(61)
        ledger.release("j1")
        rows = ledger.rows()
        assert len(rows) == 1
        assert rows[0]["backend"] == "sim"
        assert rows[0]["tx_hash"] == ""          # no chain, and it does not pretend otherwise
        assert rows[0]["gas_used"] == 0
        assert rows[0]["resolution"] == "challenge_window_elapsed"
        assert rows[0]["provider_payout_wei"] == to_wei(PRICE)

    def test_rows_say_which_path_confirmed_the_fraud(self, ledger, commit):
        ledger.open_escrow("j1", "requester", "provider", PRICE)
        c = commit(ledger, "j1", claimed=WRONG)
        ledger.prove_data_mismatch("j1", c.data, "watcher", c.proof)
        row = ledger.rows()[0]
        assert row["resolution"] == "data_mismatch_proof"
        assert row["reporter"] == "watcher"
        assert row["verdict"] == ""              # no judge was involved, and it says so

    def test_invariant_violation_is_detectable(self, ledger, clock, commit):
        ledger.open_escrow("j1", "requester", "provider", PRICE)
        commit(ledger, "j1")
        clock.advance_s(61)
        ledger.release("j1")
        ledger.check_invariants()
        ledger.stake_wei["provider"] += to_wei(1.0)      # conjure value out of nothing
        with pytest.raises(InvariantViolation):
            ledger.check_invariants()


# --------------------------------------------------------------------------
# property test: value conservation over randomised job sequences
# --------------------------------------------------------------------------

OPS = ("stake", "unstake", "claim", "withdraw", "open", "commit", "release",
       "fraud", "verdict", "cancel", "tick")


@pytest.mark.parametrize("seed", range(12))
def test_value_is_conserved_over_random_sequences(seed, tmp_path):
    """Drive the ledger with 300 random operations and assert conservation after
    every one of them, whether it was accepted or rejected.

    Commitments are bound to one real DA block so the `fraud` operation submits
    a genuine Merkle proof; the run must exercise the same code path a
    challenger on chain would."""
    rng = random.Random(seed)
    clock = ManualClock(0)
    L = Ledger(min_stake=1.0, challenge_window_s=10, award_timeout_s=5,
               unbonding_period_s=10, clock=clock)

    nodes = [f"node-{i}" for i in range(4)]
    for n in nodes:
        L.stake(n, rng.choice([1.0, 2.5, 10.0]))
    L.stake("validator", 5.0)
    L.register_validator("validator")

    # One DA block, three blobs. Every commitment claims the hash of blob 0
    # while pointing at blob 1, so a proof of blob 1 is always a real mismatch.
    layer = da.DALayer(root_dir=tmp_path / "da")
    for payload in (b"blob zero", b"blob one - what was really stored", b"blob two"):
        layer.submit_blob(payload, seal=False)
    layer.seal_block()
    block = layer._blocks[layer.height]
    stored_meta = layer.get_blob_meta(block.blob_ids[1])
    stored_proof, stored_root = layer.inclusion_proof(stored_meta.blob_id)
    claimed_hash = layer.get_blob_meta(block.blob_ids[0]).commitment
    assert claimed_hash != stored_meta.commitment

    jobs: list[str] = []
    accepted = {op: 0 for op in OPS}

    for step in range(300):
        op = rng.choice(OPS)
        job = rng.choice(jobs) if jobs else None
        try:
            if op == "stake":
                L.stake(rng.choice(nodes), round(rng.uniform(0.001, 5.0), 6))
            elif op == "unstake":
                L.request_unstake(rng.choice(nodes), round(rng.uniform(0.001, 5.0), 6))
            elif op == "claim":
                L.claim_unstake(rng.choice(nodes))
            elif op == "withdraw":
                L.withdraw(rng.choice(nodes + ["treasury", "requester", "watcher"]))
            elif op == "open":
                job = f"job-{seed}-{step}"
                L.open_escrow(job, "requester", rng.choice(nodes),
                              round(rng.uniform(0.0001, 3.0), 6))
                jobs.append(job)
            elif op == "commit" and job:
                L.record_commitment(job, L.escrows[job].provider, claimed_hash,
                                    stored_meta.blob_id, merkle_root=stored_root,
                                    leaf_index=stored_meta.index)
            elif op == "release" and job:
                L.release(job)
            elif op == "fraud" and job:
                L.prove_data_mismatch(job, stored_meta.data, "watcher", stored_proof)
            elif op == "verdict" and job:
                L.submit_verdict(job, "validator", rng.choice(list(VerdictKind)),
                                 rng.randint(1, 5), "randomised judge reason")
            elif op == "cancel" and job:
                L.cancel(job, "requester")
            elif op == "tick":
                clock.advance_s(rng.choice([1, 4, 11, 30]))
            else:
                continue
            accepted[op] += 1
        except LedgerError:
            pass                     # a rejected operation must still conserve value
        L.check_invariants()

    acct = L.check_invariants()
    # the run has to have actually done something, or it proves nothing
    assert accepted["open"] > 0 and accepted["commit"] > 0
    assert accepted["release"] + accepted["fraud"] + accepted["verdict"] + accepted["cancel"] > 0
    assert acct["n_settlements"] > 0
    # every settlement says how it was reached; nothing is left to be inferred
    assert all(r["resolution"] in ("challenge_window_elapsed", "award_timeout_cancel",
                                   "data_mismatch_proof", "validator_verdict")
               for r in L.rows())

    # Value in == value out, restated independently of check_invariants and on
    # the exact integers: summing the float view first would only be testing
    # float64's rounding.
    w = acct["wei"]
    assert w["deposited"] == (w["stake_active"] + w["stake_unbonding"] + w["escrow_held"]
                              + w["withdrawable"] + w["withdrawn"])
    assert w["slashed"] == w["validator_reward"] + w["treasury"]
    assert w["provider_payout"] + w["requester_refund"] == sum(
        r["amount_wei"] for r in L.record_wei)
