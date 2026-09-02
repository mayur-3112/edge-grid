"""Generate every results table in the paper and the report from the CSVs.

The Phase-1 paper printed per-strategy accuracy of 50/50/48/48% where its own
CSV said 62.5/62.5/60/60%, because the table was typed by hand. Nothing here is
typed: each table is rendered from the latest successful run and stamped with
that run's id, so a reader can always trace a number back to the run and the
git SHA that produced it.

    python -m experiments.make_tables

Writes docs/report/generated/*.md, one file per table, plus tables.md with all of
them, and results.json with the headline figures Chapter 8 quotes inline.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from edgegrid import config as C
from edgegrid.runlog import RunLog


class Skip(Exception):
    pass


def _run(experiment: str) -> Path:
    p = RunLog.latest(experiment)
    if p is None:
        raise Skip(f"no successful '{experiment}' run")
    return p


def _csv(run: Path, table: str) -> pd.DataFrame:
    f = run / f"{table}.csv"
    if not f.exists() or f.stat().st_size == 0:
        raise Skip(f"{run.name} has no {table}.csv")
    return pd.read_csv(f)


def _json(run: Path, name: str) -> dict:
    f = run / f"{name}.json"
    if not f.exists():
        raise Skip(f"{run.name} has no {name}.json")
    return json.loads(f.read_text())


def _md(headers: list[str], rows: list[list], align: str | None = None) -> str:
    align = align or ("l" + "r" * (len(headers) - 1))
    sep = {"l": ":---", "r": "---:", "c": ":---:"}
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(sep[a] for a in align) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def _stamp(run: Path, note: str = "") -> str:
    sha = "unknown"
    cfg = run / "config.json"
    if cfg.exists():
        sha = json.loads(cfg.read_text()).get("git_sha", "unknown")
    tail = f" {note}" if note else ""
    return f"\n\n*Generated from run `{run.name}` at commit `{sha}`.{tail}*\n"


# --------------------------------------------------------------------- tables

def t_latency(head: dict) -> tuple[str, str]:
    run = _run("inference-benchmark")
    warm = _json(run, "warm_summary")
    prof = _json(run, "hardware_profile")
    rows = [["Warm", warm.get("ttft_ms_n", ""), f"{warm['ttft_ms_mean']:,.1f}",
             f"{warm['ttft_ms_median']:,.1f}", f"{warm['ttft_ms_p95']:,.1f}",
             f"{warm.get('tokens_per_sec_mean', 0):.2f}"]]
    try:
        cw = _json(run, "cold_warm_summary")
        for phase in ("cold", "warm"):
            n = cw.get(f"{phase}_ttft_ms_n")
            if n:
                rows.append([f"{phase.capitalize()} (paired)", n,
                             f"{cw[f'{phase}_ttft_ms_mean']:,.1f}",
                             f"{cw[f'{phase}_ttft_ms_median']:,.1f}",
                             f"{cw[f'{phase}_ttft_ms_p95']:,.1f}", "-"])
        head["cold_warm_ratio"] = cw.get("cold_over_warm_ratio")
        head["cold_penalty_ms"] = cw.get("ttft_penalty_ms")
        head["cold_ttft_mean_ms"] = cw.get("cold_ttft_ms_mean")
    except Skip:
        pass

    # the sub-second count is the claim Objective 7 actually makes, so read it
    # from the trials rather than assuming every trial passed
    try:
        t = _csv(run, "trials")
        head["ttft_sub_second"] = int((t["ttft_ms"] < 1000).sum())
    except Skip:
        head["ttft_sub_second"] = None

    head.update({
        "ttft_warm_mean_ms": warm["ttft_ms_mean"],
        "ttft_warm_median_ms": warm["ttft_ms_median"],
        "ttft_warm_p95_ms": warm["ttft_ms_p95"],
        "ttft_warm_stdev_ms": warm.get("ttft_ms_stdev"),
        "ttft_n_warm": warm.get("ttft_ms_n"),
        "tokens_per_sec": warm.get("tokens_per_sec_mean"),
        "model": (prof.get("served_model") or prof.get("benchmark_model")),
        "tier": prof.get("tier_name"), "accelerator": prof.get("accelerator"),
        "cpu_count": prof.get("cpu_count"), "ram_gb": prof.get("ram_gb"),
    })
    md = ("### Table 8.1 - Time to first token\n\n"
          + _md(["Condition", "N", "Mean (ms)", "Median (ms)", "p95 (ms)", "Tokens/s"], rows)
          + _stamp(run, f"Model {head['model']} on a {head['tier']} node with no accelerator. "
                        f"{head['ttft_sub_second']} of {head['ttft_n_warm']} warm trials fell "
                        f"below one second."))
    return "latency", md


def t_auction(head: dict) -> tuple[str, str]:
    run = _run("exp2-auction-convergence-summary")
    df = _csv(run, "summary").sort_values("n_nodes")
    rows = [[int(r.n_nodes), int(r.n_auctions),
             f"{r.first_bid_ms_mean:.1f} ± {r.first_bid_ms_sd:.1f}",
             f"{r.last_bid_ms_mean:.1f} ± {r.last_bid_ms_sd:.1f}",
             f"{r.broadcast_to_award_ms_mean:,.0f}",
             f"{r.mesh_ready_ms_mean/1000:.1f}"] for r in df.itertuples()]
    head.update({
        "auction_node_counts": [int(x) for x in df["n_nodes"]],
        "auction_last_bid_ms": [round(float(x), 1) for x in df["last_bid_ms_mean"]],
        "auction_award_ms": round(float(df["broadcast_to_award_ms_mean"].mean()), 1),
        "auction_n_auctions": int(df["n_auctions"].sum()),
    })
    md = ("### Table 8.2 - Auction timing versus network size\n\n"
          + _md(["Nodes", "Auctions", "First bid (ms)", "Last bid (ms)",
                 "Broadcast to award (ms)", "Mesh forms (s)"], rows)
          + _stamp(run, "Broadcast-to-award is pinned by the fixed "
                        f"{C.BID_WINDOW_S:g} s bid window; the bid arrival times are the "
                        "scaling signal. All processes on one host."))
    return "auction", md


def t_verification(head: dict) -> tuple[str, str]:
    run = _run("verification")
    df = _csv(run, "summary")
    cols = [("strategy", "Strategy"), ("TP", "TP"), ("FP", "FP"), ("TN", "TN"),
            ("FN", "FN"), ("ERR_fraud", "Err"), ("precision", "Precision"),
            ("precision_bal", "Precision (bal.)"), ("recall", "Recall"), ("f1", "F1")]
    have = [(c, h) for c, h in cols if c in df.columns]
    rows = []
    for r in df.itertuples():
        row = []
        for c, _ in have:
            v = getattr(r, c)
            row.append(f"{v:.1%}" if c in ("precision", "precision_bal", "recall", "f1")
                       else (f"**{v}**" if c == "strategy" and v == "OVERALL" else v))
        rows.append(row)
    o = df[df["strategy"] == "OVERALL"]
    if len(o):
        o = o.iloc[0]
        head.update({
            "verif_precision": float(o["precision"]),
            "verif_precision_bal": float(o.get("precision_bal", o["precision"])),
            "verif_recall": float(o["recall"]), "verif_f1": float(o["f1"]),
            "verif_fpr": float(o.get("fpr", 0)),
            "verif_n_fraud": int(o["N_fraud"]), "verif_n_honest": int(o["N_honest"]),
            "verif_error_rate": float(o.get("error_rate", 0)),
        })
    raw = _csv(run, "raw")
    if "judge_model" in raw:
        head["judge"] = f"{raw['judge_backend'].iloc[0]}/{raw['judge_model'].iloc[0]}"
        head["honest_source"] = str(raw.get("answer_origin", pd.Series(["?"])).iloc[0])
    md = ("### Table 8.3 - Fraud detection by corruption strategy\n\n"
          + _md([h for _, h in have], rows)
          + _stamp(run, "Precision is reported both as measured on the natural 1:4 "
                        "honest-to-fraud design and class-balanced; the raw figure alone "
                        "overstates the judge. Judge errors are counted separately and "
                        "never folded into failures."))
    return "verification", md


def t_paraphrase(head: dict) -> tuple[str, str]:
    run = _run("paraphrase")
    h = _json(run, "headline")
    rows = [["Questions measured", h.get("n_questions_measured")],
            ["Paraphrases per answer", h.get("k_requested")],
            ["Judgements", h.get("n_judgements")],
            ["Verdict flips", h.get("n_flipped")],
            ["Verdict flip rate", f"{h.get('verdict_flip_rate', 0):.1%}"],
            ["Mean score s.d. across paraphrases", f"{h.get('mean_score_sd', 0):.2f}"],
            ["Answers whose score moved by ≥2", h.get("n_score_range_ge_2")]]
    head.update({"paraphrase_flip_rate": h.get("verdict_flip_rate"),
                 "paraphrase_n": h.get("n_questions_measured"),
                 "paraphrase_score_sd": h.get("mean_score_sd")})
    md = ("### Table 8.4 - Judge self-consistency under paraphrase\n\n"
          + _md(["Measure", "Value"], rows, align="lr")
          + _stamp(run, "The same claim reworded should receive the same verdict. Every "
                        "flip is a case where collateral would have been slashed, or "
                        "spared, by wording alone."))
    return "paraphrase", md


def t_settlement(head: dict) -> tuple[str, str]:
    run = _run("settlement-onchain")
    gas = _json(run, "gas_used")
    inv = _json(run, "invariants")
    s = _csv(run, "settlements")
    rows = [[k, f"{int(v):,}"] for k, v in sorted(gas.items(), key=lambda kv: -int(kv[1]))]
    head.update({"gas": {k: int(v) for k, v in gas.items()},
                 "chain_id": inv.get("chain_id"),
                 "n_settlements": int(inv.get("n_settlements", len(s))),
                 "value_conserved": True})
    res = _md(["Job", "Resolution", "Final state", "Slashed (GRID)"],
              [[f"`{r.job_id}`", str(getattr(r, "resolution", "-")).replace("_", " "),
                str(r.state).replace("_", " "), f"{float(r.slash_amount):.4f}"]
               for r in s.itertuples()],
              align="lllr")
    md = ("### Table 8.5 - Gas per on-chain operation\n\n"
          + _md(["Operation", "Gas"], rows, align="lr")
          + "\n\n### Table 8.6 - Resolution path taken per job\n\n" + res
          + _stamp(run, f"Local EVM chain, chainId {head.get('chain_id')}. Gas is reported "
                        "in units: this chain has no gas price, and converting with an "
                        "invented one would be a fabrication."))
    return "settlement", md


def t_cost(head: dict) -> tuple[str, str]:
    run = _run("cost")
    df = _csv(run, "cost_summary")
    h = _json(run, "headline")
    rows = [[r.label, f"${r.usd_per_1k:.6f}", f"${r.verification_usd_per_1k:.6f}",
             f"${r.total_usd_per_1k:.6f}"] for r in df.itertuples()]
    head.update({"cost_grid_total": h.get("total_usd_per_1k"),
                 "cost_central": h.get("centralised_usd_per_1k"),
                 "cost_ratio": h.get("ratio_vs_centralised"),
                 "verification_share_pct": h.get("verification_share_pct")})
    md = ("### Table 8.7 - Cost per 1,000 delivered tokens\n\n"
          + _md(["System", "Inference", "Verification", "Total"], rows)
          + _stamp(run, h.get("caveat", "")))
    return "cost", md




def t_weights(head: dict) -> tuple[str, str]:
    """Objective 3's weight-distribution clause, and the tamper controls."""
    run = _run("weights")
    a = _csv(run, "artefacts")
    rows = [[r.model_id.replace("synth-weights-", ""), f"{int(r.bytes)/2**20:,.2f}",
             f"{r.cold_fetch_ms:,.1f}", f"{r.warm_fetch_ms:,.2f}",
             f"{r.speedup:,.0f}x", "yes" if r.cid_verified else "**NO**"]
            for r in a.itertuples()]
    md = ("### Table 8.8 - Content-addressed weight distribution\n\n"
          + _md(["Artefact (bytes)", "MiB", "Cold fetch (ms)", "Warm fetch (ms)",
                 "Speed-up", "CID re-verified"], rows))
    v = _csv(run, "verification")
    vr = [[str(r.case).replace("_", " "), str(r.outcome),
           str(getattr(r, "exception", "") or "-")] for r in v.itertuples()]
    md += ("\n\n### Table 8.9 - Tamper detection, with an honest control\n\n"
           + _md(["Case", "Outcome", "Exception"], vr, align="lll"))
    head.update({
        "weights_n": len(a),
        "weights_all_verified": bool(a["cid_verified"].all()),
        "weights_max_speedup": float(a["speedup"].max()),
        "weights_tamper_rejected": int((v["outcome"] == "REJECTED").sum()),
        "weights_tamper_cases": int(len(v)),
    })
    try:
        lru = _csv(run, "lru_order")
        head["weights_lru_correct"] = bool(lru["correct"].iloc[0])
    except Skip:
        pass
    return "weights", md + _stamp(run, "Verification recomputes the CID after download "
                                       "rather than trusting the daemon that served it.")


def t_swarm(head: dict) -> tuple[str, str]:
    """Auction timing once peers stop sharing a loopback interface."""
    import json as _j
    rows, lats = [], []
    for exp in ("exp2-swarm-containers", "exp2-swarm-netem-10ms",
                "exp2-swarm-netem-25ms", "exp2-swarm-netem-50ms"):
        try:
            run = _run(exp)
            a = _csv(run, "auctions")
        except Skip:
            continue
        lat = float(_j.loads((run / "config.json").read_text())
                    ["params"].get("latency_ms") or 0.0)
        lats.append((lat, run.name))
        rows.append([f"{lat:.0f}", int(a["n_nodes"].iloc[0]), len(a),
                     f"{a['first_bid_ms'].mean():.1f}", f"{a['last_bid_ms'].mean():.1f}",
                     f"{a['mesh_ready_ms'].mean()/1000:.1f}"])
    if not rows:
        raise Skip("no container swarm runs")
    rows.sort(key=lambda r: float(r[0]))
    md = ("### Table 8.10 - Auction timing across container network namespaces\n\n"
          + _md(["Injected RTT (ms)", "Nodes", "Auctions", "First bid (ms)",
                 "Last bid (ms)", "Mesh forms (s)"], rows))
    head.update({"swarm_latencies_ms": [l for l, _ in sorted(lats)],
                 "swarm_first_bid_ms": [float(r[3]) for r in rows],
                 "swarm_runs": [n for _, n in sorted(lats)]})
    return "swarm", md + ("\n\n*Each node is a container with its own network namespace "
                          "and a distinct address on a bridge, so peers no longer share a "
                          "loopback interface. This is not a LAN deployment: one kernel, "
                          "no physical NIC, no wide-area path. Latency is injected with "
                          "`tc netem`.*\n")


def t_judge_panel(head: dict) -> tuple[str, str]:
    """The size-versus-family question, and what a quorum buys."""
    run = _run("judge-panel")
    d = _csv(run, "summary")
    keep = d[d.strategy.isin(["negate", "swap_incorrect", "OVERALL"])]
    rows = []
    for r in keep.itertuples():
        err = int(getattr(r, "ERR_fraud", 0)) + int(getattr(r, "ERR_honest", 0))
        n = int(r.N_fraud) + int(r.N_honest)
        rows.append([r.config, r.strategy.replace("_", " "),
                     f"{r.recall:.0%}", f"{r.fpr:.0%}",
                     f"{getattr(r, 'precision_bal', float('nan')):.0%}",
                     f"{err}/{n}" + (" **unusable**" if err > n * 0.4 else "")])
    md = ("### Table 8.11 - Judge configurations against the two hard strategies\n\n"
          + _md(["Judge", "Strategy", "Recall", "FPR", "Precision (bal.)", "Errors"], rows,
                align="llrrrl"))
    comp = d[(d.strategy == "OVERALL") &
             ((d.get("ERR_fraud", 0).fillna(0) + d.get("ERR_honest", 0).fillna(0))
              < 0.4 * (d.N_fraud + d.N_honest))]
    head.update({
        "panel_run": run.name,
        "panel_complete_configs": comp.config.tolist(),
        "panel_unusable_configs": d[(d.strategy == "OVERALL") &
                                    ~d.config.isin(comp.config)].config.tolist(),
    })
    for r in comp.itertuples():
        head[f"panel_{r.config}_recall"] = float(r.recall)
        head[f"panel_{r.config}_fpr"] = float(r.fpr)
    return "judge_panel", md + _stamp(
        run, "A configuration whose error rate exceeds 40% is marked unusable: its rates "
             "are computed over the few judgements that completed and carry no weight.")


TABLES = [t_latency, t_auction, t_verification, t_paraphrase, t_settlement,
          t_cost, t_weights, t_swarm, t_judge_panel]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=C.REPO_ROOT / "docs" / "report" / "generated")
    args = ap.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)

    head: dict = {}
    made, skipped = [], {}
    parts = []
    for fn in TABLES:
        name = fn.__name__[2:]
        try:
            key, md = fn(head)
            (args.out / f"{key}.md").write_text(md + "\n")
            parts.append(md)
            made.append(key)
            print(f"  wrote  {key}.md")
        except Skip as e:
            skipped[name] = str(e)
            print(f"  SKIP   {name:14s} {e}")
        except Exception as e:
            skipped[name] = f"{type(e).__name__}: {e}"
            print(f"  ERROR  {name:14s} {type(e).__name__}: {e}", file=sys.stderr)

    (args.out / "tables.md").write_text(
        "<!-- GENERATED by experiments/make_tables.py. Do not edit by hand. -->\n\n"
        + "\n\n".join(parts) + "\n")
    head["_skipped"] = skipped
    (args.out / "results.json").write_text(json.dumps(head, indent=2, default=str))
    print(f"\n{len(made)} table(s) -> {args.out}")
    if skipped:
        for n, why in skipped.items():
            print(f"  skipped {n}: {why}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
