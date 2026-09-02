"""Tests for the verification track.

The four defects these are written against are all *silent* ones - each produced
a plausible number rather than an error, which is why they survived a published
result. Each therefore gets a test that fails loudly if it comes back:

  * a judge outage must be ERROR, never FAIL (an outage read as fraud detection),
  * an unparseable response must be ERROR, never PASS (score defaulted to 3,
    which equalled the pass threshold),
  * a missing GROQ_API_KEY must raise, never swap in a mock,
  * per-strategy metrics must use their own subset's denominators.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from edgegrid import config as C
from edgegrid.da import DALayer
from edgegrid.schemas import Commitment, VerdictKind, sha256_hex

from verification.evaluator import (BACKENDS, Judge, JudgeConfigError,
                                    JudgeParseError, _parse, strip_think)
from verification.fraud_injector import (STRATEGIES, check_validity, inject_fraud,
                                         polarity, similarity)
from verification.run_harness import Generator, metrics_for
from verification.validator import (ValidatorPool, audit_score, sample_jobs,
                                    should_audit)


# ==========================================================================
# the four silent-failure defects
# ==========================================================================

def test_judge_outage_is_error_not_fail():
    """A judge that cannot be reached must produce ERROR.

    The previous evaluator returned score=1 / verdict=fail from its exception
    handler, so a judge outage across a run read as unanimous fraud detection -
    and every one of those verdicts would have slashed a stake."""
    j = Judge(backend="mock", max_retries=1,
              mock_response=ConnectionError("judge host is down"))
    v = j.score("What causes tides?", "The Moon's gravity.")
    assert v.verdict is VerdictKind.ERROR
    assert v.verdict is not VerdictKind.FAIL
    assert v.quality_score is None and v.judge_score is None
    assert "judge unavailable" in v.reason


def test_real_backend_outage_is_error():
    """Same property against a real HTTP backend pointed at a dead port."""
    j = Judge(backend="ollama", base_url="http://127.0.0.1:1", max_retries=0,
              timeout_s=2.0)
    v = j.score("What causes tides?", "The Moon's gravity.")
    assert v.verdict is VerdictKind.ERROR
    assert v.judge_backend == "ollama"
    j.close()


@pytest.mark.parametrize("junk", [
    "I think the answer is probably fine, honestly.",
    "",
    "<think>the model reasoned but never emitted JSON</think>",
    '{"verdict": "PASS"}',          # verdict but no score
    '{"score": "excellent"}',       # unusable score
    '{"score": 9}',                 # outside the rubric
])
def test_unparseable_response_is_error_not_pass(junk):
    """An unparseable judge response must be ERROR.

    The old parser's last fallback was `score = 3`, and PASS_THRESHOLD is 3, so
    every response the parser could not read silently became a PASS."""
    j = Judge(backend="mock", max_retries=0, mock_response=junk)
    v = j.score("What causes tides?", "The Moon's gravity.")
    assert v.verdict is VerdictKind.ERROR, f"{junk!r} did not produce ERROR"
    assert v.verdict is not VerdictKind.PASS
    assert v.quality_score is None


def test_parse_never_defaults_to_the_pass_threshold():
    """Directly: the parser raises rather than inventing a score."""
    with pytest.raises(JudgeParseError):
        _parse("no json here and no score either")
    assert C.PASS_THRESHOLD == 3, "this defect was specific to threshold == default score"


def test_missing_groq_key_raises(monkeypatch):
    """A missing key is a hard failure, not a silent switch to a mock.

    The old constructor printed a notice and set backend='mock', whose keyword
    list was copied out of this repo's own fixtures - so it scored the project's
    test data far better than any real judge would."""
    monkeypatch.setattr(C, "GROQ_API_KEY", "")
    with pytest.raises(JudgeConfigError) as e:
        Judge(backend="groq")
    assert "GROQ_API_KEY" in str(e.value)


def test_missing_groq_key_never_yields_a_mock(monkeypatch):
    monkeypatch.setattr(C, "GROQ_API_KEY", "")
    try:
        j = Judge(backend="groq")
    except JudgeConfigError:
        return
    pytest.fail(f"constructed a judge with no key, backend={j.backend}")


def test_no_auto_backend():
    """'auto' was accepted into the Groq branch but dispatched to Ollama, POSTing
    a Groq model name to localhost:11434."""
    assert "auto" not in BACKENDS
    with pytest.raises(JudgeConfigError):
        Judge(backend="auto")


def test_mock_requires_being_asked_for_by_name():
    j = Judge(backend="mock")
    v = j.score("What is the capital of France?", "Paris is the capital of France.")
    assert v.judge_backend == "mock"
    assert v.verdict in (VerdictKind.PASS, VerdictKind.FAIL)


# ==========================================================================
# per-strategy denominators
# ==========================================================================

def _row(qid, fraud, strategy, verdict, score=3):
    return {"question_id": qid, "is_fraud": fraud, "fraud_strategy": strategy,
            "verdict": verdict, "score": score}


def test_per_strategy_denominators_are_subset_specific():
    """Each strategy row must count only its own fraud rows.

    The old harness read the global `evaluated_records` for the honest class, so
    all four strategy rows printed an identical FP/TN pair regardless of what
    that strategy did."""
    honest = [_row(1, False, "none (honest)", "pass"),
              _row(2, False, "none (honest)", "fail"),
              _row(3, False, "none (honest)", "pass")]
    negate = [_row(q, True, "negate", "fail") for q in (1, 2, 3)]
    swap = [_row(1, True, "swap_incorrect", "pass"),
            _row(2, True, "swap_incorrect", "pass"),
            _row(3, True, "swap_incorrect", "fail")]

    m_neg = metrics_for(negate, honest, "negate")
    m_swap = metrics_for(swap, honest, "swap_incorrect")
    assert m_neg["N_fraud"] == 3 and m_swap["N_fraud"] == 3
    assert (m_neg["TP"], m_neg["FN"]) == (3, 0)
    assert (m_swap["TP"], m_swap["FN"]) == (1, 2)
    assert m_neg["recall"] != m_swap["recall"]


def test_dropped_corruption_moves_the_honest_denominator():
    """When a corruption is dropped as invalid, that question leaves the
    strategy's subset - and so does its honest counterpart."""
    honest = [_row(q, False, "none (honest)", "pass") for q in (1, 2, 3)]
    negate = [_row(q, True, "negate", "fail") for q in (1, 2)]   # q3 dropped
    qids = {r["question_id"] for r in negate}
    h_sub = [r for r in honest if r["question_id"] in qids]
    m = metrics_for(negate, h_sub, "negate")
    assert m["N_honest"] == 2, "honest denominator must follow the subset, not the global list"


def test_errors_are_excluded_from_rates_and_reported_separately():
    honest = [_row(1, False, "none (honest)", "pass"),
              _row(2, False, "none (honest)", "error", score="")]
    fraud = [_row(1, True, "negate", "fail"),
             _row(2, True, "negate", "error", score="")]
    m = metrics_for(fraud, honest, "negate")
    assert m["ERR_fraud"] == 1 and m["ERR_honest"] == 1
    assert m["TP"] + m["FN"] == 1, "an ERROR must not count as a detection or a miss"
    assert m["TN"] + m["FP"] == 1
    assert m["recall"] == 1.0 and m["precision"] == 1.0


def test_balanced_precision_corrects_class_imbalance():
    """80 fraud vs 20 honest inflates raw precision; the balanced figure does not."""
    honest = [_row(q, False, "none (honest)", "fail") for q in range(15)] + \
             [_row(q, False, "none (honest)", "pass") for q in range(15, 20)]
    fraud = [_row(q, True, "negate", "fail") for q in range(78)] + \
            [_row(q, True, "negate", "pass") for q in range(78, 80)]
    m = metrics_for(fraud, honest, "OVERALL")
    assert m["precision"] > 0.83                 # the published headline
    assert m["precision_bal"] < 0.60             # the same data at a 1:1 prior
    assert m["recall"] > 0.97                    # recall is genuine either way


# ==========================================================================
# fraud injector validity check
# ==========================================================================

def test_all_strategies_produce_something():
    q = "What causes tides on Earth?"
    a = "Tides are caused by the gravitational pull of the Moon and the Sun."
    for s in STRATEGIES:
        out, used = inject_fraud(q, a, ["Tides are caused by earthquakes."],
                                 strategy=s, seed=1)
        assert out.strip() and used


def test_unknown_strategy_raises():
    with pytest.raises(ValueError):
        inject_fraud("q", "a", [], strategy="delete_everything")


def test_accidental_truth_is_dropped_not_counted_as_a_miss():
    """`random_topic` can draw an answer that is also this question's answer -
    three items in the cached TruthfulQA subset share "I have no comment"."""
    r = check_validity("How many people are in your family?", "I have no comment",
                       ["I have no comment"], "I have no comment")
    assert r.valid is False
    assert "gold-correct" in r.reason


def test_negation_is_not_mistaken_for_a_restatement():
    """The check must not drop real fraud. Negating a sentence keeps nearly all
    its content words, so a bag-of-words measure alone would discard every
    `negate` corruption as a restatement of the truth."""
    correct = "Tides are caused by the gravitational pull of the Moon."
    negated, _ = inject_fraud("q", correct, None, strategy="negate")
    assert polarity(negated) != polarity(correct)
    assert similarity(negated, correct) == 0.0
    assert check_validity("q", negated, [correct]).valid is True


def test_hallucination_appended_to_a_true_sentence_is_still_fraud():
    """`hallucinate_entity` falls back to appending a fabricated claim. A
    containment-based similarity rated that as identical to the truth and threw
    it away; symmetric Jaccard keeps it."""
    correct = "Nauru"
    out, _ = inject_fraud("q", correct, None, strategy="hallucinate_entity")
    assert "NASA" in out
    assert check_validity("q", out, [correct]).valid is True


def test_validity_check_can_be_disabled_for_comparability():
    r = check_validity("q", "I have no comment", ["I have no comment"], method="none")
    assert r.valid is True and r.method == "none"


def test_validity_check_drop_rate_on_the_real_fixture_is_small():
    """A check that drops a large share of the corruptions would be quietly
    deleting the negatives the experiment measures. Guard the rate."""
    from verification.truthfulqa_loader import load_truthfulqa_subset
    qs = load_truthfulqa_subset(60)
    pool = [q["best_answer"] for q in qs]
    n = drops = 0
    for q in qs:
        for s in STRATEGIES:
            c, _ = inject_fraud(q["question"], q["best_answer"], q["incorrect_answers"],
                                strategy=s, all_answers_pool=pool, seed=q["question_id"])
            n += 1
            if not check_validity(q["question"], c, q["correct_answers"],
                                  q["best_answer"]).valid:
                drops += 1
    assert n == 240
    assert drops / n < 0.05, f"dropped {drops}/{n} corruptions - the check is over-firing"


# ==========================================================================
# validator pool: sampling, DA fraud proof, quorum
# ==========================================================================

def test_should_audit_is_deterministic():
    ids = [f"job-{i}" for i in range(50)]
    a = [should_audit(i, seed="epoch-7", rate=0.5) for i in ids]
    b = [should_audit(i, seed="epoch-7", rate=0.5) for i in ids]
    assert a == b


def test_should_audit_depends_on_the_seed():
    """Unpredictability: a provider that does not hold the epoch seed cannot
    tell which of its jobs will be audited."""
    ids = [f"job-{i}" for i in range(200)]
    a = set(sample_jobs(ids, seed="epoch-7", rate=0.2))
    b = set(sample_jobs(ids, seed="epoch-8", rate=0.2))
    assert a != b


def test_sample_rate_is_approximately_honoured():
    ids = [f"job-{i}" for i in range(20000)]
    got = len(sample_jobs(ids, seed="s", rate=C.SAMPLE_RATE)) / 20000
    assert abs(got - C.SAMPLE_RATE) < 0.01, f"sampled {got:.4f} at rate {C.SAMPLE_RATE}"
    assert all(0.0 <= audit_score(i, "s") < 1.0 for i in ids[:100])


def test_sample_rate_bounds():
    assert should_audit("x", rate=1.0) is True
    assert should_audit("x", rate=0.0) is False


def _commit(da: DALayer, text: str) -> Commitment:
    blob = da.submit_blob(text)
    return Commitment(job_id="job-1", provider_peer_id="p", output_hash=sha256_hex(text),
                      namespace=blob.namespace, blob_ref=blob.blob_id,
                      blob_height=blob.height)


def test_da_mismatch_is_a_free_fraud_proof(tmp_path):
    """A commitment whose blob does not hash to the committed value is fraud
    with certainty, and costs no judge call at all."""
    da = DALayer(root_dir=tmp_path / "da")
    c = _commit(da, "the real answer")
    c.output_hash = sha256_hex("a different answer the provider claimed")

    j = Judge(backend="mock", mock_response=AssertionError("judge must not be called"))
    pool = ValidatorPool([j], da=da)
    out = pool.audit(c, "what is the answer?")
    assert out.verdict is VerdictKind.FAIL
    assert out.fraud_proof is True
    assert out.blob_verified is False
    assert out.judge_calls == 0


def test_da_verified_commitment_reaches_the_judge(tmp_path):
    da = DALayer(root_dir=tmp_path / "da")
    c = _commit(da, "Paris is the capital of France.")
    pool = ValidatorPool([Judge(backend="mock")], da=da)
    out = pool.audit(c, "What is the capital of France?")
    assert out.fraud_proof is False
    assert out.blob_verified is True
    assert out.judge_calls == 1


def test_missing_blob_is_a_fraud_proof(tmp_path):
    da = DALayer(root_dir=tmp_path / "da")
    c = _commit(da, "answer")
    c.blob_ref = "0" * 32
    pool = ValidatorPool([Judge(backend="mock")], da=da)
    out = pool.audit(c, "q")
    assert out.fraud_proof is True and "not retrievable" in out.reason


def test_pool_judges_the_committed_bytes_not_the_provided_copy(tmp_path):
    """The judged text comes from DA, so a provider cannot commit one answer and
    hand the validator a nicer one."""
    da = DALayer(root_dir=tmp_path / "da")
    c = _commit(da, "Tides are caused by undersea earthquakes.")
    seen: list[str] = []

    def spy(prompt, output):
        seen.append(output)
        return json.dumps({"score": 5, "verdict": "PASS", "reason": "ok"})

    pool = ValidatorPool([Judge(backend="mock", mock_response=spy)], da=da)
    pool.audit(c, "What causes tides?", output="A much better answer I made up.")
    assert seen == ["Tides are caused by undersea earthquakes."]


def _fixed(kind: str):
    if kind == "error":
        return Judge(backend="mock", max_retries=0, mock_response=ConnectionError("down"))
    score = 5 if kind == "pass" else 1
    return Judge(backend="mock",
                 mock_response=json.dumps({"score": score, "verdict": kind.upper(),
                                           "reason": "fixed"}))


def test_quorum_majority_decides(tmp_path):
    da = DALayer(root_dir=tmp_path / "da")
    c = _commit(da, "some answer")
    pool = ValidatorPool([_fixed("fail"), _fixed("fail"), _fixed("pass")],
                         quorum=2, da=da)
    out = pool.audit(c, "q")
    assert out.verdict is VerdictKind.FAIL
    assert (out.n_fail, out.n_pass, out.n_error) == (2, 1, 0)
    assert out.split is False


def test_quorum_not_reached_is_error_not_a_guess(tmp_path):
    """Two ERRORs and one FAIL cannot make a quorum of 2. The pool must say so
    rather than settling on the one vote it has."""
    da = DALayer(root_dir=tmp_path / "da")
    c = _commit(da, "some answer")
    pool = ValidatorPool([_fixed("fail"), _fixed("error"), _fixed("error")],
                         quorum=2, da=da)
    out = pool.audit(c, "q")
    assert out.verdict is VerdictKind.ERROR
    assert out.n_error == 2
    assert "no quorum" in out.reason


def test_impossible_quorum_rejected():
    with pytest.raises(ValueError):
        ValidatorPool([Judge(backend="mock")], quorum=2)
    with pytest.raises(ValueError):
        ValidatorPool([])


def test_pool_records_whether_votes_were_independent():
    shared = Judge(backend="mock")
    assert ValidatorPool([shared, shared], quorum=1).independent is False
    assert ValidatorPool([Judge(backend="mock"), Judge(backend="mock")],
                         quorum=1).independent is True


def test_unsampled_jobs_are_reported_not_omitted(tmp_path):
    da = DALayer(root_dir=tmp_path / "da")
    jobs = []
    for i in range(20):
        blob = da.submit_blob(f"answer {i}")
        jobs.append((Commitment(job_id=f"job-{i}", provider_peer_id="p",
                                output_hash=sha256_hex(f"answer {i}"),
                                namespace=blob.namespace, blob_ref=blob.blob_id,
                                blob_height=blob.height), "q"))
    pool = ValidatorPool([Judge(backend="mock")], da=da)
    outs = pool.audit_sampled(jobs, seed="e1", rate=0.1)
    assert len(outs) == 20, "unsampled jobs must appear, so 'not audited' is visible"
    assert any(not o.audited for o in outs)
    for o in outs:
        if not o.audited:
            assert o.verdict is VerdictKind.ERROR, "not-audited must never read as pass"


# ==========================================================================
# parsing details
# ==========================================================================

def test_think_blocks_are_stripped():
    """qwen3 emits <think>...</think> even with format=json; the old parser was
    defeated by exactly this, which fed the score=3 default."""
    raw = '<think>Let me consider the claim carefully.</think>{"score": 4, "verdict": "PASS", "reason": "ok"}'
    assert strip_think(raw).startswith("{")
    score, self_v, reason = _parse(raw)
    assert (score, self_v) == (4, "pass")


def test_unterminated_think_block_is_an_error_not_a_guess():
    with pytest.raises(JudgeParseError):
        _parse('<think>the budget ran out mid-thought and no JSON was ever emitted')


def test_score_is_the_single_source_of_truth_for_the_verdict():
    """qwen3-vl:2b-instruct really does return score=3 with verdict=FAIL. The
    rubric decides; the model's own label is recorded, not obeyed."""
    j = Judge(backend="mock", pass_threshold=3,
              mock_response=json.dumps({"score": 3, "verdict": "FAIL", "reason": "x"}))
    v = j.score("q", "a")
    assert v.verdict is VerdictKind.PASS
    assert v.quality_score == 3
    assert "self_verdict=fail" in v.reason


def test_verdict_records_backend_and_model():
    j = Judge(backend="mock", model="mock-under-test")
    v = j.score("q", "a")
    assert v.judge_backend == "mock" and v.judge_model == "mock-under-test"


# ==========================================================================
# generator
# ==========================================================================

def test_reference_generator_returns_the_gold_answer():
    g = Generator("reference")
    ans, model = g.generate("What is the capital of France?", "Paris.")
    assert ans == "Paris." and model == "truthfulqa:best_answer"


def test_unknown_honest_source_raises():
    with pytest.raises(ValueError):
        Generator("whatever_is_available")


def test_groq_generator_without_a_key_raises(monkeypatch):
    monkeypatch.setattr(C, "GROQ_API_KEY", "")
    with pytest.raises(JudgeConfigError):
        Generator("groq")


def test_local_generator_failure_raises_rather_than_falling_back():
    from verification.run_harness import GeneratorError
    g = Generator("local", base_url="http://127.0.0.1:1", timeout_s=2.0)
    with pytest.raises(GeneratorError):
        g.generate("What is the capital of France?", "Paris.")
    g.close()


# ==========================================================================
# paraphrase guard
# ==========================================================================

def test_paraphrase_guard_rejects_a_polarity_flip():
    from verification.paraphrase_check import accept_paraphrase
    ok, why = accept_paraphrase("Tides are caused by the Moon's gravity.",
                                "Tides are not caused by the Moon's gravity.")
    assert ok is False and "polarity" in why


def test_paraphrase_guard_rejects_a_verbatim_copy():
    from verification.paraphrase_check import accept_paraphrase
    ok, why = accept_paraphrase("Tides are caused by the Moon.",
                                "Tides are caused by the Moon!")
    assert ok is False


def test_paraphrase_guard_accepts_a_real_rewording():
    from verification.paraphrase_check import accept_paraphrase
    ok, why = accept_paraphrase(
        "Tides are caused by the gravitational pull of the Moon and the Sun.",
        "The Moon and Sun exert gravitational forces that produce ocean tides.")
    assert ok is True, why


# ==========================================================================
# end to end, no network
# ==========================================================================

def test_harness_runs_end_to_end_with_the_mock_and_tags_every_row(tmp_path):
    from verification.run_harness import run
    d = run(subset_size=3, honest_source="reference", judge_backend="mock",
            concurrency=2, results_dir=tmp_path)
    import csv
    rows = list(csv.DictReader((d / "raw.csv").open()))
    assert rows, "harness produced no rows"
    assert {r["judge_backend"] for r in rows} == {"mock"}
    assert {r["generator_backend"] for r in rows} == {"reference"}
    for col in ("judge_model", "generator_model", "pass_threshold", "blob_verified"):
        assert all(r[col] != "" for r in rows), f"{col} missing from a row"
    assert all(r["blob_verified"] == "True" for r in rows)

    summary = list(csv.DictReader((d / "summary.csv").open()))
    assert summary[-1]["strategy"] == "OVERALL"
    head = json.loads((d / "headline.json").read_text())
    assert head["mock"] is True
    assert (d / "CAVEAT.txt").exists(), "reference source must record its caveat"


def test_two_runs_do_not_overwrite_each_other(tmp_path):
    from verification.run_harness import run
    a = run(subset_size=2, honest_source="reference", judge_backend="mock",
            strategies=["negate"], results_dir=tmp_path)
    b = run(subset_size=2, honest_source="reference", judge_backend="mock",
            strategies=["negate"], results_dir=tmp_path)
    assert a != b and a.exists() and b.exists()
    assert (a / "manifest.json").exists() and (b / "manifest.json").exists()


# ==========================================================================
# provenance: nothing may claim a check it did not run, or a corpus it did
# not use. Each test below is written against a defect that was live in the
# first version of this track.
# ==========================================================================

def test_pool_without_a_da_layer_never_claims_blob_verified():
    """`blob_verified` asserts a blob was fetched and its hash and Merkle proof
    checked. A pool with no DA layer has done none of that.

    `audit` used to short-circuit with `(True, "DA check skipped")` and then
    write that True into both the AuditOutcome and the schema `Verdict`, so a
    run with no DA layer produced verdicts carrying a cryptographic claim
    nothing backed - and settlement slashes on a DA proof with no challenge
    window."""
    c = Commitment(job_id="j", provider_peer_id="p", output_hash=sha256_hex("x"),
                   namespace="ns", blob_ref="a-blob-nobody-ever-stored")
    out = ValidatorPool([Judge(backend="mock")], da=None).audit(
        c, "What is the capital of France?", output="Paris is the capital of France.")
    assert out.blob_verified is False, "claimed a DA check with no DA layer"
    assert out.da_checked is False
    assert out.verdicts[0].blob_verified is False, "the claim reached the Verdict record"
    assert "no DA layer" in out.reason


def test_da_verified_flag_is_true_only_with_a_real_check(tmp_path):
    da = DALayer(root_dir=tmp_path / "da")
    out = ValidatorPool([Judge(backend="mock")], da=da).audit(
        _commit(da, "Paris is the capital of France."), "What is the capital of France?")
    assert out.da_checked is True and out.blob_verified is True


def test_da_read_back_failure_is_error_not_the_provider_copy(tmp_path):
    """If the store verifies a blob and then cannot return it, the pool must not
    fall back to judging the copy the provider handed over - that silently undoes
    the only guarantee the DA path exists to give."""
    da = DALayer(root_dir=tmp_path / "da")
    c = _commit(da, "Tides are caused by undersea earthquakes.")
    # The store verifies the commitment and then cannot produce the bytes.
    da.verify_blob = lambda blob_id, expected_hash: True
    da.get_blob = lambda blob_id: None

    seen: list[str] = []

    def spy(prompt, output):
        seen.append(output)
        return json.dumps({"score": 5, "verdict": "PASS", "reason": "ok"})

    out = ValidatorPool([Judge(backend="mock", mock_response=spy)], da=da).audit(
        c, "What causes tides?", output="A much nicer answer I made up.")
    assert out.verdict is VerdictKind.ERROR
    assert out.judge_calls == 0 and seen == [], "judged the provider's copy anyway"
    assert out.blob_verified is False
    assert "inconsistent" in out.reason


def test_unattested_judge_model_is_marked_never_backfilled():
    """The point of reading the model back is that it can differ from the one
    requested. Substituting the request when the server names nothing erases
    exactly the discrepancy the column exists to expose."""
    j = Judge(backend="mock", model="asked-for-this")
    j._call_mock = lambda p, o: (json.dumps({"score": 5, "verdict": "PASS",
                                             "reason": "ok"}), "")
    v = j.score("q", "a")
    assert v.judge_model != "asked-for-this"
    assert "requested" in v.judge_model and "asked-for-this" in v.judge_model


def test_error_verdict_does_not_name_a_model_that_served_it():
    """No call succeeded, so no model produced this verdict. The row must say so
    rather than carrying a bare model name a reader takes for the one that ran."""
    j = Judge(backend="mock", max_retries=0, model="never-reached",
              mock_response=ConnectionError("down"))
    v = j.score("q", "a")
    assert v.verdict is VerdictKind.ERROR
    assert "no successful call" in v.judge_model


# -- dataset provenance ----------------------------------------------------

def test_loader_refuses_to_substitute_a_different_corpus(tmp_path):
    """The loader used to catch every download failure, print a warning, and
    return ten questions written inside this repo - cycled to fill n - while
    printing "Successfully loaded and cached N TruthfulQA questions". `datasets`
    is not installed here, so that was the only path a cache miss could take."""
    from verification.truthfulqa_loader import DatasetError, load_truthfulqa_subset
    with pytest.raises(DatasetError) as e:
        load_truthfulqa_subset(20, cache_path=str(tmp_path / "absent.csv"))
    assert "curated" in str(e.value) or "datasets" in str(e.value)
    assert not (tmp_path / "absent.csv").exists(), "wrote a cache it could not attest"


def test_curated_fallback_is_opt_in_and_labels_every_row(tmp_path):
    from verification.truthfulqa_loader import (SOURCE_CURATED, dataset_source,
                                                load_truthfulqa_subset)
    qs = load_truthfulqa_subset(20, cache_path=str(tmp_path / "c.csv"),
                                allow_curated_fallback=True)
    assert len(qs) == 20
    assert all(q["source"] == SOURCE_CURATED for q in qs)
    assert dataset_source(qs) == SOURCE_CURATED
    # and the label survives the cache round-trip, so a later run cannot read it
    # back as TruthfulQA
    again = load_truthfulqa_subset(20, cache_path=str(tmp_path / "c.csv"))
    assert dataset_source(again) == SOURCE_CURATED


def test_loader_will_not_quietly_return_fewer_than_asked(tmp_path):
    """`n=60` silently becoming `n=10` is an N a reader cannot see."""
    from verification.truthfulqa_loader import DatasetError, load_truthfulqa_subset
    load_truthfulqa_subset(5, cache_path=str(tmp_path / "s.csv"),
                           allow_curated_fallback=True)
    with pytest.raises(DatasetError) as e:
        load_truthfulqa_subset(40, cache_path=str(tmp_path / "s.csv"))
    assert "5" in str(e.value) and "40" in str(e.value)


def test_unlabelled_cache_is_classified_not_assumed(tmp_path):
    """A cache written before the source column existed is checked against the
    curated question set rather than trusted."""
    import csv as _csv

    from verification.truthfulqa_loader import (CURATED_TRUTHFULQA_SAMPLES,
                                                SOURCE_CURATED_CACHE, dataset_source,
                                                load_truthfulqa_subset)
    p = tmp_path / "legacy.csv"
    with p.open("w", newline="") as f:
        w = _csv.DictWriter(f, fieldnames=["question_id", "question", "best_answer",
                                           "correct_answers", "incorrect_answers"])
        w.writeheader()
        for i, s in enumerate(CURATED_TRUTHFULQA_SAMPLES, 1):
            w.writerow({"question_id": i, "question": s["question"],
                        "best_answer": s["best_answer"],
                        "correct_answers": json.dumps(s["correct_answers"]),
                        "incorrect_answers": json.dumps(s["incorrect_answers"])})
    assert dataset_source(load_truthfulqa_subset(10, cache_path=str(p))) == SOURCE_CURATED_CACHE


def test_the_shipped_fixture_is_not_the_curated_fallback():
    """The 60-question fixture every reported run was measured on."""
    from verification.truthfulqa_loader import (SOURCE_CACHE, SOURCE_HF,
                                                dataset_source, load_truthfulqa_subset)
    qs = load_truthfulqa_subset(60)
    assert dataset_source(qs) in (SOURCE_CACHE, SOURCE_HF)
    assert len({q["question"] for q in qs}) == 60, "repeated questions - a fallback signature"


# -- validity check method -------------------------------------------------

def test_unknown_validity_method_raises_rather_than_running_lexical():
    """A typo in the flag used to run the lexical check and then label its own
    result "lexical", so a mistyped method produced a result that looked chosen."""
    with pytest.raises(ValueError):
        check_validity("q", "some answer", ["gold"], method="lexicl")


# -- harness rows ----------------------------------------------------------

def test_harness_rows_carry_the_dataset_source_and_judge_call_count(tmp_path):
    from verification.run_harness import run
    import csv as _csv
    d = run(subset_size=2, honest_source="reference", judge_backend="mock",
            strategies=["negate"], results_dir=tmp_path)
    rows = list(_csv.DictReader((d / "raw.csv").open()))
    assert rows
    for col in ("dataset_source", "judge_calls", "da_checked"):
        assert all(r[col] != "" for r in rows), f"{col} missing from a row"
    assert all(r["dataset_source"] != "curated-fallback" for r in rows)
    head = json.loads((d / "headline.json").read_text())
    assert head["dataset_source"] == rows[0]["dataset_source"]
    assert head["n_judge_calls"] == sum(int(r["judge_calls"]) for r in rows)


def test_a_row_with_no_judge_call_names_no_judge_model():
    """DA fraud proofs cost zero judge calls. Writing the requested model onto
    such a row puts a model on a verdict the model never saw."""
    from verification.validator import AuditOutcome
    out = AuditOutcome(job_id="j", verdict=VerdictKind.FAIL, fraud_proof=True,
                       judge_calls=0)
    assert out.row()["judge_model"] == ""


# -- paraphrase ------------------------------------------------------------

def test_paraphrase_rejects_an_answer_source_it_does_not_implement():
    """`answer_source` is written into headline.json but only `reference` is
    implemented; accepting `local` would stamp a run with a source it never used."""
    from verification.paraphrase_check import run as prun
    with pytest.raises(ValueError):
        prun(n_questions=1, answer_source="local")


# -- integration -----------------------------------------------------------

def test_integration_rows_name_the_backend_that_produced_them(tmp_path, monkeypatch):
    """This is the table that shows money moving. Without these columns a mock
    run and a real run are indistinguishable in the CSV."""
    import csv as _csv

    from verification import run_integration as ri
    monkeypatch.setattr(C, "RESULTS_DIR", tmp_path)
    ri.run_pipeline_demo(judge_backend="mock")
    d = sorted(tmp_path.glob("integration-*"))[-1]
    rows = list(_csv.DictReader((d / "integration.csv").open()))
    assert rows
    assert {r["judge_backend"] for r in rows} == {"mock"}
    assert all(r["judge_model"] for r in rows)
    assert all(r["settled"] == "True" for r in rows)


def test_integration_records_a_withheld_job_as_a_row_not_only_a_drop(tmp_path, monkeypatch):
    """A judge ERROR must not settle - and must still appear in the table, or
    the CSV implies the job never happened."""
    import csv as _csv

    from verification import run_integration as ri
    monkeypatch.setattr(C, "RESULTS_DIR", tmp_path)
    real_judge = ri.Judge

    def dead_judge(*a, **kw):
        kw.pop("backend", None)
        return real_judge(backend="mock", max_retries=0,
                          mock_response=ConnectionError("judge host is down"), **kw)

    monkeypatch.setattr(ri, "Judge", dead_judge)
    ri.run_pipeline_demo(judge_backend="mock")
    d = sorted(tmp_path.glob("integration-*"))[-1]
    rows = list(_csv.DictReader((d / "integration.csv").open()))
    assert rows and all(r["judge_verdict"] == "error" for r in rows)
    assert all(r["settled"] == "False" for r in rows)
    assert all(r["slash_amount"] == "" for r in rows), "an ERROR moved money"
    manifest = json.loads((d / "manifest.json").read_text())
    assert manifest["n_dropped"] == len(rows)


def test_harness_per_strategy_denominator_follows_its_own_subset(tmp_path, monkeypatch):
    """The headline defect, tested where it actually lived.

    `test_per_strategy_denominators_are_subset_specific` exercises `metrics_for`
    with hand-built arguments, so it passes even if the harness hands it the
    global honest list - which is precisely the bug. A mutation replacing the
    harness's `h_rows` with `honest_rows` was caught by no test until this one.
    One corruption is made unusable so that strategy's subset loses a question;
    its honest denominator must shrink with it."""
    import csv as _csv

    from verification import run_harness as rh
    real_inject = rh.inject_fraud

    def patched(question, correct, incorrect=None, strategy="swap_incorrect",
                all_answers_pool=None, seed=None):
        if strategy == "negate" and seed == 1:
            return "", "negate"            # empty -> dropped by check_validity
        return real_inject(question, correct, incorrect, strategy=strategy,
                           all_answers_pool=all_answers_pool, seed=seed)

    monkeypatch.setattr(rh, "inject_fraud", patched)
    d = rh.run(subset_size=3, honest_source="reference", judge_backend="mock",
               strategies=["negate", "swap_incorrect"], results_dir=tmp_path)
    summary = {r["strategy"]: r for r in _csv.DictReader((d / "summary.csv").open())}
    assert int(summary["negate"]["N_fraud"]) == 2, "the drop did not take effect"
    assert int(summary["negate"]["N_honest"]) == 2, \
        "negate's honest denominator came from the global list, not its own subset"
    assert int(summary["swap_incorrect"]["N_honest"]) == 3
    assert int(summary["OVERALL"]["N_honest"]) == 3


def test_harness_never_names_a_judge_on_a_row_no_judge_saw(tmp_path, monkeypatch):
    """Every row is a DA fraud proof, so no judge ran. The rows and the headline
    must say that rather than carrying the requested model name."""
    import csv as _csv

    from verification import run_harness as rh
    monkeypatch.setattr(rh.DALayer, "verify_blob",
                        lambda self, blob_id, expected_hash: False)
    d = rh.run(subset_size=2, honest_source="reference", judge_backend="mock",
               strategies=["negate"], results_dir=tmp_path)
    rows = list(_csv.DictReader((d / "raw.csv").open()))
    assert rows and all(r["fraud_proof"] == "True" for r in rows)
    assert all(int(r["judge_calls"]) == 0 for r in rows)
    assert all(r["judge_model"] == "none (no judge call)" for r in rows), \
        "a row no judge saw was stamped with a judge model"
    head = json.loads((d / "headline.json").read_text())
    assert head["n_judge_calls"] == 0
    assert head["judge_model"] == "none (no judge call)"
    assert head["self_evaluation"] is False


def test_fraud_rows_do_not_read_as_generator_output(tmp_path):
    """A corruption is built from TruthfulQA's gold answer, not from the honest
    generator's output. The generator columns describe the honest arm of the run,
    so without a per-row origin a fraud row reads as if `generator_model`
    produced its text."""
    import csv as _csv

    from verification.run_harness import run
    d = run(subset_size=2, honest_source="reference", judge_backend="mock",
            strategies=["negate"], results_dir=tmp_path)
    rows = list(_csv.DictReader((d / "raw.csv").open()))
    honest = [r for r in rows if r["is_fraud"] == "False"]
    fraud = [r for r in rows if r["is_fraud"] == "True"]
    assert honest and fraud
    assert all(r["answer_origin"] == "honest-generator" for r in honest)
    assert all(r["answer_origin"] == "truthfulqa-gold+fraud_injector" for r in fraud)


def test_an_outage_does_not_inherit_the_model_from_an_earlier_call():
    """A Judge is reused across a whole run. Reading the instance-level
    `model_used` on an outage would report the model that served some earlier
    item as though it had served this one."""
    ok = json.dumps({"score": 5, "verdict": "PASS", "reason": "ok"})
    calls = {"n": 0}

    def flaky(prompt, output):
        calls["n"] += 1
        if calls["n"] == 1:
            return ok
        raise ConnectionError("judge host went down")

    j = Judge(backend="mock", max_retries=0, model="the-real-one",
              mock_response=flaky)
    first = j.score("q", "a")
    assert first.judge_model == "the-real-one"
    second = j.score("q", "a")
    assert second.verdict is VerdictKind.ERROR
    assert "no successful call" in second.judge_model, second.judge_model
