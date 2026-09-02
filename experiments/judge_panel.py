"""Experiment: does judge *diversity* buy what judge *size* does not?

Chapter 9 leaves a claim unmeasured - that "model diversity may matter more than
model size" for an LLM judge, because a bigger model of the same family may hold
the same misconceptions, only more confidently. The Experiment-3 baseline is
consistent with that story but cannot distinguish it: it used one judge,
`qwen3-vl:2b-instruct`, which caught 100% of `random_topic` and 95% of
`hallucinate_entity` but only 30% of `negate` and 35% of `swap_incorrect`, and
scored the frauds it missed at 3.80-4.05, above the pass threshold of 3. That is
a belief failure, not a hesitation, so repeating the same model would not fix it.

This runs the 2x2 that separates the two explanations, over the *same* TruthfulQA
subset, the *same* four corruption strategies and the same protocol, changing
only the judge:

    qwen3vl-2b   2.1B  qwen3vl   the baseline
    qwen3vl-8b   8.8B  qwen3vl   same family, 4x the parameters   -> is it size?
    llama3.2-3b  3.2B  llama     different family, similar size   -> is it diversity?
    panel        -     mixed     all three, under three quorum rules

Design decisions that matter for reading the numbers:

  * Every judge sees an identical item. The honest answers and the corruptions
    are generated once, cached, and reused by every configuration and every
    later invocation, so a difference between configurations cannot be a
    difference in the items. The item set is content-addressed by its
    parameters; changing the question count or the strategies makes a new one
    rather than silently mixing.

  * The three individual configurations are the panel's own member votes, not
    separate runs. Each judge is called exactly once per item, the panel
    deliberates over those three verdicts, and the individual rows are those
    same verdicts read back out. The panel's verdict is a pure function of the
    member votes, so this is exact rather than an approximation - and it makes
    the individual/panel comparison perfectly paired, which is what the kappa
    statistics need.

  * `qwen3-vl:latest` is a reasoning model and is judged with reasoning off
    (`think=False`). With it on, it spends the whole token budget deliberating
    and returns no answer at all - 0 usable verdicts, which measures nothing.
    That is a real difference in how this judge is operated and it is recorded
    in the run params; see `verification.panel.ThinkingAwareJudge`.

  * Recall is never reported alone. A judge that fails everything has perfect
    recall and is useless, so every recall figure here is printed beside that
    judge's false-positive rate on honest answers, and the headline comparison
    refuses to call a winner without both.

  * Resumable. A configuration whose votes are already cached is replayed rather
    than re-judged, and every replayed row is marked `replayed=True`, so a table
    can never present cached votes as a fresh measurement. `--force` re-judges.

    python -m experiments.judge_panel --questions 12
    python -m experiments.judge_panel --configs qwen3vl-2b,llama3.2-3b
    python -m experiments.judge_panel --figure-only
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import math
import sys
import threading
import time
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from edgegrid import config as C
from edgegrid.runlog import RunLog
from edgegrid.schemas import Verdict, VerdictKind

from verification.fraud_injector import STRATEGIES, check_validity, inject_fraud
from verification.panel import (QUORUM_RULES, JudgeMember, JudgePanel,
                                ThinkingAwareJudge, pairwise_agreement)
from verification.run_harness import Generator, GeneratorError, metrics_for
from verification.truthfulqa_loader import dataset_source, load_truthfulqa_subset

# The 2x2. `role` is what each seat is in the argument, written down so a result
# table states the hypothesis it is testing rather than leaving it to the reader.
JUDGE_SPECS: dict[str, dict] = {
    "qwen3vl-2b": {
        "backend": "ollama", "model": "qwen3-vl:2b-instruct",
        "family": "qwen3vl", "params_b": 2.1, "think": None,
        "role": "baseline - the judge Experiment 3 measured",
    },
    "qwen3vl-8b": {
        "backend": "ollama", "model": "qwen3-vl:latest",
        "family": "qwen3vl", "params_b": 8.8,
        # Reasoning on, this model never emits an answer inside the token
        # budget. See ThinkingAwareJudge.
        "think": False,
        "role": "same family, 4x parameters - tests the size explanation",
    },
    "llama3.2-3b": {
        "backend": "ollama", "model": "llama3.2:3b",
        "family": "llama", "params_b": 3.2, "think": None,
        "role": "different family, similar size - tests the diversity explanation",
    },
    # Hosted seats. These run on Groq's hardware, which is what makes the
    # comparison affordable: a 120B judge is not servable on a CPU-only edge
    # node, and the point of the arm is the model, not where it executes.
    # They also separate the two explanations far more cleanly than the local
    # models can, because at 20-27B we can hold scale roughly fixed and vary
    # only the family.
    "qwen-27b": {
        "backend": "groq", "model": "qwen/qwen3.8-27b",
        "family": "qwen", "params_b": 27.0, "think": None,
        "role": "same family as the baseline, 13x parameters - the size arm",
    },
    "gptoss-20b": {
        "backend": "groq", "model": "openai/gpt-oss-20b",
        "family": "gpt-oss", "params_b": 20.0, "think": None,
        "role": "different family at comparable scale to qwen-27b - the diversity arm",
    },
    "gptoss-120b": {
        "backend": "groq", "model": "openai/gpt-oss-120b",
        "family": "gpt-oss", "params_b": 120.0, "think": None,
        # Groq returns 400 "Failed to validate JSON" for this family on a large
        # fraction of calls when strict JSON is demanded, which cost roughly
        # half the judgements in the first hosted run. The parser recovers an
        # object from prose, so the constraint is dropped for this seat.
        "json_mode": False,
        "role": "different family, largest available - the ceiling",
    },
    # OpenRouter seats. Groq serves two families; the diversity arm needs more
    # than two lineages or "different family" is a two-point comparison rather
    # than a measurement. These were selected by probing every free model on a
    # known-hard item and keeping the ones that answered reliably; the rest were
    # rate-limited (429) or forbidden (403) on this account, which is recorded
    # here so the selection is not mistaken for a quality judgement.
    "nemotron-120b": {
        "backend": "openrouter", "model": "nvidia/nemotron-3-super-120b-a12b:free",
        "family": "nvidia-nemotron", "params_b": 120.0, "think": None,
        "json_mode": False, "timeout_s": 180.0, "max_retries": 4,
        "role": "third family at the top of the scale range",
    },
    "minimax-m3": {
        "backend": "openrouter", "model": "minimax/minimax-m3:free",
        "family": "minimax", "params_b": 0.0, "think": None,
        "json_mode": False, "timeout_s": 180.0, "max_retries": 4,
        "role": "fourth family - parameter count not published",
    },
    "ling-3-flash": {
        "backend": "openrouter", "model": "inclusionai/ling-3.0-flash-fin:free",
        "family": "inclusionai-ling", "params_b": 0.0, "think": None,
        "json_mode": False, "timeout_s": 180.0, "max_retries": 4,
        "role": "fifth family - parameter count not published",
    },
}

BASELINE = "qwen3vl-2b"
SIZE_ARM = "qwen-27b"
DIVERSITY_ARM = "gptoss-20b"

# The two strategies the baseline judge fails on. The whole comparison is about
# these; the other two are already at 95-100% and cannot separate anything.
HARD_STRATEGIES = ("negate", "swap_incorrect")

HONEST_STRATEGY = "none (honest)"


# --------------------------------------------------------------------------
# small statistics
# --------------------------------------------------------------------------

def wilson(k: int, n: int, z: float = 1.96) -> tuple[Optional[float], Optional[float]]:
    """95% Wilson score interval for a proportion.

    Reported on every recall figure because N here is 10-12 questions per
    strategy: at that size a 20-point difference between two judges is entirely
    ordinary sampling noise, and a table of bare point estimates invites the
    reader to believe otherwise."""
    if n <= 0:
        return None, None
    p = k / n
    d = 1.0 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - half) / d, (centre + half) / d


def _fmt_ci(lo, hi) -> str:
    """A Wilson lower bound of exactly 0.0 is a real bound (it is what k=0
    gives), so emptiness is tested explicitly rather than by truthiness."""
    if lo is None or lo == "" or hi is None or hi == "":
        return "n/a"
    return f"[{float(lo):.0%}, {float(hi):.0%}]"


# --------------------------------------------------------------------------
# the item set - built once, cached, shared by every configuration
# --------------------------------------------------------------------------

def items_key(questions: int, strategies: list[str], honest_source: str,
              generator_model: str, validity_check: str) -> str:
    payload = json.dumps({
        "questions": questions, "strategies": sorted(strategies),
        "honest_source": honest_source, "generator_model": generator_model,
        "validity_check": validity_check,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def build_items(questions: int, strategies: list[str], honest_source: str,
                generator_model: Optional[str], validity_check: str,
                log: RunLog) -> tuple[list[dict], list[dict], dict]:
    """(items, dropped, provenance). Honest generation plus fraud injection,
    exactly as `verification.run_harness` does it - imported, not reimplemented,
    so the only thing this experiment varies is the judge."""
    gen = Generator(honest_source, generator_model)
    dropped: list[dict] = []

    def drop(what: str, why: str) -> None:
        log.drop(what, why)
        dropped.append({"what": what, "why": why})
        print(f"  drop {what}: {why}")

    try:
        qs = load_truthfulqa_subset(n=questions)
        source = dataset_source(qs)
        pool_answers = [q["best_answer"] for q in qs]
        honest: dict[int, str] = {}
        t0 = time.monotonic()
        for q in qs:
            try:
                ans, _served = gen.generate(q["question"], q["best_answer"])
                honest[q["question_id"]] = ans
            except GeneratorError as e:
                drop(f"q{q['question_id']}:honest", str(e))
        print(f"[gen] {len(honest)}/{len(qs)} honest answers in "
              f"{time.monotonic() - t0:.1f}s via {gen.backend_label}/{gen.model_used}")

        items: list[dict] = []
        for q in qs:
            qid = q["question_id"]
            if qid in honest:
                items.append({
                    "item_key": f"{qid}:{HONEST_STRATEGY}",
                    "question_id": qid, "question": q["question"],
                    "answer": honest[qid], "is_fraud": False,
                    "fraud_strategy": HONEST_STRATEGY, "expected_verdict": "pass",
                    "validity_sim": "", "answer_origin": "honest-generator"})
            for strat in strategies:
                corrupted, used = inject_fraud(
                    q["question"], q["best_answer"], q["incorrect_answers"],
                    strategy=strat, all_answers_pool=pool_answers, seed=qid)
                v = check_validity(q["question"], corrupted, q["correct_answers"],
                                   q["best_answer"], method=validity_check)
                if not v.valid:
                    drop(f"q{qid}:{strat}", f"invalid corruption - {v.reason}")
                    continue
                items.append({
                    "item_key": f"{qid}:{used}",
                    "question_id": qid, "question": q["question"],
                    "answer": corrupted, "is_fraud": True,
                    "fraud_strategy": used, "expected_verdict": "fail",
                    "validity_sim": round(v.similarity, 3),
                    "answer_origin": "truthfulqa-gold+fraud_injector"})

        provenance = {
            "dataset_source": source,
            "generator_backend": gen.backend_label,
            "generator_model": gen.model_label(),
            "honest_source": honest_source,
            "n_questions_loaded": len(qs),
            "n_items": len(items),
            "dropped": dropped,
        }
        return items, dropped, provenance
    finally:
        gen.close()


def load_or_build_items(cache: Path, key: str, **kw) -> tuple[list[dict], dict, bool]:
    """Items are content-addressed and written once. Reusing them is what makes
    the four configurations comparable at all, so a cache hit is a feature here
    rather than an optimisation - and it is announced, never silent."""
    f = cache / "items.json"
    if f.exists():
        payload = json.loads(f.read_text())
        return payload["items"], payload["provenance"], True
    items, _dropped, provenance = build_items(**kw)
    cache.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"key": key, "items": items,
                             "provenance": provenance}, indent=2))
    return items, provenance, False


# --------------------------------------------------------------------------
# vote cache
# --------------------------------------------------------------------------

def votes_path(cache: Path, label: str, threshold: int) -> Path:
    return cache / f"votes-{label}-t{threshold}.json"


def load_votes(cache: Path, label: str, threshold: int) -> dict[str, Verdict]:
    f = votes_path(cache, label, threshold)
    if not f.exists():
        return {}
    raw = json.loads(f.read_text())
    return {k: Verdict.model_validate(v) for k, v in raw.items()}


def save_votes(cache: Path, label: str, threshold: int,
               votes: dict[str, Verdict]) -> None:
    cache.mkdir(parents=True, exist_ok=True)
    tmp = votes_path(cache, label, threshold).with_suffix(".json.tmp")
    tmp.write_text(json.dumps({k: json.loads(v.model_dump_json())
                               for k, v in votes.items()}, indent=2))
    tmp.replace(votes_path(cache, label, threshold))


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------

def _row(item: dict, config: str, kind: str, spec: dict, verdict: VerdictKind,
         score, judge_backend: str, judge_model: str, reason: str,
         replayed: bool, judge_calls: int, latency_ms: float,
         n_pass="", n_fail="", n_error="", disagreement="") -> dict:
    return {
        "config": config,
        "config_kind": kind,
        "family": spec.get("family", ""),
        "params_b": spec.get("params_b", ""),
        "question_id": item["question_id"],
        "question": item["question"],
        "answer": item["answer"],
        "is_fraud": item["is_fraud"],
        "fraud_strategy": item["fraud_strategy"],
        "answer_origin": item["answer_origin"],
        "validity_sim": item["validity_sim"],
        "expected_verdict": item["expected_verdict"],
        "verdict": verdict.value,
        "score": "" if score is None else score,
        "correct": (verdict.value == item["expected_verdict"]
                    if verdict is not VerdictKind.ERROR else ""),
        "judge_backend": judge_backend,
        "judge_model": judge_model,
        "judge_calls": judge_calls,
        "replayed": replayed,
        "n_pass": n_pass, "n_fail": n_fail, "n_error": n_error,
        "disagreement": disagreement,
        "latency_ms": round(latency_ms, 1),
        "reason": (reason or "")[:300],
    }


def config_metrics(rows: list[dict], config: str, kind: str) -> list[dict]:
    """Per-strategy and overall metrics for one configuration, using
    `run_harness.metrics_for` so this experiment and Experiment 3 compute
    precision, recall and the balanced correction identically."""
    honest = [r for r in rows if not r["is_fraud"]]
    out: list[dict] = []
    for strat in sorted({r["fraud_strategy"] for r in rows if r["is_fraud"]}):
        f_rows = [r for r in rows if r["fraud_strategy"] == strat]
        qids = {r["question_id"] for r in f_rows}
        h_rows = [r for r in honest if r["question_id"] in qids]
        m = metrics_for(f_rows, h_rows, strat)
        lo, hi = wilson(m["TP"], m["TP"] + m["FN"])
        m.update({"config": config, "config_kind": kind,
                  "recall_ci_lo": "" if lo is None else round(lo, 4),
                  "recall_ci_hi": "" if hi is None else round(hi, 4)})
        out.append(m)
    m = metrics_for([r for r in rows if r["is_fraud"]], honest, "OVERALL")
    lo, hi = wilson(m["TP"], m["TP"] + m["FN"])
    m.update({"config": config, "config_kind": kind,
              "recall_ci_lo": "" if lo is None else round(lo, 4),
              "recall_ci_hi": "" if hi is None else round(hi, 4)})
    out.append(m)
    # HARD = the two strategies the baseline fails; the comparison lives here.
    hard_f = [r for r in rows if r["fraud_strategy"] in HARD_STRATEGIES]
    if hard_f:
        qids = {r["question_id"] for r in hard_f}
        m = metrics_for(hard_f, [r for r in honest if r["question_id"] in qids],
                        "HARD(negate+swap)")
        lo, hi = wilson(m["TP"], m["TP"] + m["FN"])
        m.update({"config": config, "config_kind": kind,
                  "recall_ci_lo": "" if lo is None else round(lo, 4),
                  "recall_ci_hi": "" if hi is None else round(hi, 4)})
        out.append(m)
    return out


def run(questions: int = 12, configs: Optional[list[str]] = None,
        panel_rules: Optional[list[str]] = None,
        strategies: Optional[list[str]] = None,
        honest_source: str = "local", generator_model: Optional[str] = None,
        validity_check: str = "lexical",
        pass_threshold: int = C.PASS_THRESHOLD, concurrency: int = 2,
        force: bool = False, cache_root: Optional[Path] = None,
        results_dir: Optional[Path] = None, panel_concurrent: bool = True,
        figures_dir: Optional[Path] = None) -> Path:
    strategies = strategies or list(STRATEGIES)
    labels = list(configs or JUDGE_SPECS)
    unknown = [x for x in labels if x not in JUDGE_SPECS]
    if unknown:
        raise ValueError(f"unknown judge config(s) {unknown}; "
                         f"choose from {list(JUDGE_SPECS)}")
    rules = list(panel_rules if panel_rules is not None else QUORUM_RULES)
    bad = [r for r in rules if r not in QUORUM_RULES]
    if bad:
        raise ValueError(f"unknown quorum rule(s) {bad}; choose from {list(QUORUM_RULES)}")
    if len(labels) < 2 and rules:
        # A one-member "panel" would be the individual judge under another name,
        # and printing it as a panel row would manufacture a comparison.
        print(f"!! only {len(labels)} judge configuration(s) requested; "
              "panel rows need at least 2 members and will be skipped")
        rules = []

    gen_model = generator_model or C.OLLAMA_MODEL
    key = items_key(questions, strategies, honest_source, gen_model, validity_check)
    cache = (Path(cache_root) if cache_root else C.RESULTS_DIR / "judge-panel-cache") / key

    params = {
        "questions": questions, "strategies": strategies,
        "judge_configs": labels, "panel_rules": rules,
        "judge_specs": {k: JUDGE_SPECS[k] for k in labels},
        "honest_source": honest_source, "generator_model": gen_model,
        "validity_check": validity_check, "pass_threshold": pass_threshold,
        "concurrency": concurrency, "panel_concurrent": panel_concurrent,
        "force": force, "items_key": key, "cache_dir": str(cache),
        "individual_configs_are_panel_member_votes": True,
        "note": ("each judge is called once per item; the individual rows are the "
                 "panel's own member votes and the panel verdict is a pure "
                 "function of them, so the comparison is exactly paired"),
    }

    with RunLog("judge-panel", params, results_dir=results_dir) as log:
        items, provenance, items_cached = load_or_build_items(
            cache, key, questions=questions, strategies=strategies,
            honest_source=honest_source, generator_model=generator_model,
            validity_check=validity_check, log=log)
        log.note(f"items {'reused from' if items_cached else 'built and written to'} "
                 f"{cache}")
        log.write_json("items_provenance", dict(provenance, items_key=key,
                                                items_from_cache=items_cached))

        members = [JudgeMember(
            label=lab,
            judge=ThinkingAwareJudge(backend=JUDGE_SPECS[lab]["backend"],
                                     model=JUDGE_SPECS[lab]["model"],
                                     pass_threshold=pass_threshold,
                                     # Strict server-side JSON is a per-model
                                     # capability, not a global setting: the
                                     # gpt-oss family returns 400 on a large
                                     # fraction of calls when it is demanded.
                                     json_mode=JUDGE_SPECS[lab].get("json_mode", True),
                                     timeout_s=JUDGE_SPECS[lab].get("timeout_s", 120.0),
                                     max_retries=JUDGE_SPECS[lab].get("max_retries", 2),
                                     think=JUDGE_SPECS[lab]["think"]),
            family=JUDGE_SPECS[lab]["family"],
            params_b=JUDGE_SPECS[lab]["params_b"]) for lab in labels]
        panel = JudgePanel(members, rule=rules[0] if rules else "majority",
                           concurrent=panel_concurrent)
        by_label = {m.label: m for m in members}

        print("=" * 88)
        print("EDGE GRID - DIVERSE JUDGE PANEL")
        print(f"  run        : {log.run_id}")
        print(f"  items      : {len(items)} "
              f"({'cached' if items_cached else 'newly generated'}, key {key})")
        print(f"  dataset    : {provenance['dataset_source']}")
        print(f"  generator  : {provenance['generator_backend']}/"
              f"{provenance['generator_model']}  (honest-source={honest_source})")
        for m in members:
            s = JUDGE_SPECS[m.label]
            print(f"  judge      : {m.label:<12} {s['model']:<22} "
                  f"{s['family']:<8} {s['params_b']}B  think={s['think']}  - {s['role']}")
        print(f"  panel      : rules={rules or 'none'}  diverse={panel.diverse} "
              f"families={sorted(set(panel.families))}")
        print(f"  threshold  : score >= {pass_threshold} is PASS")
        print("=" * 88)

        # -- votes: cached where possible, judged where not -------------------
        votes: dict[str, dict[str, Verdict]] = {}
        for lab in labels:
            cached = {} if force else load_votes(cache, lab, pass_threshold)
            keep = {k: v for k, v in cached.items()
                    if k in {i["item_key"] for i in items}}
            votes[lab] = keep
            missing = len(items) - len(keep)
            state = "FORCED re-judge" if force else (
                "complete, will be replayed" if missing == 0 else
                f"partial, {missing} item(s) to judge")
            print(f"  cache {lab:<12} {len(keep):>3}/{len(items)} votes - {state}")

        lock = threading.Lock()
        # Per (item, judge), not per item: an item where one member was cached
        # and two were judged is fresh for those two and replayed for the first,
        # and a single per-item flag would mislabel two thirds of its rows.
        cached_at_start = {l: set(votes[l]) for l in labels}
        pending = [it for it in items
                   if any(it["item_key"] not in votes[l] for l in labels)]
        replayed_items = len(items) - len(pending)
        print(f"\n[judge] {len(pending)} item(s) need at least one judge call, "
              f"{replayed_items} fully replayed from cache; per-judge coverage is "
              f"the `cache` lines above; item concurrency={concurrency}")
        done = [0]
        t0 = time.monotonic()

        def judge_item(it: dict) -> None:
            need = [l for l in labels if it["item_key"] not in votes[l]]
            if need:
                live = JudgePanel([by_label[l] for l in need], rule="majority",
                                  concurrent=panel_concurrent)
                outcome = live.deliberate(it["question"], it["answer"],
                                          job_id=it["item_key"])
                with lock:
                    for lab, v in zip(need, outcome.verdicts):
                        votes[lab][it["item_key"]] = v
                    for lab in need:
                        save_votes(cache, lab, pass_threshold, votes[lab])
            with lock:
                done[0] += 1
                marks = "".join({"pass": "P", "fail": "F", "error": "E"}[
                    votes[l][it["item_key"]].verdict.value] for l in labels)
                print(f"  [{done[0]:>3}/{len(items)}] q{it['question_id']:<3} "
                      f"{it['fraud_strategy']:<26} {marks:<6} "
                      f"{'judged' if need else 'replayed'}")

        if concurrency > 1 and pending:
            with cf.ThreadPoolExecutor(max_workers=concurrency) as ex:
                list(ex.map(judge_item, items))
        else:
            for it in items:
                judge_item(it)
        judged_s = time.monotonic() - t0
        print(f"[judge] done in {judged_s:.1f}s")

        # A judge that errored on every item measured nothing; say so loudly
        # rather than letting a 0%-recall row read as a finding.
        for lab in labels:
            n_err = sum(1 for v in votes[lab].values()
                        if v.verdict is VerdictKind.ERROR)
            if n_err:
                msg = (f"{lab}: {n_err}/{len(items)} member verdicts are ERROR "
                       "(excluded from every rate)")
                print(f"  !! {msg}")
                log.note(msg)

        # -- rows ------------------------------------------------------------
        all_rows: list[dict] = []
        vote_rows: list[dict] = []
        for it in items:
            member_verdicts = [votes[l][it["item_key"]] for l in labels]
            for lab, v in zip(labels, member_verdicts):
                replayed = it["item_key"] in cached_at_start[lab]
                all_rows.append(_row(
                    it, lab, "judge", JUDGE_SPECS[lab], v.verdict, v.judge_score,
                    v.judge_backend, v.judge_model, v.reason, replayed,
                    0 if replayed else 1, v.latency_ms))
                vote_rows.append({
                    "item_key": it["item_key"], "question_id": it["question_id"],
                    "fraud_strategy": it["fraud_strategy"],
                    "expected_verdict": it["expected_verdict"],
                    "judge_label": lab, "family": JUDGE_SPECS[lab]["family"],
                    "params_b": JUDGE_SPECS[lab]["params_b"],
                    "verdict": v.verdict.value,
                    "score": "" if v.judge_score is None else v.judge_score,
                    "judge_model": v.judge_model,
                    "latency_ms": round(v.latency_ms, 1),
                    "reason": (v.reason or "")[:300]})
            for rule in rules:
                o = panel.aggregate(member_verdicts, rule)
                all_rows.append(_row(
                    it, f"panel-{rule}", "panel",
                    {"family": "+".join(sorted(set(panel.families))), "params_b": ""},
                    o.verdict, o.mean_score, o.panel_backend, o.panel_model,
                    o.reason, True, 0, o.latency_ms,
                    n_pass=o.n_pass, n_fail=o.n_fail, n_error=o.n_error,
                    disagreement=o.disagreement))

        for r in all_rows:
            log.append("raw", r)
        log.write_table("panel_votes", vote_rows)

        # -- metrics ----------------------------------------------------------
        summary: list[dict] = []
        config_order = labels + [f"panel-{r}" for r in rules]
        for cfg in config_order:
            rows = [r for r in all_rows if r["config"] == cfg]
            kind = "panel" if cfg.startswith("panel-") else "judge"
            summary.extend(config_metrics(rows, cfg, kind))
        log.write_table("summary", summary)

        agreement = pairwise_agreement(
            {l: {k: v.verdict.value for k, v in votes[l].items()} for l in labels})
        log.write_table("agreement", agreement)

        # -- the hypothesis ----------------------------------------------------
        def hard(cfg: str) -> Optional[dict]:
            for m in summary:
                if m["config"] == cfg and m["strategy"] == "HARD(negate+swap)":
                    return m
            return None

        def overall(cfg: str) -> Optional[dict]:
            for m in summary:
                if m["config"] == cfg and m["strategy"] == "OVERALL":
                    return m
            return None

        verdict_text: list[str] = []
        hyp: dict = {"hypothesis": ("model diversity matters more than model size "
                                    "for an LLM judge (Chapter 9, future work)")}
        b, s, d = hard(BASELINE), hard(SIZE_ARM), hard(DIVERSITY_ARM)
        if not (b and s and d):
            missing = sorted({n for n, m in ((BASELINE, b), (SIZE_ARM, s),
                                             (DIVERSITY_ARM, d)) if not m})
            hyp["result"] = "not computed"
            hyp["why"] = f"configuration(s) {missing} were not part of this run"
            verdict_text.append(
                f"HYPOTHESIS NOT TESTED: {missing} missing from this run.")
        else:
            d_size = s["recall"] - b["recall"]
            d_div = d["recall"] - b["recall"]
            ob, os_, od = overall(BASELINE), overall(SIZE_ARM), overall(DIVERSITY_ARM)
            hyp.update({
                "hard_strategies": list(HARD_STRATEGIES),
                "baseline": {"config": BASELINE, "hard_recall": b["recall"],
                             "hard_recall_ci": [b["recall_ci_lo"], b["recall_ci_hi"]],
                             "fpr_honest": ob["fpr"], "f1_bal": ob["f1_bal"]},
                "size_arm": {"config": SIZE_ARM, "hard_recall": s["recall"],
                             "hard_recall_ci": [s["recall_ci_lo"], s["recall_ci_hi"]],
                             "fpr_honest": os_["fpr"], "f1_bal": os_["f1_bal"],
                             "delta_recall_vs_baseline": round(d_size, 4)},
                "diversity_arm": {"config": DIVERSITY_ARM, "hard_recall": d["recall"],
                                  "hard_recall_ci": [d["recall_ci_lo"], d["recall_ci_hi"]],
                                  "fpr_honest": od["fpr"], "f1_bal": od["f1_bal"],
                                  "delta_recall_vs_baseline": round(d_div, 4)},
            })
            # Point estimates decide the direction; the intervals decide whether
            # that direction is worth anything at this N. Both are reported and
            # neither is allowed to stand alone.
            separated = (s["recall_ci_hi"] != "" and d["recall_ci_lo"] != ""
                         and (d["recall_ci_lo"] > s["recall_ci_hi"]
                              or s["recall_ci_lo"] > d["recall_ci_hi"]))
            if d_div > d_size:
                res = "supports"
                claim = (f"the different-family judge ({DIVERSITY_ARM}) gains "
                         f"{d_div:+.0%} on the strategies the baseline fails, "
                         f"the same-family 4x-larger judge ({SIZE_ARM}) gains "
                         f"{d_size:+.0%}")
            elif d_size > d_div:
                res = "refutes"
                claim = (f"the same-family 4x-larger judge ({SIZE_ARM}) gains "
                         f"{d_size:+.0%} on the strategies the baseline fails, "
                         f"more than the different-family judge "
                         f"({DIVERSITY_ARM}) at {d_div:+.0%}: on this data size "
                         f"buys more than diversity")
            else:
                res = "inconclusive"
                claim = (f"both arms move hard-strategy recall by the same "
                         f"{d_size:+.0%}; this design cannot separate them")
            hyp["result"] = res
            hyp["claim"] = claim
            hyp["ci_separated"] = bool(separated)
            hyp["n_hard_fraud_items"] = b["N_fraud"]
            hyp["caveat"] = (
                "" if separated else
                f"the 95% Wilson intervals on the two arms overlap at N="
                f"{b['N_fraud']} hard-fraud items, so the direction is not "
                f"statistically separated - this is a point estimate, not a "
                f"demonstrated effect")
            verdict_text.append(f"HYPOTHESIS {res.upper()}: {claim}.")
            if not separated:
                verdict_text.append(f"  CAVEAT: {hyp['caveat']}.")
            for nm, m, om in ((BASELINE, b, ob), (SIZE_ARM, s, os_),
                              (DIVERSITY_ARM, d, od)):
                verdict_text.append(
                    f"  {nm:<12} hard recall {m['recall']:>6.0%} "
                    f"{_fmt_ci(m['recall_ci_lo'], m['recall_ci_hi']):<16} "
                    f"honest FPR {om['fpr']:>6.0%}   balanced F1 {om['f1_bal']:.3f}")

        # Does any quorum beat every individual judge?
        if rules:
            best_ind = max((overall(l) for l in labels),
                           key=lambda m: m["f1_bal"])
            best_pan = max((overall(f"panel-{r}") for r in rules),
                           key=lambda m: m["f1_bal"])
            hyp["best_individual"] = {"config": best_ind["config"],
                                      "f1_bal": best_ind["f1_bal"],
                                      "recall": best_ind["recall"],
                                      "fpr": best_ind["fpr"]}
            hyp["best_panel"] = {"config": best_pan["config"],
                                 "f1_bal": best_pan["f1_bal"],
                                 "recall": best_pan["recall"],
                                 "fpr": best_pan["fpr"],
                                 "error_rate": best_pan["error_rate"]}
            hyp["panel_beats_every_individual"] = bool(
                best_pan["f1_bal"] > best_ind["f1_bal"])
            verdict_text.append(
                f"  best individual {best_ind['config']} F1bal={best_ind['f1_bal']:.3f}"
                f"   best panel {best_pan['config']} F1bal={best_pan['f1_bal']:.3f}"
                f"  -> panel {'beats' if hyp['panel_beats_every_individual'] else 'does not beat'}"
                " every individual judge")

        n_dis, n_comparable = 0, 0
        for it in items:
            kinds = [votes[l][it["item_key"]].verdict for l in labels]
            voting = [k for k in kinds if k is not VerdictKind.ERROR]
            if len(voting) > 1:
                n_comparable += 1
                if len(set(voting)) > 1:
                    n_dis += 1
        headline = {
            "run_id": log.run_id,
            "items_key": key,
            "n_items": len(items),
            "n_judge_calls": sum(r["judge_calls"] for r in all_rows),
            "n_items_replayed_from_cache": replayed_items,
            "judge_seconds": round(judged_s, 1),
            "configs": config_order,
            "pass_threshold": pass_threshold,
            "panel_diverse": panel.diverse,
            "panel_families": sorted(set(panel.families)),
            "n_items_members_disagreed": n_dis,
            "n_items_comparable": n_comparable,
            "disagreement_rate": (round(n_dis / n_comparable, 4)
                                  if n_comparable else ""),
            "agreement": agreement,
            "hypothesis": hyp,
            **{f"items_{k}": v for k, v in provenance.items() if k != "dropped"},
            "items_from_cache": items_cached,
            "n_items_dropped_invalid": len(provenance["dropped"]),
            "dropped": provenance["dropped"],
        }
        log.write_json("headline", headline)

        _print_summary(summary, config_order)
        _print_agreement(agreement, n_dis, n_comparable)
        print("\n" + "=" * 88)
        for line in verdict_text:
            print(line)
        print("=" * 88)

        fig_dir = Path(figures_dir) if figures_dir else C.FIGURES_DIR
        try:
            p = make_figure(log.dir, fig_dir)
            print(f"\nfigure : {p}")
        except Exception as e:
            # A failed plot must not destroy a 40-minute measurement, but it also
            # must not pass silently as though the figure exists.
            log.note(f"figure failed: {type(e).__name__}: {e}")
            print(f"\n!! figure FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        print(f"results: {log.dir}")
        panel.close()
        return log.dir


def _print_summary(summary: list[dict], order: list[str]) -> None:
    cols = [("config", 16), ("strategy", 20), ("N_f", 4), ("N_h", 4), ("TP", 3),
            ("FP", 3), ("TN", 3), ("FN", 3), ("ERR", 4), ("prec", 7),
            ("prec_bal", 8), ("recall", 7), ("recall 95% CI", 16), ("fpr", 6),
            ("f1_bal", 7), ("hon_mu", 7), ("frd_mu", 7)]
    line = "  ".join(f"{h:<{w}}" for h, w in cols)
    print("\n" + line)
    print("-" * len(line))
    for cfg in order:
        for r in [x for x in summary if x["config"] == cfg]:
            vals = [r["config"][:16], r["strategy"][:20], r["N_fraud"], r["N_honest"],
                    r["TP"], r["FP"], r["TN"], r["FN"],
                    r["ERR_fraud"] + r["ERR_honest"],
                    f"{r['precision']:.0%}", f"{r['precision_bal']:.0%}",
                    f"{r['recall']:.0%}",
                    _fmt_ci(r["recall_ci_lo"], r["recall_ci_hi"]),
                    f"{r['fpr']:.0%}", f"{r['f1_bal']:.3f}",
                    r["mean_score_honest"], r["mean_score_fraud"]]
            print("  ".join(f"{str(v):<{w}}" for v, (_h, w) in zip(vals, cols)))
        print("-" * len(line))


def _print_agreement(agreement: list[dict], n_dis: int, n_items: int) -> None:
    print("\ninter-judge agreement (pass/fail, ERROR rows excluded)")
    print(f"{'judge A':<14} {'judge B':<14} {'n':>4} {'raw':>7} {'kappa':>8}  note")
    for a in agreement:
        print(f"{a['judge_a']:<14} {a['judge_b']:<14} {a['n_compared']:>4} "
              f"{a['raw_agreement']:>7.2%} {str(a['cohens_kappa']):>8}  "
              f"{a['kappa_note']}")
    if n_items:
        print(f"\nitems where the members disagreed: {n_dis}/{n_items} "
              f"({n_dis / n_items:.1%}) - items with >1 non-ERROR vote")


# --------------------------------------------------------------------------
# figure
# --------------------------------------------------------------------------

def make_figure(run_dir: Path, out_dir: Path) -> str:
    """Recall per corruption strategy, grouped by judge configuration.

    Two stacked axes rather than one: the validated palette holds three
    categorical colours, and six series on one axis would force a fourth colour
    and a repeated hatch. Individual judges above, quorum rules below, sharing
    one x-axis of strategies so the `negate` column reads straight down. Each
    configuration's false-positive rate on honest answers is printed in the
    legend, because a recall bar on its own can be won by failing everything."""
    import matplotlib.pyplot as plt
    import numpy as np
    import pandas as pd

    from experiments import style
    from experiments.style import CATEGORICAL, HATCH, INK_2

    df = pd.read_csv(run_dir / "summary.csv")
    head = json.loads((run_dir / "headline.json").read_text())
    strategies = [s for s in df["strategy"].unique()
                  if s not in ("OVERALL", "HARD(negate+swap)")]
    # negate and swap_incorrect first: they are the columns that carry the result.
    strategies = sorted(strategies, key=lambda s: (s not in HARD_STRATEGIES, s))
    judges = [c for c in head["configs"] if not c.startswith("panel-")]
    panels = [c for c in head["configs"] if c.startswith("panel-")]
    if not strategies or not judges:
        raise ValueError(f"{run_dir.name}: nothing to plot "
                         f"(strategies={strategies}, judges={judges})")

    style.apply()
    groups = [("Individual judges", judges), ("Quorum rules over all three", panels)]
    groups = [g for g in groups if g[1]]
    fig, axes = plt.subplots(len(groups), 1, figsize=(8.0, 3.1 * len(groups) + 1.0),
                             sharex=True)
    # The shared style puts titles 26pt clear of the axes and legends above that
    # again, so stacked axes need the gap opened by hand or the lower title lands
    # on the upper axis's baseline.
    fig.subplots_adjust(hspace=0.62)
    axes = np.atleast_1d(axes)
    x = np.arange(len(strategies))

    for ax, (title, cfgs) in zip(axes, groups):
        w = 0.8 / len(cfgs)
        for i, cfg in enumerate(cfgs):
            sub = df[df["config"] == cfg].set_index("strategy")
            vals = [float(sub.loc[s, "recall"]) if s in sub.index else np.nan
                    for s in strategies]
            fpr = float(sub.loc["OVERALL", "fpr"]) if "OVERALL" in sub.index else float("nan")
            off = (i - (len(cfgs) - 1) / 2) * w
            ax.bar(x + off, vals, width=w * 0.9, color=CATEGORICAL[i % len(CATEGORICAL)],
                   hatch=HATCH[i % len(HATCH)], edgecolor="white", linewidth=1.0,
                   label=f"{cfg}  (honest FPR {fpr:.0%})", zorder=2)
            for xx, v in zip(x + off, vals):
                if not np.isnan(v):
                    ax.annotate(f"{v:.0%}", xy=(xx, v), xytext=(0, 3),
                                textcoords="offset points", ha="center",
                                fontsize=7.6, color=INK_2)
        ax.set_ylim(0, 1.16)
        ax.set_yticks([0, .25, .5, .75, 1.0])
        ax.set_yticklabels(["0%", "25%", "50%", "75%", "100%"])
        ax.set_ylabel("Recall (frauds caught)")
        ax.set_title(title)
        ax.grid(axis="x", visible=False)
        style.legend_top(ax, ncol=min(3, len(cfgs)))

    axes[-1].set_xticks(x)
    axes[-1].set_xticklabels(strategies)
    for ax in axes:
        for j, s in enumerate(strategies):
            if s in HARD_STRATEGIES:
                ax.axvspan(j - 0.5, j + 0.5, color=INK_2, alpha=0.055, zorder=0)
    hyp = head.get("hypothesis", {})
    n_hard = hyp.get("n_hard_fraud_items", "?")
    style.caption(
        axes[-1],
        f"{head['n_items']} items, {head['n_judge_calls']} judge calls - shaded "
        f"columns (negate, swap_incorrect) are where the 2.1B baseline fails; "
        f"N={n_hard} fraud items there, so 95% CIs are wide - run {run_dir.name}",
        y=-0.30)
    out_dir.mkdir(parents=True, exist_ok=True)
    return style.finish(fig, out_dir / "fig_judge_panel.png")


# --------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Diverse judge panel experiment")
    p.add_argument("--questions", type=int, default=12)
    p.add_argument("--configs", type=str, default=",".join(JUDGE_SPECS),
                   help=f"judge configurations to run: {','.join(JUDGE_SPECS)}")
    p.add_argument("--panel-rules", type=str, default=",".join(QUORUM_RULES))
    p.add_argument("--strategies", type=str, default=",".join(STRATEGIES))
    p.add_argument("--honest-source", choices=("reference", "local", "groq"),
                   default="local")
    p.add_argument("--generator-model", type=str, default=None)
    p.add_argument("--validity-check", choices=("none", "lexical"), default="lexical")
    p.add_argument("--pass-threshold", type=int, default=C.PASS_THRESHOLD)
    p.add_argument("--concurrency", type=int, default=2,
                   help="items in flight; each item runs its judges concurrently")
    p.add_argument("--no-panel-concurrency", action="store_true",
                   help="run panel members one at a time (a constrained host may "
                        "be faster serialised)")
    p.add_argument("--force", action="store_true",
                   help="re-judge configurations whose votes are already cached")
    p.add_argument("--cache-root", type=Path, default=None)
    p.add_argument("--figures-dir", type=Path, default=None)
    p.add_argument("--figure-only", type=Path, default=None, metavar="RUN_DIR",
                   help="redraw the figure from an existing run directory "
                        "(pass 'latest' for the most recent successful run)")
    a = p.parse_args(argv)

    if a.figure_only is not None:
        run_dir = a.figure_only
        if str(run_dir) == "latest":
            found = RunLog.latest("judge-panel")
            if found is None:
                print("no successful judge-panel run found", file=sys.stderr)
                return 1
            run_dir = found
        print(make_figure(Path(run_dir), a.figures_dir or C.FIGURES_DIR))
        return 0

    run(questions=a.questions,
        configs=[s.strip() for s in a.configs.split(",") if s.strip()],
        panel_rules=[s.strip() for s in a.panel_rules.split(",") if s.strip()],
        strategies=[s.strip() for s in a.strategies.split(",") if s.strip()],
        honest_source=a.honest_source, generator_model=a.generator_model,
        validity_check=a.validity_check, pass_threshold=a.pass_threshold,
        concurrency=a.concurrency, force=a.force, cache_root=a.cache_root,
        panel_concurrent=not a.no_panel_concurrency,
        figures_dir=a.figures_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
