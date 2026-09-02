"""Experiment 4 - cost per verified inference, grid versus centralised.

This is the experiment the Phase-1 run reported as a single ETH-vs-USD pair with
`tokens_per_job` set to 30 in one file and 256 in another. It is rebuilt here to
answer a narrower but defensible question:

    What does one thousand delivered tokens cost on the grid, once the
    verification that makes the output trustworthy is paid for?

Three honesty constraints shape the method:

  * Verification is itself an inference call. It is incurred on SAMPLE_RATE of
    jobs, and omitting it would flatter the grid. It is a separate, visible
    component of the cost, never a footnote.
  * GRID has no market price. Every dollar figure is a notional conversion at a
    stated rate and is labelled a cost *model*, not a market observation. The
    token-denominated figures beside it are the real measurement.
  * Settlement gas is reported in gas units, not dollars. Converting gas to
    dollars needs a gas price and an ETH price, and this runs on a local chain
    where neither exists. Inventing them would be the same error as before.

Inputs are the recorded runs of the other three experiments, so this experiment
never re-measures anything; it composes measurements that already exist and
states which run each came from.
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

import pandas as pd

from edgegrid import config as C
from edgegrid.runlog import RunLog


class MissingInput(Exception):
    """A prerequisite experiment has not been run."""


def _latest(experiment: str) -> Path:
    p = RunLog.latest(experiment)
    if p is None:
        raise MissingInput(
            f"no successful '{experiment}' run in {C.RESULTS_DIR}; run it first")
    return p


def _read_csv(run: Path, name: str) -> pd.DataFrame:
    f = run / f"{name}.csv"
    if not f.exists() or f.stat().st_size == 0:
        raise MissingInput(f"{run.name} has no {name}.csv")
    return pd.read_csv(f)


def collect(log: RunLog) -> dict:
    """Pull the measured quantities out of the other experiments' runs."""
    bench_run = _latest("inference-benchmark")
    trials = _read_csv(bench_run, "trials")
    if "eval_count" not in trials or "total_ms" not in trials:
        raise MissingInput(f"{bench_run.name}/trials.csv lacks eval_count/total_ms")
    tokens_per_job = float(trials["eval_count"].mean())
    ms_per_job = float(trials["total_ms"].mean())
    tok_per_sec = float(trials["tokens_per_sec"].mean())
    model = str(trials["served_model"].iloc[0])
    log.note(f"inference from {bench_run.name}: {tokens_per_job:.1f} tok/job, "
             f"{tok_per_sec:.2f} tok/s, model {model}")

    settle_run = _latest("settlement-onchain")
    settlements = _read_csv(settle_run, "settlements")
    gas_path = settle_run / "gas_used.json"
    gas = json.loads(gas_path.read_text()) if gas_path.exists() else {}
    # An escrow that is opened, committed and released is the honest-path cost.
    honest_gas = sum(int(gas.get(k, 0)) for k in
                     ("openEscrow", "recordCommitment", "release", "withdraw:marketplace"))
    fraud_gas = sum(int(gas.get(k, 0)) for k in
                    ("openEscrow", "recordCommitment", "submitVerdict"))
    prices = [float(x) for x in settlements["amount"] if float(x) > 0]
    price_per_job = statistics.mean(prices) if prices else 0.0
    log.note(f"settlement from {settle_run.name}: mean escrow {price_per_job:.4f} GRID, "
             f"honest-path gas {honest_gas}")

    verif_run = _latest("verification")
    raw = _read_csv(verif_run, "raw")
    judged = raw[raw["judge_calls"] > 0] if "judge_calls" in raw else raw
    judge_calls_per_audit = float(judged["judge_calls"].mean()) if len(judged) else 1.0
    judge_backend = str(raw["judge_backend"].iloc[0]) if len(raw) else "unknown"
    judge_model = str(raw["judge_model"].iloc[0]) if len(raw) else "unknown"
    log.note(f"verification from {verif_run.name}: {judge_calls_per_audit:.2f} judge "
             f"calls per audit on {judge_backend}/{judge_model}")

    return {
        "tokens_per_job": tokens_per_job,
        "ms_per_job": ms_per_job,
        "tokens_per_sec": tok_per_sec,
        "model": model,
        "price_per_job_grid": price_per_job,
        "honest_gas": honest_gas,
        "fraud_gas": fraud_gas,
        "judge_calls_per_audit": judge_calls_per_audit,
        "judge_backend": judge_backend,
        "judge_model": judge_model,
        "sources": {
            "inference": bench_run.name,
            "settlement": settle_run.name,
            "verification": verif_run.name,
        },
    }


def compute(m: dict) -> tuple[list[dict], dict]:
    tokens_per_job = max(m["tokens_per_job"], 1e-9)
    jobs_per_1k = 1000.0 / tokens_per_job

    # provider revenue per 1k delivered tokens, in GRID and at the notional rate
    grid_per_1k = m["price_per_job_grid"] * jobs_per_1k
    inference_usd = grid_per_1k * C.GRID_USD

    # A judge call is an inference of comparable size, so its cost is the same
    # per-token price, incurred on SAMPLE_RATE of jobs.
    audits_per_1k = jobs_per_1k * C.SAMPLE_RATE
    verify_usd = audits_per_1k * m["judge_calls_per_audit"] * m["price_per_job_grid"] * C.GRID_USD

    central_usd = C.CENTRALIZED_USD_PER_1K_TOKENS

    rows = [
        {"label": "The Edge Grid (this work)",
         "usd_per_1k": round(inference_usd, 8),
         "verification_usd_per_1k": round(verify_usd, 8),
         "total_usd_per_1k": round(inference_usd + verify_usd, 8),
         "grid_per_1k": round(grid_per_1k, 6),
         "basis": "measured clearing price x measured tokens/job, notional GRID_USD"},
        {"label": "Centralised API baseline",
         "usd_per_1k": round(central_usd, 8),
         "verification_usd_per_1k": 0.0,
         "total_usd_per_1k": round(central_usd, 8),
         "grid_per_1k": 0.0,
         "basis": "published list rate, no independent verification performed"},
    ]

    total = inference_usd + verify_usd
    headline = {
        "tokens_per_job": round(tokens_per_job, 2),
        "jobs_per_1k_tokens": round(jobs_per_1k, 3),
        "grid_per_1k_tokens": round(grid_per_1k, 6),
        "inference_usd_per_1k": round(inference_usd, 8),
        "verification_usd_per_1k": round(verify_usd, 8),
        "verification_share_pct": round(100 * verify_usd / total, 2) if total else 0.0,
        "total_usd_per_1k": round(total, 8),
        "centralised_usd_per_1k": round(central_usd, 8),
        "ratio_vs_centralised": round(total / central_usd, 4) if central_usd else None,
        "sample_rate": C.SAMPLE_RATE,
        "grid_usd_rate": C.GRID_USD,
        "settlement_gas_honest_path": m["honest_gas"],
        "settlement_gas_fraud_path": m["fraud_gas"],
        "gas_note": ("gas is reported in units, not dollars: a local chain has no gas "
                     "price and no ETH price, and inventing them would be a fabrication"),
        "caveat": ("GRID has no market price. Dollar figures are a cost MODEL at a stated "
                   "notional rate, not a market observation. The GRID-denominated and "
                   "gas-denominated figures are the actual measurements."),
        "sources": m["sources"],
        "judge": f"{m['judge_backend']}/{m['judge_model']}",
        "model": m["model"],
    }
    return rows, headline


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Experiment 4 - cost and settlement")
    ap.parse_args(argv)

    with RunLog("cost", {"sample_rate": C.SAMPLE_RATE, "grid_usd": C.GRID_USD,
                         "centralised_usd_per_1k": C.CENTRALIZED_USD_PER_1K_TOKENS}) as log:
        try:
            measured = collect(log)
        except MissingInput as e:
            log.drop("inputs", str(e))
            print(f"cannot run: {e}")
            raise

        rows, headline = compute(measured)
        log.write_table("cost_summary", rows)
        log.write_json("headline", headline)
        log.write_json("measured", measured)

        w = max(len(r["label"]) for r in rows)
        print(f"\ncost per 1,000 delivered tokens  (model {measured['model']}, "
              f"judge {headline['judge']})")
        print(f"  {'':{w}}   inference   verification        total")
        for r in rows:
            print(f"  {r['label']:{w}}  ${r['usd_per_1k']:.6f}     "
                  f"${r['verification_usd_per_1k']:.6f}  ${r['total_usd_per_1k']:.6f}")
        print(f"\n  verification is {headline['verification_share_pct']}% of grid cost "
              f"at a {C.SAMPLE_RATE:.0%} audit rate")
        if headline["ratio_vs_centralised"] is not None:
            print(f"  grid total is {headline['ratio_vs_centralised']:.3f}x the centralised rate")
        print(f"\n  settlement gas: honest path {headline['settlement_gas_honest_path']:,} "
              f"| fraud path {headline['settlement_gas_fraud_path']:,}")
        print(f"\n  {headline['caveat']}")
        print(f"\nresults -> {log.dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
