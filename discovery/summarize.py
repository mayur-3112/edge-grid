"""Pool every recorded Experiment 2 run into one table, with dispersion.

The README used to quote a single `run_network` invocation. That is the shape of
number that does not survive being re-run: on a loaded host the same nine
auctions move by a factor of three, and one run has no way to say so. Reading
every `exp2-auction-convergence-*/auctions.csv` on disk and reporting a spread
alongside each mean fixes that - and it makes the README's table the output of a
command rather than a copy-paste, so a reader can regenerate it.

Runs are pooled, never merged silently: `sources.csv` lists exactly which run
directories contributed and how many auctions each supplied, and any run whose
auctions did not all succeed is reported rather than dropped.

    python -m discovery.summarize
    python -m discovery.summarize --experiment exp2-warm-bonus
"""

from __future__ import annotations

import argparse
import csv
import statistics
from pathlib import Path
from typing import Optional

from edgegrid import config as C
from edgegrid.runlog import RunLog

METRICS = [
    ("first_bid_ms", "first bid (ms)"),
    ("last_bid_ms", "last bid (ms)"),
    ("broadcast_to_award_ms", "broadcast -> award (ms)"),
    ("mesh_ready_ms", "mesh ready (ms)"),
]


def load_runs(experiment: str, results_dir: Optional[Path] = None) -> list[dict]:
    """Every auction row on disk for `experiment`, tagged with its run id."""
    base = Path(results_dir) if results_dir else C.RESULTS_DIR
    rows: list[dict] = []
    for run_dir in sorted(base.glob(f"{experiment}-*")):
        path = run_dir / "auctions.csv"
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                row["run_id"] = run_dir.name
                rows.append(row)
    return rows


def _num(row: dict, key: str) -> Optional[float]:
    raw = row.get(key, "")
    if raw in ("", None):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def summarize(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """(per-node-count stats, per-run provenance). Failed auctions are excluded
    from the statistics and counted in the provenance, never both."""
    ok = [r for r in rows if str(r.get("ok", "")).lower() == "true"]

    by_run: dict[str, dict] = {}
    for r in rows:
        e = by_run.setdefault(r["run_id"], {"run_id": r["run_id"], "auctions": 0,
                                            "failed": 0})
        e["auctions"] += 1
        if str(r.get("ok", "")).lower() != "true":
            e["failed"] += 1

    stats: list[dict] = []
    for n in sorted({int(float(r["n_nodes"])) for r in ok}):
        group = [r for r in ok if int(float(r["n_nodes"])) == n]
        entry = {"n_nodes": n, "n_auctions": len(group),
                 "n_runs": len({r["run_id"] for r in group}),
                 "bids": int(statistics.median(
                     [float(r["n_eligible"]) for r in group]))}
        for key, _label in METRICS:
            vals = [v for v in (_num(r, key) for r in group) if v is not None]
            entry[f"{key}_mean"] = round(statistics.mean(vals), 1) if vals else ""
            entry[f"{key}_sd"] = (round(statistics.stdev(vals), 1)
                                  if len(vals) > 1 else "")
            entry[f"{key}_min"] = round(min(vals), 1) if vals else ""
            entry[f"{key}_max"] = round(max(vals), 1) if vals else ""
        stats.append(entry)
    return stats, sorted(by_run.values(), key=lambda e: e["run_id"])


def markdown(stats: list[dict]) -> str:
    head = "| nodes | auctions | bids | " + " | ".join(l for _k, l in METRICS) + " |"
    rule = "|---" * (3 + len(METRICS)) + "|"
    lines = [head, rule]
    for s in stats:
        cells = [f"{s[f'{k}_mean']} ± {s[f'{k}_sd']}" for k, _l in METRICS]
        lines.append(f"| {s['n_nodes']} | {s['n_auctions']} | {s['bids']} | "
                     + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="discovery.summarize", description=__doc__.splitlines()[0])
    p.add_argument("--experiment", default="exp2-auction-convergence")
    p.add_argument("--results-dir", default=None)
    args = p.parse_args(argv)

    rows = load_runs(args.experiment, args.results_dir)
    if not rows:
        print(f"no runs found for {args.experiment!r}")
        return 1
    stats, sources = summarize(rows)
    failed = sum(s["failed"] for s in sources)

    with RunLog(f"{args.experiment}-summary",
                {"experiment": args.experiment, "n_runs": len(sources),
                 "n_auctions": len(rows), "n_failed": failed}) as log:
        log.write_table("summary", stats)
        log.write_table("sources", sources)
        log.write_json("markdown", {"table": markdown(stats)})
        for s in sources:
            if s["failed"]:
                log.drop(s["run_id"], f"{s['failed']} auction(s) did not succeed")
        print(f"pooled {len(rows)} auctions from {len(sources)} runs "
              f"({failed} failed, excluded from the statistics)\n")
        print(markdown(stats))
        print(f"\nwritten to {log.dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
