"""Validator agent pool: sampling, DA fraud proofs, and quorum voting.

The Phase-1 design promised a pool of validator agents that audit a random
fraction of jobs and vote; the repo only ever had a single judge call. This is
the missing piece, and it is ordered so the cheap check runs first:

  1. `should_audit` - a job is audited with probability `C.SAMPLE_RATE`. The
     decision is a keyed hash of the job id, so it is *deterministic* (anyone
     holding the epoch seed can recompute the audit set and check that a
     validator sampled honestly instead of choosing its targets) and
     *unpredictable* (a provider that does not yet hold the seed cannot tell
     which of its jobs will be looked at, so it cannot cheat only on the rest).

  2. DA verification - fetch the committed blob, recompute its sha256, check the
     Merkle inclusion proof. A mismatch is a *fraud proof*: it needs no judge, no
     model, and no subjective call, and it costs one hash. This is the only path
     here that produces certainty; everything downstream of it is an opinion.

  3. Quorum judging - only if the blob checks out do we spend judge calls. Each
     validator scores independently and the pool needs `quorum` concurring
     non-ERROR votes. ERROR votes are never counted as either side; a pool that
     cannot reach quorum returns ERROR, which upstream must treat as "do not
     settle yet", not as "innocent" and not as "guilty".
"""

from __future__ import annotations

import concurrent.futures as cf
import hashlib
import time
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

from edgegrid import config as C
from edgegrid.da import DALayer
from edgegrid.schemas import Commitment, Verdict, VerdictKind

from verification.evaluator import Judge

_MAX_U64 = 2 ** 64


def audit_score(job_id: str, seed: str | int = "") -> float:
    """Uniform-in-[0,1) keyed hash of (seed, job_id). Exposed so a test - or an
    auditor of a validator - can reproduce the sampling decision exactly."""
    h = hashlib.sha256(f"{seed}\x00{job_id}".encode("utf-8")).digest()
    return int.from_bytes(h[:8], "big") / _MAX_U64


def should_audit(job_id: str, seed: str | int = "",
                 rate: Optional[float] = None) -> bool:
    """True if this job falls in the audited sample. `rate` defaults to
    C.SAMPLE_RATE (5%)."""
    r = C.SAMPLE_RATE if rate is None else rate
    if r <= 0.0:
        return False
    if r >= 1.0:
        return True
    return audit_score(job_id, seed) < r


def sample_jobs(job_ids: Iterable[str], seed: str | int = "",
                rate: Optional[float] = None) -> list[str]:
    return [j for j in job_ids if should_audit(j, seed, rate)]


@dataclass
class AuditOutcome:
    """The pool's ruling on one job, with the vote breakdown kept intact.

    `verdict` is what settlement acts on. `fraud_proof` marks the case where the
    ruling is a cryptographic fact rather than a model's opinion - settlement can
    slash on that without a challenge window.

    `da_checked` and `blob_verified` are separate on purpose. `blob_verified`
    asserts that a blob was fetched and its hash and Merkle proof checked; a pool
    with no DA layer has not done that, and must not claim it did."""

    job_id: str
    verdict: VerdictKind
    fraud_proof: bool = False
    blob_verified: bool = False
    da_checked: bool = False
    audited: bool = True
    n_pass: int = 0
    n_fail: int = 0
    n_error: int = 0
    quorum: int = 1
    split: bool = False
    mean_score: Optional[float] = None
    reason: str = ""
    judge_calls: int = 0
    latency_ms: float = 0.0
    verdicts: list[Verdict] = field(default_factory=list)

    def row(self) -> dict:
        """Flat dict for a RunLog table."""
        v0 = self.verdicts[0] if self.verdicts else None
        return {
            "job_id": self.job_id,
            "verdict": self.verdict.value,
            "fraud_proof": self.fraud_proof,
            "blob_verified": self.blob_verified,
            "da_checked": self.da_checked,
            "audited": self.audited,
            "n_pass": self.n_pass,
            "n_fail": self.n_fail,
            "n_error": self.n_error,
            "quorum": self.quorum,
            "split": self.split,
            "mean_score": "" if self.mean_score is None else round(self.mean_score, 3),
            "judge_calls": self.judge_calls,
            "latency_ms": round(self.latency_ms, 1),
            "judge_backend": v0.judge_backend if v0 else "",
            "judge_model": v0.judge_model if v0 else "",
            "reason": self.reason[:300],
        }


class ValidatorPool:
    """N independent validator agents voting on the same job.

    `judges` may hold one Judge per agent (different backends or models give
    genuine independence) or a single Judge reused, which is cheaper but means
    the votes are correlated - `independent` records which, so a result table can
    never imply independence it did not have.
    """

    def __init__(self, judges: Sequence[Judge], peer_ids: Optional[Sequence[str]] = None,
                 quorum: Optional[int] = None, da: Optional[DALayer] = None,
                 max_workers: int = 4):
        if not judges:
            raise ValueError("a validator pool needs at least one judge")
        self.judges = list(judges)
        self.peer_ids = list(peer_ids or [f"validator-{i}" for i in range(len(self.judges))])
        if len(self.peer_ids) != len(self.judges):
            raise ValueError("peer_ids and judges must be the same length")
        q = C.VALIDATOR_QUORUM if quorum is None else quorum
        if not 1 <= q <= len(self.judges):
            raise ValueError(
                f"quorum {q} is impossible for a pool of {len(self.judges)} validators")
        self.quorum = q
        self.da = da
        self.max_workers = max_workers
        self.independent = len({id(j) for j in self.judges}) == len(self.judges)

    # -- step 2: DA -------------------------------------------------------

    def verify_commitment(self, commitment: Commitment) -> tuple[bool, str]:
        """(ok, reason). Cheap, objective, and run before any judge call."""
        if self.da is None:
            return False, "no DA layer configured; commitment not verified"
        if not self.da.verify_blob(commitment.blob_ref, commitment.output_hash):
            data = self.da.get_blob(commitment.blob_ref)
            if data is None:
                return False, f"blob {commitment.blob_ref} is not retrievable from DA"
            return False, ("committed output_hash does not match the blob or its "
                           "Merkle proof does not land on the block root")
        return True, "blob hash and Merkle inclusion proof both check out"

    # -- step 3: judging --------------------------------------------------

    def _vote(self, prompt: str, output: str, job_id: str,
              blob_verified: bool) -> list[Verdict]:
        def one(i: int) -> Verdict:
            return self.judges[i].score(prompt, output, job_id=job_id,
                                        validator_peer_id=self.peer_ids[i],
                                        blob_verified=blob_verified)

        if len(self.judges) == 1:
            return [one(0)]
        with cf.ThreadPoolExecutor(max_workers=min(self.max_workers, len(self.judges))) as ex:
            return list(ex.map(one, range(len(self.judges))))

    def tally(self, verdicts: Sequence[Verdict]) -> tuple[VerdictKind, bool, str]:
        """Quorum rule. Returns (verdict, split, reason).

        FAIL is checked first, so with a quorum at or below half the pool a
        single dishonest validator can force a slash - that is why quorum should
        be set above n/2 in any deployment where validators are not trusted. The
        `split` flag marks every case where both sides reached quorum."""
        n_pass = sum(1 for v in verdicts if v.verdict is VerdictKind.PASS)
        n_fail = sum(1 for v in verdicts if v.verdict is VerdictKind.FAIL)
        n_err = len(verdicts) - n_pass - n_fail
        split = n_pass >= self.quorum and n_fail >= self.quorum
        if n_fail >= self.quorum:
            return VerdictKind.FAIL, split, f"{n_fail}/{len(verdicts)} validators voted fail"
        if n_pass >= self.quorum:
            return VerdictKind.PASS, split, f"{n_pass}/{len(verdicts)} validators voted pass"
        return (VerdictKind.ERROR, False,
                f"no quorum: pass={n_pass} fail={n_fail} error={n_err} quorum={self.quorum}")

    # -- the whole audit --------------------------------------------------

    def audit(self, commitment: Commitment, prompt: str,
              output: Optional[str] = None) -> AuditOutcome:
        """Audit one job. `output` is only used when there is no DA layer; with
        one, the answer judged is the bytes actually committed, never a copy the
        provider hands over separately."""
        t0 = time.monotonic()
        job_id = commitment.job_id
        da_checked = self.da is not None
        if not da_checked:
            # No DA layer means the commitment was NOT verified. Reporting
            # blob_verified=True here (which this method used to do) puts a
            # cryptographic claim on a Verdict that nothing backs, and settlement
            # slashes on fraud_proof without a challenge window.
            blob_ok, blob_reason = False, "no DA layer configured; commitment unverified"
        else:
            blob_ok, blob_reason = self.verify_commitment(commitment)

        if da_checked and not blob_ok:
            return AuditOutcome(
                job_id=job_id, verdict=VerdictKind.FAIL, fraud_proof=True,
                blob_verified=False, da_checked=True, quorum=self.quorum,
                n_fail=0, judge_calls=0,
                reason=f"DA fraud proof: {blob_reason}",
                latency_ms=(time.monotonic() - t0) * 1000.0)

        judged = output
        if da_checked:
            data = self.da.get_blob(commitment.blob_ref)
            if data is None:
                # verify_commitment said the blob is retrievable and matches, so
                # a None here is the store contradicting itself. Judging the
                # provider's own copy instead would silently undo the guarantee
                # this whole path exists to give.
                return AuditOutcome(
                    job_id=job_id, verdict=VerdictKind.ERROR, fraud_proof=False,
                    blob_verified=False, da_checked=True, quorum=self.quorum,
                    judge_calls=0,
                    reason=(f"DA store is inconsistent: blob {commitment.blob_ref} "
                            "verified but then read back empty; refusing to judge "
                            "the provider-supplied copy"),
                    latency_ms=(time.monotonic() - t0) * 1000.0)
            judged = data.decode("utf-8", "replace")
        if judged is None:
            raise ValueError("nothing to judge: pass `output` or configure a DA layer")

        verdicts = self._vote(prompt, judged, job_id, blob_ok)
        kind, split, reason = self.tally(verdicts)
        scored = [v.judge_score for v in verdicts if v.judge_score is not None]
        return AuditOutcome(
            job_id=job_id, verdict=kind, fraud_proof=False, blob_verified=blob_ok,
            da_checked=da_checked,
            n_pass=sum(1 for v in verdicts if v.verdict is VerdictKind.PASS),
            n_fail=sum(1 for v in verdicts if v.verdict is VerdictKind.FAIL),
            n_error=sum(1 for v in verdicts if v.verdict is VerdictKind.ERROR),
            quorum=self.quorum, split=split,
            mean_score=(sum(scored) / len(scored)) if scored else None,
            reason=f"{reason}; {blob_reason}", judge_calls=len(verdicts),
            latency_ms=(time.monotonic() - t0) * 1000.0, verdicts=verdicts)

    def audit_sampled(self, jobs: Sequence[tuple[Commitment, str]], seed: str | int = "",
                      rate: Optional[float] = None) -> list[AuditOutcome]:
        """Audit only the sampled fraction. Unsampled jobs are returned as
        `audited=False` outcomes rather than omitted, so a caller can never
        mistake "not looked at" for "passed"."""
        out: list[AuditOutcome] = []
        for commitment, prompt in jobs:
            if not should_audit(commitment.job_id, seed, rate):
                out.append(AuditOutcome(
                    job_id=commitment.job_id, verdict=VerdictKind.ERROR,
                    audited=False, quorum=self.quorum,
                    reason="not sampled for audit"))
                continue
            out.append(self.audit(commitment, prompt))
        return out
