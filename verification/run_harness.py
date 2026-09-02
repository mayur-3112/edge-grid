"""Verification evaluation harness.

Produces the validator-accuracy table: for each fraud-injection strategy, how
often the judge catches a corrupted answer and how often it wrongly rejects an
honest one.

What is different from the previous harness, and why:

  * Results go to `docs/results/<run_id>/` through `RunLog`. The old harness
    `os.remove`d the previous results file at the top of every run, so a run
    could not be compared with the one before it and a crash destroyed both.

  * Every row carries `judge_backend`, `judge_model`, `judge_calls`,
    `generator_backend`, `generator_model`, `dataset_source`, `answer_origin`,
    `blob_verified` and `da_checked`. The published "83.87% precision" figure was
    measured on answers from a Groq model whose name appears in no data file in
    this repo, which makes the number unattributable. These columns fix that
    permanently. Two of them are subtler than they look:

      - `answer_origin` distinguishes a row whose text came from the honest
        generator from one built by corrupting TruthfulQA's gold answer. The
        generator columns describe the honest arm of the run, so without this a
        fraud row reads as if the named model produced it.
      - `judge_model` is "none (no judge call)" on a DA fraud proof, and a model
        name the server did not itself confirm is marked as merely requested.

  * The honest answer's source is an explicit choice, not a fallback chain:

      --honest-source reference  TruthfulQA `best_answer`. Clean labels, but the
                                 task is then trivially easy - the judge is
                                 grading the gold answer - and the run prints
                                 that caveat. Useful as a ceiling, not a result.
      --honest-source local      real generation from local Ollama. The honest
                                 default here.
      --honest-source groq       the original path; requires GROQ_API_KEY.

    This matters because the original 75% false-positive rate on honest answers
    was substantially the *generator's* fault, not the judge's: many of those
    answers were actually wrong. Precision figures that do not name the
    generator are not interpretable.

  * Per-strategy metrics use that strategy's own subset for both classes. The
    old code read the global honest list, so all four strategy rows printed an
    identical FP/TN pair.

  * PASS, FAIL and ERROR are counted separately. ERROR - judge unreachable, or a
    response with no recoverable score - is excluded from precision and recall
    and reported in its own column, rather than being folded into FAIL where it
    would masquerade as fraud detection.

  * Alongside the raw precision the table reports `precision_bal`, precision
    corrected to a 1:1 fraud:honest prior. With four fraud strategies per
    question the raw class balance is 80:20, which inflates precision on its own;
    the corrected figure is the one to quote.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from edgegrid import config as C
from edgegrid.da import DALayer, NAMESPACE_INFERENCE
from edgegrid.runlog import RunLog
from edgegrid.schemas import Commitment, VerdictKind, new_id, sha256_hex

from verification.evaluator import BACKENDS, Judge, JudgeConfigError
from verification.fraud_injector import (STRATEGIES, VALIDITY_METHODS,
                                         check_validity, inject_fraud)
from verification.truthfulqa_loader import dataset_source, load_truthfulqa_subset
from verification.validator import ValidatorPool

HONEST_SOURCES = ("reference", "local", "groq")

# A reasoning model emits its chain of thought before the answer and the whole
# thing comes out of one budget: at 160 tokens gpt-oss-120b returned an empty
# string. The honest condition must not be silently degraded by a token limit,
# so the budget is generous and short answers simply finish early.
GEN_MAX_TOKENS = int(os.getenv("GEN_MAX_TOKENS", "900"))

GEN_SYSTEM = ("You are a helpful, accurate assistant. Answer the question "
              "directly, truthfully and concisely in one or two sentences.")

REFERENCE_CAVEAT = """
!! CAVEAT - --honest-source reference !!
The honest answers in this run are TruthfulQA's own `best_answer` strings. The
judge is therefore grading the gold label, which is a trivially easy task: any
false-positive rate measured here is a floor, not an estimate of what the judge
does to a real edge node's output. Quote this run as a ceiling on judge quality
only, and use --honest-source local for a figure about the system.
""".strip()


class GeneratorError(RuntimeError):
    """The honest-answer generator failed. Never silently substituted."""


class Generator:
    """Produces the honest edge-node answer. One explicit backend, no fallback.

    A failure raises. The harness records the failed question as a dropped row
    in the run manifest instead of quietly substituting a different source,
    which is how the previous run ended up with answers from a model it never
    named."""

    def __init__(self, source: str, model: Optional[str] = None,
                 timeout_s: float = 180.0, base_url: Optional[str] = None):
        if source not in HONEST_SOURCES:
            raise ValueError(f"unknown honest source {source!r}; choose {list(HONEST_SOURCES)}")
        self.source = source
        self.client: Any = None
        if source == "reference":
            self.model_requested = "truthfulqa:best_answer"
            self.backend_label = "reference"
        elif source == "local":
            self.model_requested = model or C.OLLAMA_MODEL
            self.backend_label = "ollama"
            self.base_url = (base_url or C.OLLAMA_HOST).rstrip("/")
            self.client = httpx.Client(timeout=timeout_s)
        else:
            if not C.GROQ_API_KEY:
                raise JudgeConfigError(
                    "--honest-source groq needs GROQ_API_KEY. It will not fall back "
                    "to a local model: the generator identity is part of the result.")
            from groq import Groq
            self.client = Groq(api_key=C.GROQ_API_KEY)
            self.model_requested = model or "allam-2-7b"
            self.backend_label = "groq"
        self.timeout_s = timeout_s
        self.model_used = ""

    def generate(self, question: str, reference: str) -> tuple[str, str]:
        """(answer, model_actually_used)."""
        if self.source == "reference":
            self.model_used = self.model_requested
            return reference, self.model_requested
        try:
            if self.source == "local":
                r = self.client.post(
                    f"{self.base_url}/api/generate",
                    json={"model": self.model_requested, "system": GEN_SYSTEM,
                          "prompt": question, "stream": False,
                          "options": {"temperature": 0.3, "num_predict": 160}},
                    timeout=self.timeout_s)
                r.raise_for_status()
                body = r.json()
                if "error" in body:
                    raise GeneratorError(f"ollama error: {body['error']}")
                text = (body.get("response") or "").strip()
                served = (body.get("model") or
                          f"{self.model_requested} (requested; server reported no model)")
            else:
                comp = self.client.chat.completions.create(
                    model=self.model_requested,
                    messages=[{"role": "system", "content": GEN_SYSTEM},
                              {"role": "user", "content": question}],
                    temperature=0.3, max_tokens=GEN_MAX_TOKENS,
                    timeout=self.timeout_s)
                text = (comp.choices[0].message.content or "").strip()
                served = (getattr(comp, "model", "") or
                          f"{self.model_requested} (requested; server reported no model)")
        except GeneratorError:
            raise
        except Exception as e:
            raise GeneratorError(f"{self.backend_label} generation failed: "
                                 f"{type(e).__name__}: {e}") from e
        # A reasoning model spends its budget thinking before it answers, so the
        # visible reply can be an unterminated <think> block or nothing at all.
        # Strip it as the judge path does, and treat "nothing left" as a
        # generator failure rather than letting an empty or half-thought string
        # be scored as an honest answer.
        from verification.evaluator import strip_think
        stripped = strip_think(text)
        if stripped:
            text = stripped
        if not text:
            raise GeneratorError(
                f"{self.backend_label} returned no answer outside its reasoning "
                f"block; raise GEN_MAX_TOKENS or choose a non-reasoning model")
        self.model_used = served
        return text, served

    def model_label(self) -> str:
        """What to record as `generator_model`. Same rule as the judge: a name we
        only asked for is marked, never presented as one that served a request."""
        return (self.model_used or
                f"{self.model_requested} (requested; no successful generation)")

    def close(self) -> None:
        if self.source == "local" and self.client is not None:
            self.client.close()


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------

def metrics_for(fraud_rows: list[dict], honest_rows: list[dict], name: str) -> dict:
    """Confusion matrix and rates for one strategy's own subset.

    Positive class is fraud. ERROR rows are held out of every rate and reported
    in their own columns - a judge outage is neither a detection nor a miss."""
    f_res = [r for r in fraud_rows if r["verdict"] in ("pass", "fail")]
    h_res = [r for r in honest_rows if r["verdict"] in ("pass", "fail")]
    tp = sum(1 for r in f_res if r["verdict"] == "fail")
    fn = sum(1 for r in f_res if r["verdict"] == "pass")
    tn = sum(1 for r in h_res if r["verdict"] == "pass")
    fp = sum(1 for r in h_res if r["verdict"] == "fail")
    err_f = len(fraud_rows) - len(f_res)
    err_h = len(honest_rows) - len(h_res)

    n_res = tp + fn + tn + fp
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    accuracy = (tp + tn) / n_res if n_res else 0.0
    fpr = fp / (fp + tn) if (fp + tn) else 0.0
    # Precision at a 1:1 prior: P = TPR / (TPR + FPR). Removes the inflation the
    # 4-fraud-per-question design bakes into the raw figure.
    precision_bal = recall / (recall + fpr) if (recall + fpr) else 0.0
    f1_bal = (2 * precision_bal * recall / (precision_bal + recall)
              if (precision_bal + recall) else 0.0)

    def mean(rows: list[dict]) -> Optional[float]:
        # A blank score is an ERROR row and is simply absent from the mean;
        # anything else non-numeric is a bug in the row writer and must surface
        # rather than being skipped into a mean that looks fine.
        s = [float(r["score"]) for r in rows if r["score"] not in ("", None)]
        return sum(s) / len(s) if s else None

    mh, mf = mean(honest_rows), mean(fraud_rows)
    return {
        "strategy": name,
        "N_fraud": len(fraud_rows), "N_honest": len(honest_rows),
        "TP": tp, "FP": fp, "TN": tn, "FN": fn,
        "ERR_fraud": err_f, "ERR_honest": err_h,
        "error_rate": round((err_f + err_h) / max(1, len(fraud_rows) + len(honest_rows)), 4),
        "precision": round(precision, 4),
        "precision_bal": round(precision_bal, 4),
        "recall": round(recall, 4),
        "fpr": round(fpr, 4),
        "f1": round(f1, 4),
        "f1_bal": round(f1_bal, 4),
        "accuracy": round(accuracy, 4),
        "mean_score_honest": "" if mh is None else round(mh, 3),
        "mean_score_fraud": "" if mf is None else round(mf, 3),
    }


# --------------------------------------------------------------------------
# the run
# --------------------------------------------------------------------------

def run(subset_size: int = 10, strategies: Optional[list[str]] = None,
        honest_source: str = "local", generator_model: Optional[str] = None,
        judge_backend: str = "ollama", judge_model: Optional[str] = None,
        validators: int = 1, quorum: Optional[int] = None,
        validity_check: str = "lexical", concurrency: int = 4,
        pass_threshold: int = C.PASS_THRESHOLD,
        results_dir: Optional[Path] = None) -> Path:
    strategies = strategies or list(STRATEGIES)
    if judge_backend not in BACKENDS:
        raise ValueError(f"unknown judge backend {judge_backend!r}; choose {list(BACKENDS)}")
    if validity_check not in VALIDITY_METHODS:
        raise ValueError(f"unknown validity check {validity_check!r}; "
                         f"choose {list(VALIDITY_METHODS)}")
    if validity_check == "llm":
        # `check_validity(method="llm")` needs an `llm_fn` the harness has no way
        # to supply from a flag. Refusing here beats failing 40 corruptions deep.
        raise ValueError(
            "validity_check='llm' needs an llm_fn and is not wired to the harness; "
            "call fraud_injector.check_validity(method='llm', llm_fn=...) directly. "
            "The harness offers 'none' and 'lexical'.")
    mock = judge_backend == "mock"

    params = {
        "subset_size": subset_size, "strategies": strategies,
        "honest_source": honest_source, "generator_model": generator_model,
        "judge_backend": judge_backend, "judge_model": judge_model,
        "validators": validators, "quorum": quorum,
        "validity_check": validity_check, "pass_threshold": pass_threshold,
        "audit_rate": 1.0,
        "audit_rate_note": ("this is a measurement harness so every item is audited; "
                            f"production sampling is C.SAMPLE_RATE={C.SAMPLE_RATE}"),
        "mock_judge": mock,
    }

    with RunLog("verification", params, results_dir=results_dir) as log:
        if honest_source == "reference":
            print(REFERENCE_CAVEAT)
            log.note("HONEST SOURCE IS THE GOLD LABEL - see caveat in README")
            (log.dir / "CAVEAT.txt").write_text(REFERENCE_CAVEAT + "\n")
        if mock:
            print("!! judge-backend=mock: every row is tagged mock and NONE of these "
                  "numbers describe a real judge. Explicitly requested, so proceeding.")
            log.note("MOCK JUDGE - results are not a measurement")

        gen = Generator(honest_source, generator_model)
        try:
            judges = [Judge(backend=judge_backend, model=judge_model,
                            pass_threshold=pass_threshold)
                      for _ in range(validators)]
        except BaseException:
            # The generator already holds an HTTP client; a judge that cannot be
            # constructed (an unset GROQ_API_KEY, say) must not leak it.
            gen.close()
            raise
        try:
            da = DALayer(root_dir=log.dir / "da")
            pool = ValidatorPool(judges, quorum=quorum, da=da,
                                 max_workers=max(1, validators))

            questions = load_truthfulqa_subset(n=subset_size)
            data_source = dataset_source(questions)
            log.note(f"dataset source: {data_source}")
            pool_answers = [q["best_answer"] for q in questions]

            print("=" * 78)
            print("EDGE GRID - VERIFICATION HARNESS")
            print(f"  run           : {log.run_id}")
            print(f"  questions     : {len(questions)} ({data_source})   "
                  f"strategies: {','.join(strategies)}")
            print(f"  honest source : {honest_source} ({gen.model_requested})")
            print(f"  judge         : {judge_backend} ({judges[0].model_requested}) "
                  f"x{validators} quorum={pool.quorum} independent={pool.independent}")
            print(f"  validity check: {validity_check}   pass threshold: {pass_threshold}")
            print("=" * 78)

            # -- 1. honest answers -------------------------------------------
            dropped: list[dict] = []

            def drop(what: str, why: str) -> None:
                log.drop(what, why)
                dropped.append({"what": what, "why": why})
                print(f"  drop {what}: {why}")

            t0 = time.monotonic()
            honest: dict[int, str] = {}
            for q in questions:
                try:
                    ans, _served = gen.generate(q["question"], q["best_answer"])
                    honest[q["question_id"]] = ans
                except GeneratorError as e:
                    drop(f"q{q['question_id']}:honest", str(e))
            print(f"[gen] {len(honest)}/{len(questions)} honest answers in "
                  f"{time.monotonic() - t0:.1f}s via {gen.backend_label}/{gen.model_used}")

            # -- 2. build the item list, dropping invalid corruptions ---------
            items: list[dict] = []
            for q in questions:
                qid = q["question_id"]
                if qid in honest:
                    items.append({"question_id": qid, "question": q["question"],
                                  "answer": honest[qid], "is_fraud": False,
                                  "fraud_strategy": "none (honest)",
                                  "expected_verdict": "pass", "validity_sim": "",
                                  "answer_origin": "honest-generator"})
                for strat in strategies:
                    corrupted, used = inject_fraud(
                        q["question"], q["best_answer"], q["incorrect_answers"],
                        strategy=strat, all_answers_pool=pool_answers, seed=qid)
                    v = check_validity(q["question"], corrupted, q["correct_answers"],
                                       q["best_answer"], method=validity_check)
                    if not v.valid:
                        drop(f"q{qid}:{strat}", f"invalid corruption - {v.reason}")
                        continue
                    # Corruptions are derived from TruthfulQA's gold answer, not
                    # from the honest generator's output, so a fraud row's text
                    # never came from `generator_model`. Recorded per row, because
                    # the run-level generator columns describe the honest arm only.
                    items.append({"question_id": qid, "question": q["question"],
                                  "answer": corrupted, "is_fraud": True,
                                  "fraud_strategy": used, "expected_verdict": "fail",
                                  "validity_sim": round(v.similarity, 3),
                                  "answer_origin": "truthfulqa-gold+fraud_injector"})

            # -- 3. commit each answer to DA, then audit ----------------------
            for it in items:
                blob = da.submit_blob(it["answer"], NAMESPACE_INFERENCE, seal=True)
                it["commitment"] = Commitment(
                    job_id=new_id(), provider_peer_id="harness-provider",
                    output_hash=sha256_hex(it["answer"]), namespace=NAMESPACE_INFERENCE,
                    blob_ref=blob.blob_id, blob_height=blob.height,
                    prompt_hash=sha256_hex(it["question"]))

            print(f"[judge] {len(items)} items, {len(items) * validators} judge calls, "
                  f"concurrency={concurrency}")
            t0 = time.monotonic()
            done = 0

            def audit(it: dict):
                return pool.audit(it["commitment"], it["question"], it["answer"])

            rows: list[dict] = []
            with cf.ThreadPoolExecutor(max_workers=max(1, concurrency)) as ex:
                for it, outcome in zip(items, ex.map(audit, items)):
                    done += 1
                    v0 = outcome.verdicts[0] if outcome.verdicts else None
                    row = {
                        "question_id": it["question_id"],
                        "question": it["question"],
                        "answer": it["answer"],
                        "is_fraud": it["is_fraud"],
                        "fraud_strategy": it["fraud_strategy"],
                        "answer_origin": it["answer_origin"],
                        "validity_sim": it["validity_sim"],
                        "expected_verdict": it["expected_verdict"],
                        "verdict": outcome.verdict.value,
                        "score": "" if outcome.mean_score is None else outcome.mean_score,
                        "correct": (outcome.verdict.value == it["expected_verdict"]
                                    if outcome.verdict is not VerdictKind.ERROR else ""),
                        "n_pass": outcome.n_pass, "n_fail": outcome.n_fail,
                        "n_error": outcome.n_error, "split": outcome.split,
                        "blob_verified": outcome.blob_verified,
                        "da_checked": outcome.da_checked,
                        "fraud_proof": outcome.fraud_proof,
                        "judge_backend": judge_backend,
                        # A row with no judge call (a DA fraud proof) gets no model
                        # name. Writing the requested one here would put a model on a
                        # verdict the model never saw.
                        "judge_model": v0.judge_model if v0 else "none (no judge call)",
                        "judge_calls": outcome.judge_calls,
                        # Run-level: these describe the honest arm. `answer_origin`
                        # says which arm this row's text actually came from.
                        "generator_backend": gen.backend_label,
                        "generator_model": gen.model_label(),
                        "dataset_source": data_source,
                        "pass_threshold": pass_threshold,
                        "latency_ms": round(outcome.latency_ms, 1),
                        "reason": (v0.reason if v0 else outcome.reason)[:300],
                        "job_id": outcome.job_id,
                    }
                    log.append("raw", row)
                    rows.append(row)
                    mark = {"pass": "P", "fail": "F", "error": "E"}[row["verdict"]]
                    print(f"  [{done}/{len(items)}] q{row['question_id']:>3} "
                          f"{row['fraud_strategy']:<26} {mark} "
                          f"score={row['score']}")
            print(f"[judge] done in {time.monotonic() - t0:.1f}s")

            # -- 4. metrics ---------------------------------------------------
            honest_rows = [r for r in rows if not r["is_fraud"]]
            summary: list[dict] = []
            for strat in sorted({r["fraud_strategy"] for r in rows if r["is_fraud"]}):
                f_rows = [r for r in rows if r["fraud_strategy"] == strat]
                qids = {r["question_id"] for r in f_rows}
                # honest denominator comes from this subset's own questions, so a
                # dropped corruption moves it - the old harness read the global list
                # and printed the same FP/TN on every strategy row.
                h_rows = [r for r in honest_rows if r["question_id"] in qids]
                summary.append(metrics_for(f_rows, h_rows, strat))
            overall = metrics_for([r for r in rows if r["is_fraud"]], honest_rows, "OVERALL")
            summary.append(overall)
            log.write_table("summary", summary)

            n_err = sum(1 for r in rows if r["verdict"] == "error")
            n_proof = sum(1 for r in rows if r["fraud_proof"])
            # Name the model from a row where a judge actually ran, so a run made
            # entirely of DA fraud proofs reports no judge rather than a plausible one.
            judged_rows = [r for r in rows if r["judge_calls"]]
            judge_model_used = judged_rows[0]["judge_model"] if judged_rows else "none (no judge call)"
            log.write_json("headline", {
                "run_id": log.run_id,
                "judge_backend": judge_backend,
                "judge_model": judge_model_used,
                "n_judge_calls": sum(r["judge_calls"] for r in rows),
                "generator_backend": gen.backend_label,
                "generator_model": gen.model_label(),
                "dataset_source": data_source,
                "honest_source": honest_source,
                "n_items": len(rows), "n_error": n_err,
                "n_da_fraud_proofs": n_proof,
                "n_dropped_invalid": len(dropped),
                "dropped": dropped,
                "precision_raw": overall["precision"],
                "precision_balanced": overall["precision_bal"],
                "recall": overall["recall"],
                "f1_balanced": overall["f1_bal"],
                "false_positive_rate_honest": overall["fpr"],
                "mean_score_honest": overall["mean_score_honest"],
                "mean_score_fraud": overall["mean_score_fraud"],
                "mock": mock,
                # Judge and generator being the same model is self-evaluation: the
                # judge shares the generator's blind spots, so a low false-positive
                # rate here is partly an artefact and must be reported as one.
                "self_evaluation": (gen.backend_label == judge_backend
                                    and gen.model_label() == judge_model_used),
            })

            _print_table(summary)
            print(f"\nERROR verdicts (excluded from all rates): {n_err}/{len(rows)}")
            print(f"DA fraud proofs (caught without a judge) : {n_proof}")
            print(f"Rows dropped (invalid corruption / gen failure) : {len(dropped)}")
            print(f"\njudge     : {judge_backend} / {judge_model_used}")
            print(f"generator : {gen.backend_label} / {gen.model_label()}"
                  f"   (honest-source={honest_source})")
            print(f"dataset   : {data_source}")
            if honest_source == "reference":
                print("\n" + REFERENCE_CAVEAT)
            print(f"\nresults: {log.dir}")
            return log.dir
        finally:
            for j in judges:
                j.close()
            gen.close()


def _print_table(summary: list[dict]) -> None:
    cols = [("strategy", 26), ("N_fraud", 7), ("N_honest", 8), ("TP", 4), ("FP", 4),
            ("TN", 4), ("FN", 4), ("ERR", 4), ("precision", 9), ("prec_bal", 9),
            ("recall", 8), ("fpr", 7), ("f1_bal", 7), ("hon_mu", 7), ("frd_mu", 7)]
    line = "  ".join(f"{h:<{w}}" for h, w in cols)
    print("\n" + line)
    print("-" * len(line))
    for r in summary:
        vals = [r["strategy"][:26], r["N_fraud"], r["N_honest"], r["TP"], r["FP"],
                r["TN"], r["FN"], r["ERR_fraud"] + r["ERR_honest"],
                f"{r['precision']:.2%}", f"{r['precision_bal']:.2%}",
                f"{r['recall']:.2%}", f"{r['fpr']:.2%}", f"{r['f1_bal']:.3f}",
                r["mean_score_honest"], r["mean_score_fraud"]]
        print("  ".join(f"{str(v):<{w}}" for v, (_h, w) in zip(vals, cols)))
    print("-" * len(line))


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Edge Grid verification harness")
    p.add_argument("--subset-size", type=int, default=10)
    p.add_argument("--strategies", type=str, default=",".join(STRATEGIES))
    p.add_argument("--honest-source", choices=HONEST_SOURCES, default="local",
                   help="reference = TruthfulQA gold (trivially easy, prints a caveat); "
                        "local = real Ollama generation; groq = needs GROQ_API_KEY")
    p.add_argument("--generator-model", type=str, default=None)
    p.add_argument("--judge-backend", choices=("groq", "ollama", "mock"), default="ollama",
                   help="mock must be asked for by name; every row it writes is tagged mock")
    p.add_argument("--judge-model", type=str, default=None)
    p.add_argument("--validators", type=int, default=1)
    p.add_argument("--quorum", type=int, default=None)
    p.add_argument("--validity-check", choices=("none", "lexical"), default="lexical")
    p.add_argument("--concurrency", type=int, default=4)
    p.add_argument("--pass-threshold", type=int, default=C.PASS_THRESHOLD)
    a = p.parse_args(argv)
    run(subset_size=a.subset_size,
        strategies=[s.strip() for s in a.strategies.split(",") if s.strip()],
        honest_source=a.honest_source, generator_model=a.generator_model,
        judge_backend=a.judge_backend, judge_model=a.judge_model,
        validators=a.validators, quorum=a.quorum,
        validity_check=a.validity_check, concurrency=a.concurrency,
        pass_threshold=a.pass_threshold)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
