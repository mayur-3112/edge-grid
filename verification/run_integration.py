"""
End-to-End Integration Runner for The Edge Grid.

Chains:
Track B (Inference Engine) -> Track C (LLM-as-Judge Verifier) -> Track D (Simulated Ledger Settlement)

Demonstrates:
1. Provider registers stake on ledger.
2. Inference executed on edge node.
3. Judge scores result (quality score 1-5, PASS/FAIL).
4. Ledger settles payment or slashes stake upon failure.
5. Exports complete transaction history to CSV.
"""

import os
import sys
import uuid
from pathlib import Path

# Ensure paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import argparse

from edgegrid.runlog import RunLog
from edgegrid.schemas import VerdictKind
from verification.evaluator import Judge
from contracts.simulate import SimulatedLedger, centralized_cost


def run_pipeline_demo(judge_backend: str = "ollama", judge_model=None):
    print("=" * 70)
    print("THE EDGE GRID - FULL PIPELINE INTEGRATION RUNNER")
    print("Inference -> Verification -> Settlement")
    print("=" * 70)

    # 1. Initialize Track D Ledger
    ledger = SimulatedLedger()
    peers = ["node-alpha-1", "node-beta-2", "node-gamma-3"]
    initial_stake = 10.0
    for p in peers:
        ledger.register_stake(p, initial_stake)
    print(f"\n[1] Initialized Ledger: Registered peers {peers} with {initial_stake} ETH stake each.")

    # 2. Initialize Track C Judge
    # Backend is explicit: an unset GROQ_API_KEY now raises rather than swapping
    # in a mock, so this demo can never report a mock's verdicts as a judge's.
    judge = Judge(backend=judge_backend, model=judge_model)
    print(f"[2] Initialized Judge: {judge.model_requested} via {judge_backend}.")
    if judge_backend == "mock":
        print("    !! mock judge: a lexical stand-in, NOT a measurement instrument. "
              "Nothing below describes a real judge.")

    # 3. Setup test jobs (mix of honest prompts, hallucinated outputs, and edge cases)
    test_jobs = [
        {
            "job_id": str(uuid.uuid4())[:8],
            "peer": "node-alpha-1",
            "prompt": "What causes tides on Earth?",
            "output": "Ocean tides are caused by gravitational forces exerted by the Moon and the Sun upon the Earth's oceans.",
            "price": 0.25,
        },
        {
            "job_id": str(uuid.uuid4())[:8],
            "peer": "node-beta-2",
            "prompt": "What is the primary gas in Earth's atmosphere?",
            "output": "Nitrogen makes up approximately 78% of Earth's atmosphere by volume.",
            "price": 0.20,
        },
        {
            "job_id": str(uuid.uuid4())[:8],
            "peer": "node-alpha-1",
            "prompt": "Can humans breathe underwater without equipment?",
            "output": "Yes, with proper meditation and lung training, humans can absorb oxygen directly from water.",
            "price": 0.30,  # Blatant hallucination -> should be slashed
        },
        {
            "job_id": str(uuid.uuid4())[:8],
            "peer": "node-gamma-3",
            "prompt": "What is the boiling point of water at sea level?",
            "output": "Water boils at 100 degrees Celsius (212 degrees Fahrenheit) at standard atmospheric pressure.",
            "price": 0.15,
        },
    ]

    # A context manager, so a crash mid-pipeline still writes a manifest saying
    # where it stopped. Previously `finish()` was only reached on the happy path,
    # so a failed run left a directory that looked merely empty.
    with RunLog("integration", {"judge_backend": judge_backend,
                                "judge_model": judge_model,
                                "n_jobs": len(test_jobs)}) as log:
        try:
            _process(test_jobs, judge, judge_backend, ledger, log)
        finally:
            judge.close()

        print("=" * 70)
        print("FINAL INTEGRATION SUMMARY")
        print("=" * 70)
        print(f"Remaining node stakes: {ledger.stakes}")
        print(f"Total settled payout : {ledger.total_cost():.4f} ETH")
        comp_cost = centralized_cost(num_jobs=len(test_jobs), tokens_per_job=30,
                                     price_per_1k_tokens=0.002)
        print(f"Centralized baseline : ${comp_cost:.6f}")
        log.write_json("headline", {
            "judge_backend": judge_backend,
            "judge_model": judge.model_used or
                           f"{judge.model_requested} (requested; no successful call)",
            "n_jobs": len(test_jobs)})
        print(f"Pipeline logs saved  : {log.dir}")
        print("=" * 70)


def _process(test_jobs, judge, judge_backend, ledger, log) -> None:
    print(f"\n[3] Processing {len(test_jobs)} inference jobs through Judge and Settlement Ledger:\n")

    for idx, job in enumerate(test_jobs, 1):
        print(f"--- Job {idx} (ID: {job['job_id']}) by {job['peer']} ---")
        print(f"Prompt : {job['prompt']}")
        print(f"Output : {job['output']}")

        # Track C: Judge Verification
        v = judge.score(prompt=job["prompt"], output=job["output"], job_id=job["job_id"])
        verdict = v.verdict.value
        score = v.quality_score
        reason = v.reason
        print(f"Judge  : Quality Score {score}/5 -> Verdict [{verdict.upper()}] (Reason: {reason})")

        # Every row names the backend and the model that produced its verdict.
        # Without these columns a mock run and a real run are indistinguishable
        # in the CSV, and this table is the one that shows money moving.
        base_row = {
            "job_id": job["job_id"],
            "provider_peer_id": job["peer"],
            "prompt": job["prompt"],
            "output": job["output"],
            "judge_backend": v.judge_backend,
            "judge_model": v.judge_model,
            "judge_quality_score": "" if score is None else score,
            "judge_verdict": verdict,
            "judge_reason": reason,
            "amount": job["price"],
        }

        if v.verdict is VerdictKind.ERROR:
            # An unreachable or unparseable judge must not move money in either
            # direction. Escrow stays open and the job is recorded as unresolved -
            # as a row, not only as a manifest drop, so the CSV never implies the
            # job simply did not happen.
            log.drop(job["job_id"], f"judge ERROR, settlement withheld: {reason}")
            log.append("integration", base_row | {
                "settled": False, "slashed": "", "slash_amount": "",
                "remaining_stake": ledger.stakes[job["peer"]],
                "settlement_note": "withheld: judge ERROR, escrow stays open"})
            print("Ledger : WITHHELD (judge ERROR - escrow stays open)\n")
            continue

        # Track D: Settlement
        settlement_rec = ledger.settle(
            job_id=job["job_id"],
            provider_peer_id=job["peer"],
            amount=job["price"],
            verdict=verdict,
        )

        status_str = f"SLASHED (-{settlement_rec['slash_amount']} ETH)" if settlement_rec["slashed"] else f"PAID (+{settlement_rec['amount']} ETH)"
        print(f"Ledger : {status_str} | Remaining stake: {ledger.stakes[job['peer']]:.2f} ETH\n")

        log.append("integration", base_row | {
            "settled": True,
            "slashed": settlement_rec["slashed"],
            "slash_amount": settlement_rec["slash_amount"],
            "remaining_stake": ledger.stakes[job["peer"]],
            "settlement_note": "",
        })


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--judge-backend", choices=("groq", "ollama", "mock"), default="ollama")
    ap.add_argument("--judge-model", default=None)
    _a = ap.parse_args()
    run_pipeline_demo(_a.judge_backend, _a.judge_model)
