"""A panel of heterogeneous judges voting on one answer.

`ValidatorPool` already votes, but its judges are typically N copies of the same
model, and it records that fact (`independent`) precisely because copies of one
model are not independent evidence. This module is the other half: a panel whose
members are *deliberately different* models, so that the thing being measured is
whether the disagreement between families buys anything.

Why that matters here. The repo's measured judge failure is not noise - the
2.1B qwen3-vl judge passes negated and swapped answers at scores of 3.8-4.05,
i.e. confidently, and it passes TruthfulQA's own labelled misconceptions. A
failure of that shape is a property of what the model believes, not of how many
times it is asked. Asking the same family again - even a much larger member of
it - can only help if the belief is not shared, which is exactly the open
question. So the panel records, per item:

  * every member's own Verdict, kept in full and never collapsed,
  * which quorum rule produced the panel verdict,
  * whether the members disagreed,
  * the member families, and whether the panel is actually diverse.

Three quorum rules, and the differences between them are the point:

    majority   - a strict majority of the members that returned a usable score.
                 A tie is not a majority and yields ERROR.
    unanimous  - every voting member must agree. Any split is ERROR. This is
                 `ValidatorPool` with quorum = n, and its ERROR rate is a direct
                 read-out of inter-judge disagreement.
    any_fail   - one FAIL condemns. The conservative rule for a slashing
                 system, and the only rule under which a single diverse judge
                 that catches a fraud the others miss can save the panel.

ERROR is never a vote. A member whose backend was unreachable, or whose reply
carried no recoverable score, is counted in `n_error` and excluded from every
quorum arithmetic - it is neither an acquittal nor a condemnation. A panel where
no member returned a usable score is ERROR, which upstream must read as "do not
settle yet".
"""

from __future__ import annotations

import concurrent.futures as cf
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

from edgegrid import config as C
from edgegrid.schemas import Verdict, VerdictKind

from verification.evaluator import Judge

QUORUM_RULES = ("majority", "unanimous", "any_fail")


class ThinkingAwareJudge(Judge):
    """A Judge that can talk to an Ollama *thinking* model.

    Measured against ollama 0.30.7, `qwen3-vl:latest` (8.8B) scored 0 usable
    verdicts through the base `Judge`, and the cause is not the model:

      * the model is a reasoning model, so ollama splits its reply into two
        fields, `thinking` and `response`. `Judge._call_ollama` reads only
        `response`, which for this model comes back "" - the entire answer,
        JSON and all, is in `thinking`. An empty string has no score, so every
        call became `JudgeParseError` -> `VerdictKind.ERROR`.
      * left to reason freely it also burns the whole `num_predict` budget
        mid-thought (120 tokens of deliberation, `done_reason: length`, no
        answer at all) at roughly 3 tokens/s on CPU.

    So this subclass does two things, both recorded rather than assumed:

      * sends ollama's `think` flag when the caller sets one. With
        `think=False` the same model answers the judge prompt in 40 tokens and
        14 s instead of running out of budget. That is a change to how the judge
        is operated and it is written into the experiment's params, because a
        reasoning model judged with reasoning off is not the same instrument as
        one judged with it on.
      * falls back to the `thinking` field when `response` is empty, and marks
        the Verdict's reason when it does, so no row can silently claim to have
        come down the normal path. The marker is per call and thread-local:
        one Judge is shared across a thread pool, so an instance attribute would
        be attributed to whichever item happened to read it last.

    The base `Judge` is unchanged. This is the narrower fix; the durable one is
    for `Judge._call_ollama` to stop discarding `thinking` outright.
    """

    THINKING_MARK = "[reply arrived in ollama `thinking` field]"

    def __init__(self, *args, think: Optional[bool] = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.think = think
        self._tl = threading.local()

    def _call_ollama(self, prompt: str, output: str) -> tuple[str, str]:
        from verification.evaluator import JUDGE_SYSTEM_PROMPT, JUDGE_USER_PROMPT
        payload = {
            "model": self.model_requested,
            "system": JUDGE_SYSTEM_PROMPT,
            "prompt": JUDGE_USER_PROMPT.format(prompt=prompt, output=output),
            "stream": False,
            "format": "json",
            "options": {"temperature": self.temperature,
                        "num_predict": self.num_predict},
        }
        if self.think is not None:
            payload["think"] = self.think
        r = self.client.post(f"{self.base_url}/api/generate", json=payload,
                             timeout=self.timeout_s)
        r.raise_for_status()
        body = r.json()
        if "error" in body:
            raise RuntimeError(f"ollama error: {body['error']}")
        text = body.get("response") or ""
        thinking = body.get("thinking") or ""
        self._tl.source = "response"
        if not text.strip() and thinking.strip():
            self._tl.source = "thinking"
            text = thinking
        return text, (body.get("model") or "")

    def score(self, *args, **kwargs) -> Verdict:
        self._tl.source = ""
        v = super().score(*args, **kwargs)
        if getattr(self._tl, "source", "") == "thinking":
            return v.model_copy(update={
                "reason": f"{self.THINKING_MARK} {v.reason}"[:400]})
        return v


@dataclass(frozen=True)
class JudgeMember:
    """One seat on the panel.

    `family` and `params_b` are carried so a result table can state what kind of
    diversity the panel had. They are metadata supplied by the caller - nothing
    here verifies that a model really belongs to the family it is labelled with,
    so they describe the experiment's intent, not a checked fact."""

    label: str
    judge: Judge
    family: str = ""
    params_b: Optional[float] = None

    def descriptor(self) -> str:
        bits = [self.label]
        if self.family:
            bits.append(self.family)
        if self.params_b is not None:
            bits.append(f"{self.params_b:g}B")
        return f"{bits[0]} ({', '.join(bits[1:])})" if len(bits) > 1 else bits[0]


@dataclass
class PanelOutcome:
    """One panel deliberation, with every member vote kept.

    The disagreement between members is the measurement this module exists to
    produce, so it is a first-class field and the member verdicts are never
    dropped in favour of the aggregate."""

    verdict: VerdictKind
    rule: str
    members: list[str]
    verdicts: list[Verdict]
    n_pass: int = 0
    n_fail: int = 0
    n_error: int = 0
    n_voting: int = 0
    unanimous: bool = False
    disagreement: bool = False
    mean_score: Optional[float] = None
    reason: str = ""
    panel_model: str = ""
    panel_backend: str = ""
    families: tuple[str, ...] = ()
    diverse: bool = False
    latency_ms: float = 0.0

    def member_rows(self) -> list[dict]:
        """One flat row per member vote, for a RunLog table. The individual
        verdicts are the data, not a diagnostic, so they get their own table."""
        rows = []
        for label, v in zip(self.members, self.verdicts):
            rows.append({
                "judge_label": label,
                "verdict": v.verdict.value,
                "score": "" if v.judge_score is None else v.judge_score,
                "judge_backend": v.judge_backend,
                "judge_model": v.judge_model,
                "latency_ms": round(v.latency_ms, 1),
                "reason": (v.reason or "")[:300],
            })
        return rows

    def row(self) -> dict:
        return {
            "panel_verdict": self.verdict.value,
            "rule": self.rule,
            "members": "|".join(self.members),
            "families": "|".join(self.families),
            "diverse": self.diverse,
            "n_pass": self.n_pass,
            "n_fail": self.n_fail,
            "n_error": self.n_error,
            "n_voting": self.n_voting,
            "unanimous": self.unanimous,
            "disagreement": self.disagreement,
            "mean_score": "" if self.mean_score is None else round(self.mean_score, 3),
            "panel_backend": self.panel_backend,
            "panel_model": self.panel_model,
            "latency_ms": round(self.latency_ms, 1),
            "reason": self.reason[:300],
        }


def tally(verdicts: Sequence[Verdict], rule: str) -> tuple[VerdictKind, str]:
    """Apply one quorum rule to a set of member verdicts. Pure, so a panel can be
    re-aggregated under a different rule from stored votes without re-judging.

    Returns (panel verdict, reason). ERROR verdicts are excluded from the
    arithmetic entirely; if that leaves nothing, the panel is ERROR."""
    if rule not in QUORUM_RULES:
        raise ValueError(f"unknown quorum rule {rule!r}; choose {list(QUORUM_RULES)}")
    n_pass = sum(1 for v in verdicts if v.verdict is VerdictKind.PASS)
    n_fail = sum(1 for v in verdicts if v.verdict is VerdictKind.FAIL)
    n_err = len(verdicts) - n_pass - n_fail
    n_vote = n_pass + n_fail
    tail = f"(pass={n_pass} fail={n_fail} error={n_err} of {len(verdicts)})"
    if n_vote == 0:
        return VerdictKind.ERROR, f"no member returned a usable score {tail}"

    if rule == "any_fail":
        if n_fail:
            return VerdictKind.FAIL, f"any_fail: {n_fail} member(s) voted fail {tail}"
        return VerdictKind.PASS, f"any_fail: no member voted fail {tail}"

    if rule == "unanimous":
        if n_fail == n_vote:
            return VerdictKind.FAIL, f"unanimous fail {tail}"
        if n_pass == n_vote:
            return VerdictKind.PASS, f"unanimous pass {tail}"
        # A split is not a verdict. Calling it PASS would let one confident
        # member acquit, and calling it FAIL would let one condemn; both are
        # other rules wearing this one's name.
        return VerdictKind.ERROR, f"unanimous: members split, no consensus {tail}"

    if n_fail * 2 > n_vote:
        return VerdictKind.FAIL, f"majority fail {n_fail}/{n_vote} {tail}"
    if n_pass * 2 > n_vote:
        return VerdictKind.PASS, f"majority pass {n_pass}/{n_vote} {tail}"
    return VerdictKind.ERROR, f"majority: tied {n_pass}-{n_fail}, no majority {tail}"


class JudgePanel:
    """N independent judges, each its own backend/model, scoring one answer.

    Duck-types `Judge`: `score()` returns a single aggregate `Verdict`, so a
    panel can be dropped into a `ValidatorPool` seat unchanged. Use
    `deliberate()` when the member votes are wanted, which for a measurement they
    always are.

    Args:
        members: the seats. At least one; labels must be unique.
        rule: default quorum rule for `deliberate`/`score`.
        concurrent: run members in threads. Set False when the backends share a
            constrained host and serialising is faster than contending.
        record: optional callback given every PanelOutcome, so a caller using the
            `Judge`-compatible `score()` path can still capture member votes.
    """

    def __init__(self, members: Sequence[JudgeMember], rule: str = "majority",
                 concurrent: bool = True, max_workers: int = 8,
                 record: Optional[Callable[[PanelOutcome], None]] = None,
                 pass_threshold: int = C.PASS_THRESHOLD):
        if not members:
            raise ValueError("a judge panel needs at least one member")
        labels = [m.label for m in members]
        if len(set(labels)) != len(labels):
            raise ValueError(f"panel member labels must be unique, got {labels}")
        if rule not in QUORUM_RULES:
            raise ValueError(f"unknown quorum rule {rule!r}; choose {list(QUORUM_RULES)}")
        self.members = list(members)
        self.rule = rule
        self.concurrent = concurrent
        self.max_workers = max_workers
        self.record = record
        self.pass_threshold = pass_threshold
        self.families = tuple(m.family or "unknown" for m in self.members)
        # One family means the panel is a size/seed ablation, not a diverse one.
        # Recorded rather than assumed, because "panel" on its own implies a
        # diversity this construction may not have.
        self.diverse = len(set(self.families)) > 1
        # Kept so `score()` can label an all-ERROR panel without a served name.
        self.model_requested = self.panel_label(requested=True)

    # -- labels ----------------------------------------------------------

    def panel_label(self, verdicts: Optional[Sequence[Verdict]] = None,
                    rule: Optional[str] = None, requested: bool = False) -> str:
        """The string recorded as `judge_model`, naming the panel and its members.

        Member names come from the served model each member reported, so panel
        provenance is as specific as single-judge provenance. Falling back to the
        requested id is marked, never presented as a served name."""
        names = []
        for i, m in enumerate(self.members):
            served = ""
            if verdicts is not None and i < len(verdicts):
                served = verdicts[i].judge_model
            if not served or requested:
                served = m.judge.model_used or f"{m.judge.model_requested} (requested)"
            names.append(f"{m.label}={served}")
        return f"panel[{rule or self.rule}]({', '.join(names)})"

    def backend_label(self) -> str:
        backends = sorted({m.judge.backend for m in self.members})
        return f"panel:{'+'.join(backends)}"

    # -- deliberation ----------------------------------------------------

    def _vote(self, prompt: str, output: str, job_id: str,
              blob_verified: bool) -> list[Verdict]:
        def one(i: int) -> Verdict:
            m = self.members[i]
            return m.judge.score(prompt, output, job_id=job_id,
                                 validator_peer_id=m.label,
                                 blob_verified=blob_verified)

        if not self.concurrent or len(self.members) == 1:
            return [one(i) for i in range(len(self.members))]
        with cf.ThreadPoolExecutor(
                max_workers=min(self.max_workers, len(self.members))) as ex:
            return list(ex.map(one, range(len(self.members))))

    def aggregate(self, verdicts: Sequence[Verdict], rule: Optional[str] = None,
                  latency_ms: float = 0.0) -> PanelOutcome:
        """Build a PanelOutcome from member verdicts already in hand.

        Separate from `deliberate` so stored votes can be re-aggregated under a
        different rule with no further judge calls - and so a test can drive the
        quorum arithmetic without a backend."""
        r = rule or self.rule
        verdicts = list(verdicts)
        kind, reason = tally(verdicts, r)
        n_pass = sum(1 for v in verdicts if v.verdict is VerdictKind.PASS)
        n_fail = sum(1 for v in verdicts if v.verdict is VerdictKind.FAIL)
        n_err = len(verdicts) - n_pass - n_fail
        n_vote = n_pass + n_fail
        scored = [v.judge_score for v in verdicts if v.judge_score is not None]
        # Unanimity and disagreement are about the members that actually voted;
        # an unreachable member has not disagreed with anything.
        unanimous = n_vote > 0 and (n_pass == n_vote or n_fail == n_vote)
        return PanelOutcome(
            verdict=kind, rule=r, members=[m.label for m in self.members],
            verdicts=verdicts, n_pass=n_pass, n_fail=n_fail, n_error=n_err,
            n_voting=n_vote, unanimous=unanimous,
            disagreement=n_vote > 1 and not unanimous,
            mean_score=(sum(scored) / len(scored)) if scored else None,
            reason=reason, panel_model=self.panel_label(verdicts, r),
            panel_backend=self.backend_label(), families=self.families,
            diverse=self.diverse, latency_ms=latency_ms)

    def deliberate(self, prompt: str, output: str, job_id: str = "",
                   rule: Optional[str] = None,
                   blob_verified: bool = False) -> PanelOutcome:
        """Run every member on one (prompt, answer) and aggregate."""
        t0 = time.monotonic()
        verdicts = self._vote(prompt, output, job_id, blob_verified)
        outcome = self.aggregate(verdicts, rule, (time.monotonic() - t0) * 1000.0)
        if self.record is not None:
            self.record(outcome)
        return outcome

    # -- Judge-compatible surface ----------------------------------------

    def score(self, prompt: str, output: str, job_id: str = "",
              validator_peer_id: str = "", blob_verified: bool = False) -> Verdict:
        """Aggregate verdict only, with the same signature as `Judge.score`, so a
        panel can occupy a `ValidatorPool` seat. `judge_model` names the panel,
        its rule and every member, so provenance survives the collapse; pass a
        `record` callback to the constructor to keep the member votes too."""
        o = self.deliberate(prompt, output, job_id=job_id, blob_verified=blob_verified)
        return Verdict(
            job_id=job_id, validator_peer_id=validator_peer_id or "panel",
            verdict=o.verdict,
            # The mean of member scores is reported as judge_score for continuity
            # with a single judge, but quality_score - the integer the rubric
            # defines - is left unset: no member gave that number.
            quality_score=None, judge_score=o.mean_score,
            reason=o.reason[:400], judge_backend=o.panel_backend,
            judge_model=o.panel_model, blob_verified=blob_verified,
            latency_ms=o.latency_ms)

    def close(self) -> None:
        for m in self.members:
            m.judge.close()


# --------------------------------------------------------------------------
# inter-judge agreement
# --------------------------------------------------------------------------

def cohens_kappa(a: Sequence[str], b: Sequence[str]) -> tuple[Optional[float], float, int]:
    """(kappa, raw agreement, n) over paired binary labels.

    kappa is None - not 0.0, and not silently omitted - when chance agreement is
    1.0, which happens whenever both raters used a single label throughout. The
    statistic is genuinely undefined there (0/0), and a judge panel that agrees
    on everything is exactly the case where it arises, so it must not be reported
    as perfect agreement or as none."""
    pairs = [(x, y) for x, y in zip(a, b)]
    n = len(pairs)
    if n == 0:
        return None, 0.0, 0
    po = sum(1 for x, y in pairs if x == y) / n
    labels = {x for x, _ in pairs} | {y for _, y in pairs}
    pe = 0.0
    for lab in labels:
        pa = sum(1 for x, _ in pairs if x == lab) / n
        pb = sum(1 for _, y in pairs if y == lab) / n
        pe += pa * pb
    if abs(1.0 - pe) < 1e-12:
        return None, po, n
    return (po - pe) / (1.0 - pe), po, n


def pairwise_agreement(votes: dict[str, dict[str, str]]) -> list[dict]:
    """Raw agreement and kappa for every pair of judges.

    `votes` maps judge label -> item key -> verdict string. Only items both
    judges scored *and* where neither errored are compared: an ERROR is not a
    disagreement, it is an absence, and folding it in would let an outage read as
    diversity. The count of items excluded for that reason is reported."""
    labels = sorted(votes)
    out: list[dict] = []
    for i, a in enumerate(labels):
        for b in labels[i + 1:]:
            shared = sorted(set(votes[a]) & set(votes[b]))
            usable = [k for k in shared
                      if votes[a][k] in ("pass", "fail") and votes[b][k] in ("pass", "fail")]
            k, po, n = cohens_kappa([votes[a][x] for x in usable],
                                    [votes[b][x] for x in usable])
            out.append({
                "judge_a": a, "judge_b": b, "n_compared": n,
                "n_shared_items": len(shared),
                "n_excluded_error": len(shared) - len(usable),
                "raw_agreement": round(po, 4),
                "cohens_kappa": "" if k is None else round(k, 4),
                "kappa_note": ("undefined: chance agreement is 1.0 (both judges "
                               "used a single label)") if k is None else "",
            })
    return out
