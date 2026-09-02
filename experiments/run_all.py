"""Run the four experiments end to end, in dependency order.

    python experiments/run_all.py                 # everything
    python experiments/run_all.py --skip auction  # everything but one
    python experiments/run_all.py --quick         # small N, for a smoke check

Order matters: cost composes measurements from the other three, so it runs last.
Each stage is a subprocess, so one failure cannot take the others down, and the
exit status of each is recorded. Nothing here invents a number - a stage that
cannot run is reported as skipped and the figures for it are simply absent.

Prerequisites are checked before anything starts, because discovering halfway
through a thirty-minute run that Ollama is down wastes the whole run.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

# Run either as `python experiments/run_all.py` or `python -m experiments.run_all`:
# the first puts experiments/ on sys.path rather than the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from edgegrid import config as C

PY = sys.executable
REPO = C.REPO_ROOT


def _port_open(host: str, port: int, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False


def preflight(quick: bool) -> dict:
    ollama_host = C.OLLAMA_HOST.split("//")[-1]
    host, _, port = ollama_host.partition(":")
    checks = {
        "ollama": _port_open(host or "localhost", int(port or 11434)),
        "chain": _port_open("127.0.0.1", 8545),
        "deployment": C.DEPLOYMENT_FILE.exists(),
        "dataset": (C.DATA_DIR / "truthfulqa_subset.csv").exists(),
    }
    print("preflight")
    for k, ok in checks.items():
        print(f"  {'ok  ' if ok else 'MISS'}  {k}")
    if not checks["ollama"]:
        print("\n  Ollama is not reachable. Start it with `ollama serve`; "
              "inference, verification and paraphrase all need it.")
    if not (checks["chain"] and checks["deployment"]):
        print("\n  No local chain or no deployment.json - the settlement stage will be\n"
              "  skipped. Start one with `make chain`, then `make contracts`.")
    return checks


STAGES = [
    # (name, module, args, quick-args, requires)
    ("inference", "inference.benchmark",
     ["--trials", "20", "--cold-pairs", "5", "--max-tokens", "64"],
     ["--trials", "5", "--cold-pairs", "1", "--max-tokens", "32"],
     ("ollama",)),
    ("auction", "discovery.run_network",
     ["--nodes", "3", "4", "5", "--repeats", "3", "--ttl", "45"],
     ["--nodes", "3", "--repeats", "1", "--ttl", "30"],
     ()),
    ("auction-summary", "discovery.summarize", [], [], ()),
    ("verification", "verification.run_harness",
     ["--subset-size", "20", "--honest-source", "local",
      "--judge-backend", "ollama", "--validity-check", "lexical", "--concurrency", "3"],
     ["--subset-size", "3", "--honest-source", "reference",
      "--judge-backend", "ollama", "--concurrency", "2"],
     ("ollama",)),
    ("paraphrase", "verification.paraphrase_check",
     ["--questions", "10", "--k", "4"],
     ["--questions", "2", "--k", "2"],
     ("ollama",)),
    ("settlement", "contracts.scripts.lifecycle", [], [], ("chain", "deployment")),
    ("cost", "experiments.cost", [], [], ()),
]


def run_stage(name: str, module: str, args: list[str], timeout: float) -> dict:
    t0 = time.monotonic()
    print(f"\n{'=' * 72}\n  {name}\n{'=' * 72}")
    if module == "contracts.scripts.lifecycle":
        cmd = [PY, str(REPO / "contracts" / "scripts" / "lifecycle.py"), *args]
    else:
        cmd = [PY, "-m", module, *args]
    try:
        r = subprocess.run(cmd, cwd=REPO, timeout=timeout)
        code = r.returncode
    except subprocess.TimeoutExpired:
        print(f"  {name} exceeded {timeout:.0f}s and was killed")
        code = -9
    except KeyboardInterrupt:
        raise
    except Exception as e:
        print(f"  {name} could not start: {e}")
        code = -1
    return {"stage": name, "module": module, "args": args, "returncode": code,
            "elapsed_s": round(time.monotonic() - t0, 1),
            "status": "ok" if code == 0 else "failed"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--skip", action="append", default=[],
                    choices=[s[0] for s in STAGES])
    ap.add_argument("--only", action="append", default=[],
                    choices=[s[0] for s in STAGES])
    ap.add_argument("--quick", action="store_true", help="small N, for a smoke check")
    ap.add_argument("--timeout", type=float, default=3600.0, help="per stage, seconds")
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args(argv)

    checks = preflight(args.quick)
    results = []
    for name, module, full, quick, requires in STAGES:
        if args.only and name not in args.only:
            continue
        if name in args.skip:
            results.append({"stage": name, "status": "skipped", "why": "--skip"})
            print(f"\n  skipping {name} (asked for)")
            continue
        missing = [r for r in requires if not checks.get(r)]
        if missing:
            results.append({"stage": name, "status": "skipped",
                            "why": f"missing prerequisite: {', '.join(missing)}"})
            print(f"\n  skipping {name}: needs {', '.join(missing)}")
            continue
        results.append(run_stage(name, module, quick if args.quick else full, args.timeout))

    if not args.no_figures:
        print(f"\n{'=' * 72}\n  figures\n{'=' * 72}")
        subprocess.run([PY, "-m", "experiments.make_figures"], cwd=REPO)
        subprocess.run([PY, "-m", "experiments.make_tables"], cwd=REPO)

    print(f"\n{'=' * 72}\n  summary\n{'=' * 72}")
    w = max(len(r["stage"]) for r in results)
    for r in results:
        extra = r.get("why", "") or f"{r.get('elapsed_s', 0):.0f}s"
        print(f"  {r['status']:8s}  {r['stage']:{w}}  {extra}")
    failed = [r["stage"] for r in results if r["status"] == "failed"]
    skipped = [r["stage"] for r in results if r["status"] == "skipped"]
    (C.RESULTS_DIR / "run_all.json").write_text(json.dumps(
        {"results": results, "preflight": checks}, indent=2))
    if skipped:
        print(f"\n  {len(skipped)} stage(s) skipped - the figures for them will be absent, "
              f"not estimated")
    if failed:
        print(f"\n  {len(failed)} stage(s) FAILED: {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
