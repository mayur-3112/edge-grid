"""Off-chain settlement ledger: the exact state machine `contracts/*.sol` runs.

Phase-1 Module 5 / Objective 4. This replaces `contracts/simulate.py`, which had
no escrow states, no challenge window, no 80/20 slash split, and - the defect
that mattered - did not conserve value: a passing provider's stake still read
10.0 after four settled jobs because nobody was ever actually paid.

Two design decisions carry most of the weight here:

  * **Integer accounting.** Everything is stored in wei (1 GRID == 10**18 wei),
    the same unit the contracts use. Floats appear only at the API boundary. A
    conservation check on floats can only ever be approximate; on integers it is
    exact, so `check_invariants()` asserts equality rather than closeness.
  * **An injectable clock.** The challenge window is real - `release()` before
    the deadline raises. Tests and the legacy shim advance a `ManualClock`
    instead of sleeping, and nothing anywhere is allowed to skip the window.

The state machine, mirroring `Marketplace.sol` exactly:

    OPEN --record_commitment--> AWAITING_VERIFICATION
    AWAITING_VERIFICATION --release after deadline--> SETTLED
    AWAITING_VERIFICATION --confirm_fraud--------->    SLASHED
    OPEN --cancel after award timeout------------->    REFUNDED

`edgegrid.chain.ChainBackend` implements the same surface against the deployed
contracts. `get_backend()` in that module is the only place that picks between
them, and it never picks silently.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Callable, Optional

from edgegrid import config as C
from edgegrid.da import verify_proof as da_verify_proof
from edgegrid.schemas import EscrowState, SettlementRecord, VerdictKind, now_ms

WEI_PER_GRID = 10 ** 18


# --------------------------------------------------------------------------
# units
# --------------------------------------------------------------------------

def to_wei(amount: float | int) -> int:
    """GRID -> wei. Rounds to the nearest wei; a negative amount is a bug, not
    a withdrawal, so it raises."""
    if isinstance(amount, int) and not isinstance(amount, bool):
        wei = amount * WEI_PER_GRID
    else:
        if not math.isfinite(amount):
            raise ValueError(f"amount must be finite, got {amount!r}")
        wei = int(round(amount * WEI_PER_GRID))
    if wei < 0:
        raise ValueError(f"amount must be non-negative, got {amount!r}")
    return wei


def from_wei(wei: int) -> float:
    return wei / WEI_PER_GRID


def _require_digest(name: str, value: str) -> str:
    """A bytes32 hex digest, the shape the contracts take. Rejected here rather
    than at the ABI boundary so simulation and chain accept the same inputs."""
    raw = value.removeprefix("0x")
    if len(raw) != 64:
        raise ValueError(f"{name} must be a 32-byte hex digest, got {len(raw)} hex chars: {value!r}")
    try:
        bytes.fromhex(raw)
    except ValueError as exc:
        raise ValueError(f"{name} is not hex: {value!r}") from exc
    return raw


# --------------------------------------------------------------------------
# errors - one per contract revert, so a caller can distinguish them
# --------------------------------------------------------------------------

class LedgerError(Exception):
    """Base for every rejected ledger operation."""


class BelowMinimumStake(LedgerError): ...
class InsufficientStake(LedgerError): ...
class UnbondingNotReady(LedgerError): ...
class NothingToWithdraw(LedgerError): ...
class EscrowExists(LedgerError): ...
class NoEscrow(LedgerError): ...
class WrongState(LedgerError): ...
class ChallengeWindowOpen(LedgerError): ...
class ChallengeWindowClosed(LedgerError): ...
class AwardWindowOpen(LedgerError): ...
class ProviderNotActive(LedgerError): ...
class NotRequester(LedgerError): ...
class NotAwardedProvider(LedgerError): ...
class CommitmentExists(LedgerError): ...
class NoCommitment(LedgerError): ...
class AlreadyResolved(LedgerError): ...
class NotValidator(LedgerError): ...
class MissingInclusionProof(LedgerError): ...
class BadInclusionProof(LedgerError): ...
class NoMismatch(LedgerError): ...


class InvariantViolation(AssertionError):
    """Value was created or destroyed. Never catch this - it means the ledger
    is wrong, not that the caller is."""


# --------------------------------------------------------------------------
# clock
# --------------------------------------------------------------------------

class ManualClock:
    """A clock a test can move. Milliseconds, matching `schemas.now_ms`."""

    def __init__(self, start_ms: Optional[int] = None):
        self.t = int(start_ms if start_ms is not None else now_ms())

    def __call__(self) -> int:
        return self.t

    def advance_s(self, seconds: float) -> int:
        self.t += int(seconds * 1000)
        return self.t


# --------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------

@dataclass
class Escrow:
    job_id: str
    requester: str
    provider: str
    amount_wei: int
    opened_ms: int
    state: EscrowState = EscrowState.OPEN
    challenge_deadline_ms: int = 0


@dataclass
class CommitmentEntry:
    job_id: str
    provider: str
    output_hash: str
    blob_ref: str
    merkle_root: str = ""
    leaf_index: int = 0
    recorded_ms: int = 0
    challenge_deadline_ms: int = 0
    resolved: bool = False
    resolution: str = ""          # "" | "data_mismatch_proof" | "validator_verdict"


@dataclass
class Unbonding:
    amount_wei: int = 0
    ready_ms: int = 0


@dataclass
class Accounting:
    """Every wei the ledger has ever seen, by where it came from and went."""

    deposited_wei: int = 0        # stake() + open_escrow() - the only inflows
    withdrawn_wei: int = 0
    escrow_held_wei: int = 0
    provider_payout_wei: int = 0
    requester_refund_wei: int = 0
    validator_reward_wei: int = 0
    treasury_wei: int = 0
    slashed_wei: int = 0
    stake_active_wei: int = 0
    stake_unbonding_wei: int = 0
    withdrawable_wei: int = 0

    def as_grid(self) -> dict:
        return {k: from_wei(v) for k, v in self.__dict__.items()}


# --------------------------------------------------------------------------
# ledger
# --------------------------------------------------------------------------

class Ledger:
    """The simulation backend. Same surface as `chain.ChainBackend`."""

    backend = "sim"

    def __init__(
        self,
        min_stake: float = C.MIN_STAKE,
        challenge_window_s: float = C.CHALLENGE_WINDOW_S,
        award_timeout_s: float = 600.0,
        unbonding_period_s: float = C.CHALLENGE_WINDOW_S,
        validator_share: float = C.VALIDATOR_SLASH_SHARE,
        treasury: str = "treasury",
        clock: Optional[Callable[[], int]] = None,
    ):
        if not 0.0 <= validator_share <= 1.0:
            raise ValueError(f"validator_share must be in [0, 1], got {validator_share}")
        self.min_stake_wei = to_wei(min_stake)
        self.challenge_window_ms = int(challenge_window_s * 1000)
        self.award_timeout_ms = int(award_timeout_s * 1000)
        self.unbonding_period_ms = int(unbonding_period_s * 1000)
        # Basis points, exactly as the contract holds it, so the split is the
        # same integer arithmetic on both sides.
        self.validator_bps = int(round(validator_share * 10_000))
        self.treasury = treasury
        self._clock = clock or now_ms

        self.stake_wei: dict[str, int] = {}
        self.unbonding: dict[str, Unbonding] = {}
        self.withdrawable_wei: dict[str, int] = {}
        self.escrows: dict[str, Escrow] = {}
        self.commitments: dict[str, CommitmentEntry] = {}
        self.verdicts: dict[str, VerdictKind] = {}
        self.verdict_detail: dict[str, dict] = {}
        self.validators: set[str] = set()
        self.records: list[SettlementRecord] = []
        # Why each record ended the way it did. `SettlementRecord` has no field
        # for it (extra="forbid"), and inferring "it must have been a proof"
        # from a non-zero slash after the fact is exactly the kind of guess this
        # module refuses to make, so it is carried alongside and joined in rows().
        self.record_meta: list[dict] = []
        # Exact wei behind each record. `SettlementRecord` carries floats, and a
        # float64 cannot hold 18 significant digits, so the record is a lossy
        # *view* of the settlement. These integers are the authority, and they
        # are what `check_invariants` reconciles.
        self.record_wei: list[dict[str, int]] = []
        self.acct = Accounting()

    # -- clock -----------------------------------------------------------

    def now(self) -> int:
        return int(self._clock())

    # -- staking ---------------------------------------------------------

    def stake(self, node: str, amount: float) -> float:
        """Post collateral. Mirrors `NodeRegistry.stake`."""
        wei = to_wei(amount)
        if wei == 0:
            raise LedgerError("stake amount must be > 0")
        new = self.stake_wei.get(node, 0) + wei
        if new < self.min_stake_wei:
            raise BelowMinimumStake(
                f"{node}: stake would be {from_wei(new)} GRID, minimum is {from_wei(self.min_stake_wei)}")
        self.stake_wei[node] = new
        self.acct.deposited_wei += wei
        self.acct.stake_active_wei += wei
        return from_wei(new)

    def request_unstake(self, node: str, amount: float) -> int:
        """Start unbonding. The collateral stays slashable until claimed, which
        is what stops a provider front-running a fraud proof."""
        wei = to_wei(amount)
        current = self.stake_wei.get(node, 0)
        if wei == 0:
            raise LedgerError("unstake amount must be > 0")
        if wei > current:
            raise InsufficientStake(f"{node}: has {from_wei(current)}, requested {amount}")
        remaining = current - wei
        if remaining != 0 and remaining < self.min_stake_wei:
            raise BelowMinimumStake(
                f"{node}: {from_wei(remaining)} left would be under the {from_wei(self.min_stake_wei)} minimum")
        self.stake_wei[node] = remaining
        self.acct.stake_active_wei -= wei
        u = self.unbonding.setdefault(node, Unbonding())
        u.amount_wei += wei
        u.ready_ms = self.now() + self.unbonding_period_ms
        self.acct.stake_unbonding_wei += wei
        return u.ready_ms

    def claim_unstake(self, node: str) -> float:
        u = self.unbonding.get(node)
        if u is None or u.amount_wei == 0:
            raise NothingToWithdraw(f"{node} has nothing unbonding")
        if self.now() < u.ready_ms:
            raise UnbondingNotReady(f"{node}: unbonding until {u.ready_ms}, now {self.now()}")
        amount = u.amount_wei
        u.amount_wei = 0
        u.ready_ms = 0
        self.acct.stake_unbonding_wei -= amount
        self._credit(node, amount)
        return from_wei(amount)

    def withdraw(self, account: str) -> float:
        """Pull a credited balance out of the ledger entirely."""
        amount = self.withdrawable_wei.get(account, 0)
        if amount == 0:
            raise NothingToWithdraw(f"{account} has nothing to withdraw")
        self.withdrawable_wei[account] = 0
        self.acct.withdrawable_wei -= amount
        self.acct.withdrawn_wei += amount
        return from_wei(amount)

    def is_active(self, node: str) -> bool:
        s = self.stake_wei.get(node, 0)
        return s > 0 and s >= self.min_stake_wei

    def slashable_of(self, node: str) -> float:
        u = self.unbonding.get(node)
        return from_wei(self.stake_wei.get(node, 0) + (u.amount_wei if u else 0))

    # -- escrow ----------------------------------------------------------

    def open_escrow(self, job_id: str, requester: str, provider: str, amount: float) -> Escrow:
        """Lock the clearing price against an active provider."""
        wei = to_wei(amount)
        if wei == 0:
            raise LedgerError("escrow amount must be > 0")
        if job_id in self.escrows:
            raise EscrowExists(f"escrow already open for job {job_id}")
        if not self.is_active(provider):
            raise ProviderNotActive(
                f"{provider} holds {self.slashable_of(provider)} GRID, "
                f"minimum active stake is {from_wei(self.min_stake_wei)}")
        e = Escrow(job_id=job_id, requester=requester, provider=provider,
                   amount_wei=wei, opened_ms=self.now())
        self.escrows[job_id] = e
        self.acct.deposited_wei += wei
        self.acct.escrow_held_wei += wei
        return e

    def record_commitment(self, job_id: str, provider: str, output_hash: str, blob_ref: str,
                          merkle_root: str = "", leaf_index: int = 0) -> CommitmentEntry:
        """Bind the provider to one output and start the challenge window.

        `output_hash` and `merkle_root` must be 32-byte hex digests, because
        that is what `VerificationContract.recordCommitment` takes. Accepting a
        placeholder string here and rejecting it on chain would mean a scenario
        that runs in simulation and cannot be replayed against the contracts."""
        _require_digest("output_hash", output_hash)
        if merkle_root:
            _require_digest("merkle_root", merkle_root)
        if leaf_index < 0:
            raise LedgerError(f"leaf_index must be non-negative, got {leaf_index}")
        e = self._escrow(job_id)
        if job_id in self.commitments:
            raise CommitmentExists(f"job {job_id} already has a commitment")
        if e.state is not EscrowState.OPEN:
            raise WrongState(f"job {job_id} is {e.state.value}, expected open")
        if provider != e.provider:
            raise NotAwardedProvider(f"{provider} was not awarded job {job_id} ({e.provider} was)")
        now = self.now()
        deadline = now + self.challenge_window_ms
        c = CommitmentEntry(job_id=job_id, provider=provider, output_hash=output_hash,
                            blob_ref=blob_ref, merkle_root=merkle_root, leaf_index=leaf_index,
                            recorded_ms=now, challenge_deadline_ms=deadline)
        self.commitments[job_id] = c
        e.state = EscrowState.AWAITING_VERIFICATION
        e.challenge_deadline_ms = deadline
        return c

    def release(self, job_id: str) -> SettlementRecord:
        """Pay the provider once the challenge window has closed unchallenged."""
        e = self._escrow(job_id)
        if e.state is not EscrowState.AWAITING_VERIFICATION:
            raise WrongState(f"job {job_id} is {e.state.value}, expected awaiting_verification")
        now = self.now()
        if now < e.challenge_deadline_ms:
            raise ChallengeWindowOpen(
                f"job {job_id}: challenge window closes at {e.challenge_deadline_ms}, now {now}")
        e.state = EscrowState.SETTLED
        self.acct.escrow_held_wei -= e.amount_wei
        self.acct.provider_payout_wei += e.amount_wei
        self._credit(e.provider, e.amount_wei)
        return self._record(e, slashed=0, validator_reward=0, treasury_amount=0,
                            fully_covered=True, provider_payout=e.amount_wei, requester_refund=0,
                            resolution="challenge_window_elapsed", reporter="")

    def cancel(self, job_id: str, requester: str) -> SettlementRecord:
        """Reclaim an escrow the provider never committed against."""
        e = self._escrow(job_id)
        if e.state is not EscrowState.OPEN:
            raise WrongState(f"job {job_id} is {e.state.value}, expected open")
        if requester != e.requester:
            raise NotRequester(f"{requester} did not open job {job_id} ({e.requester} did)")
        deadline = e.opened_ms + self.award_timeout_ms
        if self.now() < deadline:
            raise AwardWindowOpen(f"job {job_id}: award window closes at {deadline}, now {self.now()}")
        e.state = EscrowState.REFUNDED
        self.acct.escrow_held_wei -= e.amount_wei
        self.acct.requester_refund_wei += e.amount_wei
        self._credit(e.requester, e.amount_wei)
        return self._record(e, slashed=0, validator_reward=0, treasury_amount=0,
                            fully_covered=True, provider_payout=0, requester_refund=e.amount_wei,
                            resolution="award_timeout_cancel", reporter=requester)

    # -- verification ----------------------------------------------------

    def register_validator(self, validator: str) -> None:
        self.validators.add(validator)

    def submit_verdict(self, job_id: str, validator: str, verdict: VerdictKind,
                       quality_score: int = 0, reason: str = "",
                       ) -> Optional[SettlementRecord]:
        """Record a judge ruling. FAIL confirms fraud; PASS and ERROR leave the
        escrow to settle normally when the window closes - a judge outage is
        never a slash.

        `quality_score` and `reason` exist so this signature is the one
        `chain.ChainBackend.submit_verdict` also accepts; the chain stores
        keccak256(reason) and this keeps the plaintext next to the verdict so a
        row from either backend can be read the same way."""
        if validator not in self.validators:
            raise NotValidator(f"{validator} is not an allow-listed validator")
        if not self.is_active(validator):
            raise ProviderNotActive(f"validator {validator} holds no active stake")
        c = self._challengeable(job_id)
        self.verdicts[job_id] = verdict
        self.verdict_detail[job_id] = {"validator": validator, "verdict": verdict.value,
                                       "quality_score": int(quality_score), "reason": reason}
        if verdict is VerdictKind.FAIL:
            return self._confirm_fraud(job_id, c, validator, "validator_verdict")
        return None

    def prove_data_mismatch(self, job_id: str, blob_data: bytes, reporter: str,
                            proof: Optional[list[tuple[str, str]]] = None) -> SettlementRecord:
        """Slash on a *proven* mismatch between the DA blob and the committed
        output hash.

        Permissionless, and the same two checks the EVM runs in
        `VerificationContract.proveDataMismatch`, in the same order:

          1. the revealed blob really is the one sitting at the committed leaf
             index under the committed Merkle root, and
          2. its sha256 is not the hash the provider committed to.

        Both checks are mandatory. Earlier this method skipped step 1 whenever
        the caller passed no proof or the commitment carried no root, which let
        anyone slash a provider by handing in arbitrary bytes - the EVM path
        cannot be fooled that way (a commitment with no root stores bytes32(0),
        and no sibling path folds to zero), so the omission also broke the
        sim/chain parity this module exists to provide. There is deliberately no
        unproven path: a missing root or a missing proof raises.
        """
        c = self._challengeable(job_id)
        if not c.merkle_root:
            raise MissingInclusionProof(
                f"job {job_id}: the commitment carries no DA Merkle root, so a mismatch "
                f"cannot be proven against it (the chain would revert with BadInclusionProof)")
        if proof is None:
            raise MissingInclusionProof(
                f"job {job_id}: a Merkle inclusion proof is required to slash under root "
                f"{c.merkle_root}; pass edgegrid.da.DALayer.inclusion_proof()'s path")
        if not da_verify_proof(blob_data, list(proof), c.merkle_root):
            raise BadInclusionProof(
                f"job {job_id}: blob is not included under committed root {c.merkle_root}")
        revealed = hashlib.sha256(blob_data).hexdigest()
        if revealed == c.output_hash:
            raise NoMismatch(
                f"job {job_id}: revealed blob matches the commitment - no fraud to prove")
        return self._confirm_fraud(job_id, c, reporter, "data_mismatch_proof")

    def _confirm_fraud(self, job_id: str, c: CommitmentEntry, reporter: str,
                       kind: str) -> SettlementRecord:
        e = self._escrow(job_id)
        if e.state is not EscrowState.AWAITING_VERIFICATION:
            raise WrongState(f"job {job_id} is {e.state.value}, expected awaiting_verification")
        c.resolved = True
        c.resolution = kind

        # Slash the escrowed amount, capped at whatever collateral is left -
        # active stake first, then unbonding.
        target = e.amount_wei
        active = self.stake_wei.get(c.provider, 0)
        u = self.unbonding.setdefault(c.provider, Unbonding())
        available = active + u.amount_wei
        slashed = min(target, available)
        fully_covered = slashed == target

        from_active = min(slashed, active)
        if from_active:
            self.stake_wei[c.provider] = active - from_active
            self.acct.stake_active_wei -= from_active
        from_unbonding = slashed - from_active
        if from_unbonding:
            u.amount_wei -= from_unbonding
            self.acct.stake_unbonding_wei -= from_unbonding

        validator_reward = (slashed * self.validator_bps) // 10_000
        treasury_amount = slashed - validator_reward     # remainder, so no dust is lost
        if slashed:
            self._credit(reporter, validator_reward)
            self._credit(self.treasury, treasury_amount)
            self.acct.validator_reward_wei += validator_reward
            self.acct.treasury_wei += treasury_amount
            self.acct.slashed_wei += slashed

        e.state = EscrowState.SLASHED
        self.acct.escrow_held_wei -= e.amount_wei
        self.acct.requester_refund_wei += e.amount_wei
        self._credit(e.requester, e.amount_wei)

        return self._record(e, slashed=slashed, validator_reward=validator_reward,
                            treasury_amount=treasury_amount, fully_covered=fully_covered,
                            provider_payout=0, requester_refund=e.amount_wei,
                            resolution=kind, reporter=reporter)

    # -- internals -------------------------------------------------------

    def _escrow(self, job_id: str) -> Escrow:
        e = self.escrows.get(job_id)
        if e is None:
            raise NoEscrow(f"no escrow for job {job_id}")
        return e

    def _challengeable(self, job_id: str) -> CommitmentEntry:
        c = self.commitments.get(job_id)
        if c is None:
            raise NoCommitment(f"no commitment for job {job_id}")
        if c.resolved:
            raise AlreadyResolved(f"job {job_id} was already resolved by {c.resolution}")
        now = self.now()
        if now > c.challenge_deadline_ms:
            raise ChallengeWindowClosed(
                f"job {job_id}: challenge window closed at {c.challenge_deadline_ms}, now {now}")
        return c

    def _credit(self, account: str, wei: int) -> None:
        if wei == 0:
            return
        self.withdrawable_wei[account] = self.withdrawable_wei.get(account, 0) + wei
        self.acct.withdrawable_wei += wei

    def _record(self, e: Escrow, *, slashed: int, validator_reward: int, treasury_amount: int,
                fully_covered: bool, provider_payout: int, requester_refund: int,
                resolution: str, reporter: str) -> SettlementRecord:
        rec = SettlementRecord(
            job_id=e.job_id,
            provider_peer_id=e.provider,
            requester_peer_id=e.requester,
            amount=from_wei(e.amount_wei),
            state=e.state,
            slashed=slashed > 0,
            slash_amount=from_wei(slashed),
            validator_reward=from_wei(validator_reward),
            treasury_amount=from_wei(treasury_amount),
            provider_payout=from_wei(provider_payout),
            requester_refund=from_wei(requester_refund),
            fully_covered=fully_covered,
            remaining_stake=from_wei(self.stake_wei.get(e.provider, 0)),
            challenge_deadline_ms=e.challenge_deadline_ms,
            created_ms=self.now(),
        )
        self.records.append(rec)
        self.record_wei.append({
            "job_id": e.job_id, "amount_wei": e.amount_wei,
            "provider_payout_wei": provider_payout, "requester_refund_wei": requester_refund,
            "slash_amount_wei": slashed, "validator_reward_wei": validator_reward,
            "treasury_amount_wei": treasury_amount,
        })
        self.record_meta.append({
            "resolution": resolution,
            "reporter": reporter,
            "verdict": self.verdict_detail.get(e.job_id, {}).get("verdict", ""),
            "quality_score": self.verdict_detail.get(e.job_id, {}).get("quality_score", 0),
        })
        return rec

    # -- invariants ------------------------------------------------------

    def check_invariants(self) -> dict:
        """Assert that value is conserved, and return the accounting.

        Three separate identities, all in integer wei so equality is exact:

          1. Everything deposited is still somewhere: staked, unbonding, held in
             an open escrow, credited but unpulled, or withdrawn.
          2. Every escrow that left the OPEN/AWAITING states landed in exactly
             one of provider payout or requester refund.
          3. Every slashed wei landed in exactly one of validator reward or
             treasury.
        """
        a = self.acct
        stake_sum = sum(self.stake_wei.values())
        unbond_sum = sum(u.amount_wei for u in self.unbonding.values())
        credited_sum = sum(self.withdrawable_wei.values())
        escrow_sum = sum(e.amount_wei for e in self.escrows.values()
                         if e.state in (EscrowState.OPEN, EscrowState.AWAITING_VERIFICATION))

        problems: list[str] = []

        def eq(name: str, lhs: int, rhs: int) -> None:
            if lhs != rhs:
                problems.append(f"{name}: {lhs} != {rhs} (delta {lhs - rhs} wei)")

        eq("stake ledger vs accounting", stake_sum, a.stake_active_wei)
        eq("unbonding ledger vs accounting", unbond_sum, a.stake_unbonding_wei)
        eq("credited ledger vs accounting", credited_sum, a.withdrawable_wei)
        eq("escrow ledger vs accounting", escrow_sum, a.escrow_held_wei)

        # (1) total in == total out
        eq("value conservation",
           a.deposited_wei,
           stake_sum + unbond_sum + escrow_sum + credited_sum + a.withdrawn_wei)

        # (2) escrow outflow accounts for every closed escrow
        closed = sum(e.amount_wei for e in self.escrows.values()
                     if e.state in (EscrowState.SETTLED, EscrowState.SLASHED, EscrowState.REFUNDED))
        eq("escrow outflow", closed, a.provider_payout_wei + a.requester_refund_wei)

        # (3) the 80/20 split loses nothing
        eq("slash split", a.slashed_wei, a.validator_reward_wei + a.treasury_wei)

        # (4) per settlement, checked on the exact wei rather than on the float
        # view. Every escrow paid exactly one party, and every slash split in two.
        for w in self.record_wei:
            paid = w["provider_payout_wei"] + w["requester_refund_wei"]
            if paid != w["amount_wei"]:
                problems.append(f"job {w['job_id']}: paid {paid} != escrow {w['amount_wei']} wei")
            split = w["validator_reward_wei"] + w["treasury_amount_wei"]
            if split != w["slash_amount_wei"]:
                problems.append(
                    f"job {w['job_id']}: split {split} != slash {w['slash_amount_wei']} wei")

        # (5) the float view has to agree with the integers to within float64's
        # precision. A larger gap means a unit or rounding bug, not representation.
        for rec, w in zip(self.records, self.record_wei):
            for name, key in (("amount", "amount_wei"), ("slash_amount", "slash_amount_wei"),
                              ("validator_reward", "validator_reward_wei"),
                              ("treasury_amount", "treasury_amount_wei"),
                              ("provider_payout", "provider_payout_wei"),
                              ("requester_refund", "requester_refund_wei")):
                if not math.isclose(getattr(rec, name), from_wei(w[key]),
                                    rel_tol=1e-12, abs_tol=1e-18):
                    problems.append(
                        f"job {rec.job_id}: {name}={getattr(rec, name)} does not match "
                        f"{w[key]} wei")

        if problems:
            raise InvariantViolation("; ".join(problems))

        return {
            "backend": self.backend,
            "deposited": from_wei(a.deposited_wei),
            "withdrawn": from_wei(a.withdrawn_wei),
            "stake_active": from_wei(stake_sum),
            "stake_unbonding": from_wei(unbond_sum),
            "escrow_held": from_wei(escrow_sum),
            "withdrawable": from_wei(credited_sum),
            "provider_payout": from_wei(a.provider_payout_wei),
            "requester_refund": from_wei(a.requester_refund_wei),
            "validator_reward": from_wei(a.validator_reward_wei),
            "treasury": from_wei(a.treasury_wei),
            "slashed": from_wei(a.slashed_wei),
            "n_settlements": len(self.records),
            # Exact integers for anything that needs to add up rather than read
            # nicely; the floats above are for humans and CSVs.
            "wei": {
                "deposited": a.deposited_wei,
                "withdrawn": a.withdrawn_wei,
                "stake_active": stake_sum,
                "stake_unbonding": unbond_sum,
                "escrow_held": escrow_sum,
                "withdrawable": credited_sum,
                "provider_payout": a.provider_payout_wei,
                "requester_refund": a.requester_refund_wei,
                "validator_reward": a.validator_reward_wei,
                "treasury": a.treasury_wei,
                "slashed": a.slashed_wei,
            },
        }

    # -- reporting -------------------------------------------------------

    def rows(self) -> list[dict]:
        """Settlement records as CSV rows, each tagged with the backend that
        produced it and carrying the exact wei alongside the float view.

        `SettlementRecord` has no backend field and no field saying *why* the
        escrow closed, so this is the only place either is attached - never
        infer the backend later from an empty tx_hash, and never infer that a
        slash rested on a proof rather than on an oracle from the fact that it
        happened. `resolution` is one of challenge_window_elapsed,
        award_timeout_cancel, data_mismatch_proof, validator_verdict."""
        return [dict(r.model_dump(mode="json"), backend=self.backend, **m,
                     **{k: v for k, v in w.items() if k != "job_id"})
                for r, w, m in zip(self.records, self.record_wei, self.record_meta)]

    def total_paid_to_providers(self) -> float:
        return from_wei(self.acct.provider_payout_wei)

    @property
    def stakes(self) -> dict[str, float]:
        """Active stake per node, in GRID."""
        return {k: from_wei(v) for k, v in self.stake_wei.items()}


def centralized_cost(num_jobs: int, tokens_per_job: int, price_per_1k_tokens: float) -> float:
    """Cost of the same workload against a hosted API, for the cost experiment."""
    return num_jobs * (tokens_per_job / 1000) * price_per_1k_tokens
