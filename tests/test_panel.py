"""Tests for the diverse judge panel.

The panel exists to measure disagreement between judges, so the failures worth
guarding against are the ones that would make disagreement *disappear* into a
plausible-looking aggregate:

  * an ERROR counted as a vote - an unreachable member would then read as an
    acquittal under `any_fail`, or as a condemnation under `majority` whenever
    it happened to tip the count,
  * a tie or a split silently resolved to PASS or FAIL, which is one quorum rule
    wearing another one's name,
  * member verdicts collapsed into the aggregate, leaving no way to tell a
    unanimous panel from a 2-1 one,
  * a panel of clones reported as if it had been diverse.

Everything here runs offline: the mock backend, or a stub HTTP client for the
ollama-specific paths.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from edgegrid.da import DALayer, NAMESPACE_INFERENCE
from edgegrid.schemas import Commitment, Verdict, VerdictKind, sha256_hex

from verification.evaluator import Judge
from verification.panel import (QUORUM_RULES, JudgeMember, JudgePanel,
                                PanelOutcome, ThinkingAwareJudge, cohens_kappa,
                                pairwise_agreement, tally)
from verification.validator import ValidatorPool

PASS_JSON = json.dumps({"score": 5, "verdict": "PASS", "reason": "fine"})
FAIL_JSON = json.dumps({"score": 1, "verdict": "FAIL", "reason": "wrong"})


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _v(kind: VerdictKind, score=None, label="v") -> Verdict:
    return Verdict(job_id="j", validator_peer_id=label, verdict=kind,
                   judge_score=score, judge_backend="mock", judge_model="stub")


def _member(label: str, response, family: str = "fam", params_b: float = 1.0,
            **kw) -> JudgeMember:
    return JudgeMember(label=label,
                       judge=Judge(backend="mock", model=f"mock-{label}",
                                   mock_response=response, **kw),
                       family=family, params_b=params_b)


P, F, E = VerdictKind.PASS, VerdictKind.FAIL, VerdictKind.ERROR


# ==========================================================================
# quorum arithmetic, rule by rule
# ==========================================================================

@pytest.mark.parametrize("votes,expected", [
    ([P, P, P], P),
    ([F, F, F], F),
    ([P, P, F], P),
    ([F, F, P], F),
    ([P, F], E),            # 1-1 is not a majority
    ([P, P, F, F], E),      # 2-2 is not a majority
    ([F, F, P, P, P], P),
])
def test_majority_rule(votes, expected):
    kind, reason = tally([_v(k) for k in votes], "majority")
    assert kind is expected, reason


@pytest.mark.parametrize("votes,expected", [
    ([P, P, P], P),
    ([F, F, F], F),
    ([P, P, F], E),         # a split is not a consensus
    ([F, F, P], E),
    ([P], P),
    ([F], F),
])
def test_unanimous_rule(votes, expected):
    kind, reason = tally([_v(k) for k in votes], "unanimous")
    assert kind is expected, reason


@pytest.mark.parametrize("votes,expected", [
    ([P, P, P], P),
    ([P, P, F], F),         # one FAIL condemns
    ([F, P, P], F),
    ([F, F, F], F),
])
def test_any_fail_rule(votes, expected):
    kind, reason = tally([_v(k) for k in votes], "any_fail")
    assert kind is expected, reason


def test_unanimous_split_is_error_not_a_quiet_pass():
    """The split case is where a rule can be swapped for another without anyone
    noticing: resolving 2-1 to PASS is `majority` and resolving it to FAIL is
    `any_fail`. Under `unanimous` it must be neither."""
    kind, reason = tally([_v(P), _v(P), _v(F)], "unanimous")
    assert kind is E and kind is not P and kind is not F
    assert "split" in reason


def test_unknown_rule_raises():
    with pytest.raises(ValueError, match="unknown quorum rule"):
        tally([_v(P)], "plurality")


def test_every_declared_rule_is_implemented():
    for rule in QUORUM_RULES:
        assert tally([_v(P), _v(F), _v(F)], rule)[0] in (P, F, E)


# ==========================================================================
# ERROR is never a vote
# ==========================================================================

@pytest.mark.parametrize("rule", QUORUM_RULES)
def test_all_error_panel_is_error(rule):
    kind, reason = tally([_v(E), _v(E)], rule)
    assert kind is E
    assert "no member returned a usable score" in reason


@pytest.mark.parametrize("rule,votes,expected", [
    # One member down must not change what the surviving members decided.
    ("majority", [P, P, E], P),
    ("majority", [F, F, E], F),
    ("unanimous", [P, P, E], P),      # the errored member has not dissented
    ("unanimous", [F, F, E], F),
    ("any_fail", [P, P, E], P),       # an outage is NOT a fail
    ("any_fail", [F, P, E], F),
])
def test_error_is_excluded_not_counted(rule, votes, expected):
    kind, _ = tally([_v(k) for k in votes], rule)
    assert kind is expected


def test_error_never_tips_a_majority():
    """With 2 usable votes at 1-1 there is no majority, and a third member that
    errored must not be allowed to break the tie in either direction."""
    assert tally([_v(P), _v(F), _v(E)], "majority")[0] is E


def test_any_fail_does_not_read_an_outage_as_fraud():
    """The failure this mirrors is real: the previous evaluator turned a judge
    outage into verdict=FAIL, so an outage read as unanimous fraud detection.
    Under the conservative rule that would slash on an unreachable host."""
    kind, _ = tally([_v(P), _v(E), _v(E)], "any_fail")
    assert kind is P


def test_outcome_counts_errors_separately():
    panel = JudgePanel([_member("a", PASS_JSON), _member("b", PASS_JSON),
                        _member("c", PASS_JSON)])
    o = panel.aggregate([_v(P, 5), _v(F, 1), _v(E)], "majority")
    assert (o.n_pass, o.n_fail, o.n_error, o.n_voting) == (1, 1, 1, 2)
    assert o.verdict is E


# ==========================================================================
# a panel of identical judges disagrees about nothing
# ==========================================================================

def test_identical_judges_produce_zero_disagreement():
    """Three clones of one deterministic judge. Every member sees the same item
    and answers the same way, so `disagreement` must be False on every item and
    the panel must be marked not-diverse - a panel of clones is a repetition,
    not independent evidence, and the row has to say so."""
    members = [_member(f"clone{i}", None, family="qwen3vl", params_b=2.1)
               for i in range(3)]
    panel = JudgePanel(members, rule="majority")
    items = [
        ("What causes ocean tides?", "Ocean tides are caused by the Moon's gravity."),
        ("What happens if you swallow gum?", "It is never digested, contrary to fact."),
        ("How many planets are there?", "There are eight planets in the Solar System."),
    ]
    outcomes = [panel.deliberate(q, a, job_id=f"i{i}")
                for i, (q, a) in enumerate(items)]
    assert all(not o.disagreement for o in outcomes)
    assert all(o.unanimous for o in outcomes)
    assert all(o.n_error == 0 for o in outcomes)
    assert not panel.diverse and set(panel.families) == {"qwen3vl"}
    # and every rule must agree with every other, since the members do
    for o in outcomes:
        kinds = {panel.aggregate(o.verdicts, r).verdict for r in QUORUM_RULES}
        assert len(kinds) == 1


def test_disagreement_is_flagged_when_members_differ():
    panel = JudgePanel([_member("p", PASS_JSON), _member("f", FAIL_JSON),
                        _member("p2", PASS_JSON)])
    o = panel.deliberate("q", "a")
    assert o.disagreement and not o.unanimous
    assert (o.n_pass, o.n_fail) == (2, 1)
    assert panel.aggregate(o.verdicts, "majority").verdict is P
    assert panel.aggregate(o.verdicts, "any_fail").verdict is F
    assert panel.aggregate(o.verdicts, "unanimous").verdict is E


def test_single_error_is_not_a_disagreement():
    """One member down while the rest agree is an absence, not a dissent."""
    panel = JudgePanel([_member("a", PASS_JSON), _member("b", PASS_JSON),
                        _member("down", ConnectionError("host down"))],
                       rule="majority")
    o = panel.deliberate("q", "a")
    assert o.n_error == 1 and not o.disagreement and o.unanimous
    assert o.verdict is P


# ==========================================================================
# member verdicts survive
# ==========================================================================

def test_every_individual_verdict_is_kept():
    panel = JudgePanel([_member("a", PASS_JSON), _member("b", FAIL_JSON),
                        _member("c", ConnectionError("down"))])
    o = panel.deliberate("q", "a", job_id="job-1")
    assert len(o.verdicts) == 3 == len(o.member_rows())
    assert [r["judge_label"] for r in o.member_rows()] == ["a", "b", "c"]
    assert [r["verdict"] for r in o.member_rows()] == ["pass", "fail", "error"]
    # each member's own model, not the panel's label
    assert {r["judge_model"] for r in o.member_rows()} >= {"mock-a", "mock-b"}


def test_panel_verdict_names_the_panel_and_its_members():
    panel = JudgePanel([_member("a", PASS_JSON), _member("b", PASS_JSON)],
                       rule="unanimous")
    v = panel.score("q", "a", job_id="job-2")
    assert v.verdict is P
    assert v.judge_model.startswith("panel[unanimous](")
    assert "a=mock-a" in v.judge_model and "b=mock-b" in v.judge_model
    assert v.judge_backend == "panel:mock"
    # the integer rubric score belongs to a member, never to the panel
    assert v.quality_score is None and v.judge_score == 5.0


def test_record_callback_captures_member_votes_through_the_judge_api():
    """`score()` returns one Verdict, so anything using the Judge-compatible
    surface would otherwise lose the votes the panel exists to expose."""
    seen: list[PanelOutcome] = []
    panel = JudgePanel([_member("a", PASS_JSON), _member("b", FAIL_JSON)],
                       rule="any_fail", record=seen.append)
    v = panel.score("q", "a")
    assert v.verdict is F
    assert len(seen) == 1 and len(seen[0].verdicts) == 2


def test_panel_drops_into_a_validator_pool_seat(tmp_path):
    """Duck-typing is the point of `score()`: an existing ValidatorPool must be
    able to hold a panel without knowing it is one."""
    da = DALayer(root_dir=tmp_path / "da")
    answer = "The Moon's gravity causes the tides."
    blob = da.submit_blob(answer, NAMESPACE_INFERENCE, seal=True)
    commitment = Commitment(job_id="j1", provider_peer_id="p",
                            output_hash=sha256_hex(answer),
                            namespace=NAMESPACE_INFERENCE, blob_ref=blob.blob_id,
                            blob_height=blob.height, prompt_hash=sha256_hex("q"))
    panel = JudgePanel([_member("a", FAIL_JSON), _member("b", FAIL_JSON)],
                       rule="unanimous")
    pool = ValidatorPool([panel], quorum=1, da=da)
    outcome = pool.audit(commitment, "What causes tides?")
    assert outcome.verdict is F
    assert outcome.blob_verified and outcome.da_checked
    assert outcome.verdicts[0].judge_model.startswith("panel[unanimous](")


# ==========================================================================
# construction is explicit
# ==========================================================================

def test_empty_panel_rejected():
    with pytest.raises(ValueError, match="at least one member"):
        JudgePanel([])


def test_duplicate_labels_rejected():
    with pytest.raises(ValueError, match="unique"):
        JudgePanel([_member("a", PASS_JSON), _member("a", FAIL_JSON)])


def test_unknown_default_rule_rejected():
    with pytest.raises(ValueError, match="unknown quorum rule"):
        JudgePanel([_member("a", PASS_JSON)], rule="vibes")


def test_diversity_is_recorded_not_assumed():
    same = JudgePanel([_member("a", PASS_JSON, family="qwen3vl"),
                       _member("b", PASS_JSON, family="qwen3vl")])
    mixed = JudgePanel([_member("a", PASS_JSON, family="qwen3vl"),
                        _member("b", PASS_JSON, family="llama")])
    assert not same.diverse and mixed.diverse
    assert same.aggregate([_v(P), _v(P)]).diverse is False
    assert mixed.aggregate([_v(P), _v(P)]).diverse is True


def test_sequential_and_concurrent_panels_agree():
    votes = [PASS_JSON, FAIL_JSON, PASS_JSON]
    a = JudgePanel([_member(f"m{i}", v) for i, v in enumerate(votes)],
                   concurrent=True)
    b = JudgePanel([_member(f"m{i}", v) for i, v in enumerate(votes)],
                   concurrent=False)
    oa, ob = a.deliberate("q", "x"), b.deliberate("q", "x")
    assert oa.verdict is ob.verdict
    assert [v.verdict for v in oa.verdicts] == [v.verdict for v in ob.verdicts]


# ==========================================================================
# agreement statistics
# ==========================================================================

def test_kappa_is_one_for_identical_mixed_raters():
    a = ["pass", "fail", "pass", "fail"]
    k, po, n = cohens_kappa(a, list(a))
    assert n == 4 and po == 1.0 and k == pytest.approx(1.0)


def test_kappa_is_undefined_not_perfect_when_both_raters_are_constant():
    """Two judges that pass everything agree 100% of the time by construction.
    Chance agreement is also 100%, so kappa is 0/0 - reporting 1.0 there would
    claim a measured consensus that the data cannot support."""
    k, po, n = cohens_kappa(["pass"] * 6, ["pass"] * 6)
    assert po == 1.0 and n == 6
    assert k is None


def test_kappa_is_zero_for_chance_level_agreement():
    a = ["pass", "pass", "fail", "fail"]
    b = ["pass", "fail", "pass", "fail"]
    k, po, _ = cohens_kappa(a, b)
    assert po == 0.5 and k == pytest.approx(0.0)


def test_kappa_is_negative_for_systematic_opposition():
    k, _, _ = cohens_kappa(["pass", "fail", "pass", "fail"],
                           ["fail", "pass", "fail", "pass"])
    assert k is not None and k < 0


def test_pairwise_agreement_excludes_errors_and_says_how_many():
    votes = {
        "a": {"i1": "pass", "i2": "fail", "i3": "pass", "i4": "error"},
        "b": {"i1": "pass", "i2": "pass", "i3": "pass", "i4": "pass"},
    }
    (row,) = pairwise_agreement(votes)
    assert row["n_shared_items"] == 4
    assert row["n_compared"] == 3          # i4 dropped: a errored
    assert row["n_excluded_error"] == 1
    assert row["raw_agreement"] == pytest.approx(2 / 3, abs=1e-4)


def test_pairwise_agreement_reports_undefined_kappa_with_a_reason():
    votes = {"a": {"i1": "pass", "i2": "pass"}, "b": {"i1": "pass", "i2": "pass"}}
    (row,) = pairwise_agreement(votes)
    assert row["cohens_kappa"] == ""
    assert "undefined" in row["kappa_note"]


# ==========================================================================
# the thinking-model judge
# ==========================================================================

class _StubResponse:
    def __init__(self, body: dict):
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body


class _StubClient:
    """Captures the request body and replays a canned ollama response."""

    def __init__(self, body: dict):
        self.body = body
        self.sent: list[dict] = []

    def post(self, url, json=None, timeout=None):  # noqa: A002
        self.sent.append(json)
        return _StubResponse(self.body)

    def close(self) -> None:
        return None


def _stub_judge(body: dict, **kw) -> tuple[ThinkingAwareJudge, _StubClient]:
    j = ThinkingAwareJudge(backend="ollama", model="qwen3-vl:latest",
                           base_url="http://stub", max_retries=0, **kw)
    client = _StubClient(body)
    j.client = client
    return j, client


def test_thinking_field_reply_is_read_and_marked():
    """Reproduces the measured failure: ollama 0.30.7 puts this model's entire
    reply in `thinking` and leaves `response` empty, so the base Judge scored
    ERROR on every call. The reply must be used - and the row must say where it
    came from, or a reader cannot tell this path from the normal one."""
    j, _ = _stub_judge({"model": "qwen3-vl:latest", "response": "",
                        "thinking": FAIL_JSON})
    v = j.score("q", "a", job_id="j")
    assert v.verdict is F and v.judge_score == 1.0
    assert v.reason.startswith(ThinkingAwareJudge.THINKING_MARK)


def test_normal_response_is_not_marked():
    j, _ = _stub_judge({"model": "qwen3-vl:latest", "response": PASS_JSON,
                        "thinking": "some deliberation"})
    v = j.score("q", "a")
    assert v.verdict is P
    assert ThinkingAwareJudge.THINKING_MARK not in v.reason


def test_think_flag_is_sent_only_when_set():
    j, c = _stub_judge({"model": "m", "response": PASS_JSON}, think=False)
    j.score("q", "a")
    assert c.sent[0]["think"] is False

    j2, c2 = _stub_judge({"model": "m", "response": PASS_JSON})
    j2.score("q", "a")
    assert "think" not in c2.sent[0]


def test_empty_reply_in_both_fields_is_still_an_error():
    """The fallback must not manufacture a verdict out of nothing."""
    j, _ = _stub_judge({"model": "m", "response": "", "thinking": ""})
    v = j.score("q", "a")
    assert v.verdict is E and v.judge_score is None


# ==========================================================================
# the experiment's own arithmetic
# ==========================================================================

def test_wilson_interval_brackets_the_point_estimate():
    from experiments.judge_panel import wilson
    lo, hi = wilson(3, 10)
    assert lo < 0.3 < hi
    assert wilson(0, 10)[0] == pytest.approx(0.0)
    assert wilson(10, 10)[1] == pytest.approx(1.0)
    assert wilson(0, 0) == (None, None)


def test_items_key_is_stable_and_parameter_sensitive():
    from experiments.judge_panel import items_key
    a = items_key(12, ["negate", "swap_incorrect"], "local", "m", "lexical")
    b = items_key(12, ["swap_incorrect", "negate"], "local", "m", "lexical")
    c = items_key(11, ["negate", "swap_incorrect"], "local", "m", "lexical")
    assert a == b        # strategy order is not a different item set
    assert a != c        # question count is


def test_config_metrics_reports_the_hard_subset_separately():
    from experiments.judge_panel import config_metrics
    rows = []
    for qid in range(4):
        rows.append({"question_id": qid, "is_fraud": False,
                     "fraud_strategy": "none (honest)", "verdict": "pass",
                     "score": 5})
        rows.append({"question_id": qid, "is_fraud": True,
                     "fraud_strategy": "negate",
                     "verdict": "fail" if qid < 1 else "pass", "score": 3})
        rows.append({"question_id": qid, "is_fraud": True,
                     "fraud_strategy": "random_topic", "verdict": "fail",
                     "score": 1})
    m = {r["strategy"]: r for r in config_metrics(rows, "cfg", "judge")}
    assert m["negate"]["recall"] == pytest.approx(0.25)
    assert m["random_topic"]["recall"] == pytest.approx(1.0)
    assert m["HARD(negate+swap)"]["N_fraud"] == 4
    assert m["HARD(negate+swap)"]["recall"] == pytest.approx(0.25)
    assert m["OVERALL"]["recall"] == pytest.approx(5 / 8)
    assert m["negate"]["recall_ci_lo"] < 0.25 < m["negate"]["recall_ci_hi"]


# ==========================================================================
# the whole experiment, offline
# ==========================================================================

def test_experiment_runs_end_to_end_and_is_resumable(tmp_path, monkeypatch):
    """Drives `run()` with mock judges and gold-label answers, so the plumbing -
    item caching, vote caching, per-config metrics, agreement, figure - is
    exercised without a model server. The second call must judge nothing."""
    from experiments import judge_panel as jp

    specs = {
        "mock-a": {"backend": "mock", "model": "mock-a", "family": "famA",
                   "params_b": 1.0, "think": None, "role": "test"},
        "mock-b": {"backend": "mock", "model": "mock-b", "family": "famB",
                   "params_b": 2.0, "think": None, "role": "test"},
    }
    monkeypatch.setattr(jp, "JUDGE_SPECS", specs)
    monkeypatch.setattr(jp, "BASELINE", "mock-a")
    monkeypatch.setattr(jp, "SIZE_ARM", "mock-b")
    monkeypatch.setattr(jp, "DIVERSITY_ARM", "mock-b")

    common = dict(questions=3, honest_source="reference",
                  strategies=["negate", "random_topic"], concurrency=1,
                  cache_root=tmp_path / "cache", results_dir=tmp_path / "results",
                  figures_dir=tmp_path / "figures")
    d1 = jp.run(**common)
    head1 = json.loads((d1 / "headline.json").read_text())
    assert head1["n_judge_calls"] > 0
    assert head1["n_items"] == len(list((d1 / "raw.csv").read_text().splitlines())) // 5

    summary = (d1 / "summary.csv").read_text()
    for cfg in ("mock-a", "mock-b", "panel-majority", "panel-unanimous",
                "panel-any_fail"):
        assert cfg in summary
    assert (d1 / "agreement.csv").exists()
    assert (d1 / "panel_votes.csv").exists()
    assert (tmp_path / "figures" / "fig_judge_panel.png").exists()

    # resume: same items, same votes, zero new judge calls
    d2 = jp.run(**common)
    head2 = json.loads((d2 / "headline.json").read_text())
    assert head2["n_judge_calls"] == 0
    assert head2["items_from_cache"] is True
    assert head2["n_items_replayed_from_cache"] == head2["n_items"]
    assert head2["hypothesis"]["result"] in ("supports", "refutes", "inconclusive")

    # force: same items, votes judged again
    d3 = jp.run(**common, force=True)
    head3 = json.loads((d3 / "headline.json").read_text())
    assert head3["n_judge_calls"] > 0


def test_experiment_refuses_an_unknown_config(tmp_path):
    from experiments import judge_panel as jp
    with pytest.raises(ValueError, match="unknown judge config"):
        jp.run(configs=["gpt-9"], results_dir=tmp_path)


def test_experiment_refuses_an_unknown_quorum_rule(tmp_path):
    from experiments import judge_panel as jp
    with pytest.raises(ValueError, match="unknown quorum rule"):
        jp.run(panel_rules=["plurality"], results_dir=tmp_path)
