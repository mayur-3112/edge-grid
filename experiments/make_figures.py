"""Regenerate every figure in the paper and the report from the result CSVs.

Figures are never drawn by hand and never carry a number that is not in a CSV.
The Phase-1 paper's results table diverged from its own data - it printed
per-strategy accuracy of 50/50/48/48% where the CSV said 62.5/62.5/60/60% -
because the table was typed. Everything here is generated, so that cannot recur.

Each figure reads the latest successful run of its experiment and prints which
run it used. A missing run is reported and skipped, never silently replaced with
placeholder data.

    python -m experiments.make_figures              # all figures
    python -m experiments.make_figures --only ttft  # one
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from edgegrid import config as C
from edgegrid.runlog import RunLog
from experiments import style
from experiments.style import BLUE, CATEGORICAL, BAD, HATCH, INK_2, MARKERS, RUST, TEAL


class Skip(Exception):
    """A figure whose experiment has not been run."""


def _run(experiment: str) -> Path:
    p = RunLog.latest(experiment)
    if p is None:
        raise Skip(f"no successful '{experiment}' run in {C.RESULTS_DIR}")
    return p


def _csv(run: Path, table: str) -> pd.DataFrame:
    f = run / f"{table}.csv"
    if not f.exists() or f.stat().st_size == 0:
        raise Skip(f"{run.name} has no {table}.csv")
    df = pd.read_csv(f)
    df.attrs["run"] = run.name
    return df


def _json(run: Path, name: str) -> dict:
    f = run / f"{name}.json"
    if not f.exists():
        raise Skip(f"{run.name} has no {name}.json")
    return json.loads(f.read_text())


# --------------------------------------------------------------------- 1. TTFT

def fig_ttft(out: Path) -> str:
    """Warm vs cold time-to-first-token against the sub-second objective.

    Log x-axis because cold is an order of magnitude slower; on a linear axis the
    warm distribution collapses to a line. The 1 s rule is drawn because
    Objective 7 is a threshold claim, and a threshold claim should be visible in
    its own figure.
    """
    run = _run("inference-benchmark")
    warm = _csv(run, "trials")
    warm = warm[warm["ttft_ms"].notna()]
    series = [("warm", warm["ttft_ms"].to_numpy(float))]
    try:
        cw = _csv(run, "cold_warm_trials")
        cold = cw.loc[cw["phase"].astype(str).str.lower() == "cold", "ttft_ms"].dropna()
        if len(cold):
            series.append(("cold", cold.to_numpy(float)))
    except Skip:
        pass
    if not series:
        raise Skip(f"{run.name}: no TTFT samples")

    fig, ax = plt.subplots(figsize=(7.2, 2.6))
    rng = np.random.default_rng(0)
    for i, (name, vals) in enumerate(series):
        y = len(series) - 1 - i
        bp = ax.boxplot(vals, positions=[y], orientation="horizontal", widths=0.42,
                        patch_artist=True, showfliers=False, zorder=2)
        bp["boxes"][0].set(facecolor=CATEGORICAL[i], alpha=0.32,
                           edgecolor=CATEGORICAL[i], linewidth=1.4, hatch=HATCH[i])
        for part in ("whiskers", "caps"):
            for a in bp[part]:
                a.set(color=CATEGORICAL[i], linewidth=1.2)
        bp["medians"][0].set(color=CATEGORICAL[i], linewidth=2.2)
        ax.scatter(vals, np.full_like(vals, y) + rng.uniform(-.11, .11, vals.size),
                   s=14, color=CATEGORICAL[i], alpha=.6, marker=MARKERS[i],
                   linewidths=0, zorder=3,
                   label=f"{name}  (n={vals.size}, median {np.median(vals):,.0f} ms)")

    ax.axvline(1000, color=INK_2, linestyle="--", linewidth=1.1, zorder=1)
    ax.annotate("1 s - Objective 7", xy=(1000, 0.97), xycoords=("data", "axes fraction"),
                xytext=(6, 0), textcoords="offset points", fontsize=8,
                color=INK_2, va="top", ha="left")
    ax.set_ylim(-0.62, len(series) - 0.38)
    ax.set_xscale("log")
    ax.set_yticks(range(len(series)))
    ax.set_yticklabels([n.capitalize() for n, _ in reversed(series)])
    ax.set_xlabel("Time to first token (ms, log scale)")
    ax.set_title("Time to first token, warm vs cold start")
    ax.grid(axis="y", visible=False)
    style.legend_top(ax, ncol=2)

    model = warm["served_model"].iloc[0] if "served_model" in warm else "n/a"
    sub = np.sum(series[0][1] < 1000)
    style.caption(ax, f"{model} - CPU only - {sub}/{series[0][1].size} warm trials under 1 s "
                      f"- box = IQR, line = median - run {run.name}")
    return style.finish(fig, out / "fig_ttft.png")


# ----------------------------------------------------------------- 2. auction

def fig_auction(out: Path) -> str:
    """Auction timing as the network grows.

    The bid window is a fixed constant by construction, so broadcast-to-award is
    pinned near it and carries no information about scaling. The bid arrival
    times underneath it are the real signal, and they are what is plotted; the
    window is drawn as a reference line so the reader can see why.
    """
    run = _run("exp2-auction-convergence-summary")
    df = _csv(run, "summary").sort_values("n_nodes")
    for c in ("n_nodes", "first_bid_ms_mean", "last_bid_ms_mean"):
        if c not in df:
            raise Skip(f"{run.name}: summary.csv lacks {c}")
    n = df["n_nodes"].to_numpy(int)

    fig, ax = plt.subplots(figsize=(6.8, 3.3))
    for i, (col, sd, lbl) in enumerate((
            ("first_bid_ms_mean", "first_bid_ms_sd", "first bid arrives"),
            ("last_bid_ms_mean", "last_bid_ms_sd", "last bid arrives (auction could close)"))):
        y = df[col].to_numpy(float)
        e = df[sd].to_numpy(float) if sd in df else np.zeros_like(y)
        ax.errorbar(n, y, yerr=e, color=CATEGORICAL[i], marker=MARKERS[i],
                    capsize=3, linewidth=2, label=lbl, zorder=3)
        # the two series sit close together on a log axis, so their direct
        # labels are pushed to opposite sides or they collide
        dy = 11 if i else -15
        for x, v in zip(n, y):
            ax.annotate(f"{v:.0f} ms", xy=(x, v), xytext=(0, dy),
                        textcoords="offset points", ha="center", fontsize=8,
                        color=CATEGORICAL[i])
    if "broadcast_to_award_ms_mean" in df:
        w = float(df["broadcast_to_award_ms_mean"].mean())
        ax.axhline(w, color=INK_2, linestyle="--", linewidth=1.1, zorder=1)
        ax.annotate(f"broadcast to award, {w:,.0f} ms - fixed by the {C.BID_WINDOW_S:g} s bid window",
                    xy=(n[0], w), xytext=(0, -14), textcoords="offset points",
                    fontsize=8, color=INK_2, va="top")
    ax.set_yscale("log")
    ax.set_xticks(n)
    ax.set_xlabel("Nodes in the network")
    ax.set_ylabel("Milliseconds after broadcast (log scale)")
    ax.set_title("Bid arrival and auction close vs network size")
    style.legend_top(ax, ncol=2)
    runs = int(df["n_runs"].iloc[0]) if "n_runs" in df else 0
    auc = int(df["n_auctions"].sum()) if "n_auctions" in df else 0
    style.caption(ax, f"{auc} auctions, {runs} runs per node count - all processes on one "
                      f"host, so this is protocol overhead, not wide-area latency")
    return style.finish(fig, out / "fig_auction.png")


# ------------------------------------------------------------ 3. verification

def fig_verification(out: Path) -> str:
    """Detection performance per corruption strategy, with the honest cost.

    Precision is plotted twice - as measured on the natural 1:4 design, and
    class-balanced. Reporting only the first is how the Phase-1 result came to
    overstate the judge.
    """
    run = _run("verification")
    df = _csv(run, "summary")
    for c in ("strategy", "precision", "recall", "f1"):
        if c not in df:
            raise Skip(f"{run.name}: summary.csv lacks {c}")
    df = df[df["strategy"] != "OVERALL"].copy()
    if df.empty:
        raise Skip(f"{run.name}: no per-strategy rows")
    df = df.sort_values("recall")

    metrics = [("precision", "Precision"), ("recall", "Recall"), ("f1", "F1")]
    y = np.arange(len(df)); h = 0.26
    fig, ax = plt.subplots(figsize=(7.4, 0.78 * len(df) + 2.1))
    for i, (col, lbl) in enumerate(metrics):
        vals = df[col].to_numpy(float)
        ax.barh(y + (1 - i) * h, vals, height=h - 0.03, color=CATEGORICAL[i],
                hatch=HATCH[i], edgecolor="white", linewidth=1.2, label=lbl, zorder=2)
        for yy, v in zip(y + (1 - i) * h, vals):
            ax.annotate(f"{v:.0%}", xy=(v, yy), xytext=(4, 0), textcoords="offset points",
                        va="center", fontsize=7.8, color=INK_2)
    if "precision_bal" in df:
        ax.scatter(df["precision_bal"], y + h, marker="|", s=170, linewidths=1.8,
                   color=BAD, zorder=4, label="Precision, class-balanced")
    ax.set_yticks(y)
    ax.set_yticklabels([s.replace("_", " ") for s in df["strategy"]])
    ax.set_xlim(0, 1.14)
    ax.set_xticks(np.arange(0, 1.01, 0.25))
    ax.set_xticklabels([f"{v:.0%}" for v in np.arange(0, 1.01, 0.25)])
    ax.set_xlabel("Score")
    ax.set_title("Fraud detection by corruption strategy")
    ax.grid(axis="y", visible=False)
    style.legend_top(ax, ncol=4)

    bits = [f"run {run.name}"]
    if {"N_fraud", "N_honest"} <= set(df.columns):
        bits.append(f"N = {int(df['N_fraud'].sum())} fraud vs {int(df['N_honest'].iloc[0])} honest")
    if "error_rate" in df:
        bits.append(f"judge error rate {df['error_rate'].mean():.1%}")
    style.caption(ax, " - ".join(bits))
    return style.finish(fig, out / "fig_verification.png")


def fig_score_dist(out: Path) -> str:
    """Where the judge puts honest and fraudulent answers on the 1-5 scale.

    This is the figure that makes the false-positive problem legible: honest mass
    below the pass threshold is honest providers being slashed, however good the
    recall looks.
    """
    run = _run("verification")
    df = _csv(run, "raw")
    for c in ("score", "is_fraud"):
        if c not in df:
            raise Skip(f"{run.name}: raw.csv lacks {c}")
    df = df[df["score"].notna()].copy()
    df["is_fraud"] = df["is_fraud"].astype(str).str.lower().isin(("true", "1"))
    scores = [1, 2, 3, 4, 5]
    honest = [int(((~df["is_fraud"]) & (df["score"] == s)).sum()) for s in scores]
    fraud = [int((df["is_fraud"] & (df["score"] == s)).sum()) for s in scores]

    x = np.arange(len(scores)); w = 0.38
    fig, ax = plt.subplots(figsize=(6.8, 3.3))
    ax.bar(x - w / 2, honest, w, color=CATEGORICAL[0], hatch=HATCH[0], edgecolor="white",
           linewidth=1.2, label=f"honest (n={sum(honest)})", zorder=2)
    ax.bar(x + w / 2, fraud, w, color=CATEGORICAL[1], hatch=HATCH[1], edgecolor="white",
           linewidth=1.2, label=f"injected fraud (n={sum(fraud)})", zorder=2)

    ax.set_ylim(0, max(max(honest), max(fraud), 1) * 1.34)
    thr = int(df["pass_threshold"].iloc[0]) if "pass_threshold" in df else C.PASS_THRESHOLD
    ax.axvline(scores.index(thr) - 0.5, color=INK_2, linestyle="--", linewidth=1.1, zorder=1)
    ax.annotate(f"fail  |  pass  (threshold {thr})",
                xy=(scores.index(thr) - 0.5, 0.99), xycoords=("data", "axes fraction"),
                xytext=(5, 0), textcoords="offset points", fontsize=8, color=INK_2,
                va="top", ha="left")
    fp = sum(honest[: thr - 1])
    if sum(honest):
        ax.annotate(f"{fp} of {sum(honest)} honest answers fall below the threshold "
                    f"- a {fp / sum(honest):.0%} false-positive rate",
                    xy=(0.0, 1.10), xycoords="axes fraction", fontsize=8.5,
                    color=BAD, va="bottom", ha="left")
    ax.set_xticks(x); ax.set_xticklabels(scores)
    ax.set_xlabel("Judge quality score (1-5)")
    ax.set_ylabel("Answers")
    ax.set_title("Judge score distribution, honest vs fraudulent", pad=46)
    ax.grid(axis="x", visible=False)
    style.legend_top(ax, ncol=2)
    src = df["answer_origin"].iloc[0] if "answer_origin" in df else "?"
    style.caption(ax, f"honest answers from {src} - judge "
                      f"{df['judge_backend'].iloc[0]}/{df['judge_model'].iloc[0]} "
                      f"- run {run.name}")
    return style.finish(fig, out / "fig_score_dist.png")


# ------------------------------------------------------- 4. paraphrase stability

def fig_paraphrase(out: Path) -> str:
    """How often the judge changes its verdict when the same claim is reworded.

    A judge that is not self-consistent under paraphrase is a serious finding for
    a system that slashes real collateral on a single verdict, so the flip rate
    is stated as the headline and the per-question score spread beneath it.
    """
    run = _run("paraphrase")
    df = _csv(run, "per_question")
    head = _json(run, "headline")
    if "score_range" not in df:
        raise Skip(f"{run.name}: per_question.csv lacks score_range")
    df = df.sort_values("score_range", ascending=False).head(14)

    fig, ax = plt.subplots(figsize=(7.4, 0.42 * len(df) + 2.4))
    y = np.arange(len(df))
    flipped = df["flipped"].astype(str).str.lower().isin(("true", "1")).to_numpy()
    ax.barh(y, df["score_range"].to_numpy(float), height=0.6,
            color=[BAD if f else BLUE for f in flipped],
            hatch=["///" if f else "" for f in flipped],
            edgecolor="white", linewidth=1.1, zorder=2)
    for yy, v, f in zip(y, df["score_range"], flipped):
        ax.annotate(f"{v:.0f}" + ("  verdict flipped" if f else ""),
                    xy=(v, yy), xytext=(5, 0), textcoords="offset points",
                    va="center", fontsize=8, color=BAD if f else INK_2)
    ax.set_yticks(y)
    ax.set_yticklabels([str(q)[:52] + ("..." if len(str(q)) > 52 else "")
                        for q in df["question"]], fontsize=8)
    ax.set_xlabel("Spread in judge score across paraphrases of the same answer (1-5 scale)")
    ax.set_title("Judge self-consistency under paraphrase")
    ax.grid(axis="y", visible=False)
    ax.set_xlim(0, max(df["score_range"].max(), 1) * 1.35)

    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(facecolor=BLUE, label="verdict held"),
                       Patch(facecolor=BAD, hatch="///", label="verdict flipped")],
              loc="lower left", bbox_to_anchor=(0, 1.005), ncol=2, frameon=False,
              borderaxespad=0, handlelength=1.6)
    style.caption(ax, f"{head.get('n_questions_measured', '?')} questions x "
                      f"{head.get('k_requested', '?')} paraphrases - flip rate "
                      f"{head.get('verdict_flip_rate', 0):.1%} - judge "
                      f"{head.get('judge_backend')}/{head.get('judge_model')} - run {run.name}")
    return style.finish(fig, out / "fig_paraphrase.png")


# --------------------------------------------------------------- 5. gas + cost

def fig_gas(out: Path) -> str:
    """Gas per on-chain operation, ordered by cost.

    Reported in gas units, not dollars: a local chain has no gas price and no ETH
    price, and converting with invented ones would be a fabrication.
    """
    run = _run("settlement-onchain")
    gas = _json(run, "gas_used")
    items = sorted(((k, int(v)) for k, v in gas.items() if int(v) > 0),
                   key=lambda kv: kv[1])
    if not items:
        raise Skip(f"{run.name}: gas_used.json is empty")
    labels = [k for k, _ in items]
    vals = np.array([v for _, v in items], float)
    # the two fraud-proof paths are the ones a reader should notice
    fraud = {"proveDataMismatch", "submitVerdict"}
    colors = [RUST if l in fraud else BLUE for l in labels]

    fig, ax = plt.subplots(figsize=(7.2, 0.46 * len(items) + 2.0))
    y = np.arange(len(items))
    ax.barh(y, vals, height=0.62, color=colors,
            hatch=["///" if l in fraud else "" for l in labels],
            edgecolor="white", linewidth=1.1, zorder=2)
    for yy, v in zip(y, vals):
        ax.annotate(f"{v:,.0f}", xy=(v, yy), xytext=(5, 0), textcoords="offset points",
                    va="center", fontsize=8.2, color=INK_2)
    ax.set_yticks(y); ax.set_yticklabels(labels, fontsize=9)
    ax.set_xlim(0, vals.max() * 1.22)
    ax.set_xlabel("Gas used")
    ax.set_title("Gas per on-chain operation")
    ax.grid(axis="y", visible=False)
    from matplotlib.patches import Patch
    ax.legend(handles=[Patch(facecolor=BLUE, label="normal path"),
                       Patch(facecolor=RUST, hatch="///", label="fraud resolution")],
              loc="lower left", bbox_to_anchor=(0, 1.005), ncol=2, frameon=False,
              borderaxespad=0, handlelength=1.6)
    style.caption(ax, f"local EVM chain, chainId 31337 - gas units, not dollars: this chain "
                      f"has no gas price - run {run.name}")
    return style.finish(fig, out / "fig_gas.png")


def fig_cost(out: Path) -> str:
    """Cost per 1k tokens, grid vs centralised, with verification counted in."""
    run = _run("cost")
    df = _csv(run, "cost_summary").sort_values("total_usd_per_1k")
    labels = df["label"].tolist()
    base = df["usd_per_1k"].to_numpy(float)
    over = (df["verification_usd_per_1k"].to_numpy(float)
            if "verification_usd_per_1k" in df else np.zeros_like(base))

    y = np.arange(len(labels))
    fig, ax = plt.subplots(figsize=(7.2, 0.66 * len(labels) + 2.1))
    ax.barh(y, base, 0.5, color=CATEGORICAL[0], hatch=HATCH[0], edgecolor="white",
            linewidth=1.4, label="inference", zorder=2)
    if over.any():
        ax.barh(y, over, 0.5, left=base, color=CATEGORICAL[2], hatch=HATCH[2],
                edgecolor="white", linewidth=1.4, label="verification overhead", zorder=2)
    for yy, b, o in zip(y, base, over):
        ax.annotate(f"${b + o:,.6f}", xy=(b + o, yy), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=8.4, color=INK_2)
    ax.set_yticks(y); ax.set_yticklabels(labels)
    ax.set_xlim(0, (base + over).max() * 1.28)
    ax.set_xlabel("USD per 1,000 delivered tokens")
    ax.set_title("Cost per 1k tokens, verification included")
    ax.grid(axis="y", visible=False)
    if over.any():
        style.legend_top(ax, ncol=2)
    head = _json(run, "headline")
    style.caption(ax, f"GRID has no market price: dollar figures are a cost model at the "
                      f"notional rate GRID_USD={C.GRID_USD}, not a market observation "
                      f"- audit rate {head.get('sample_rate', C.SAMPLE_RATE):.0%} - run {run.name}")
    return style.finish(fig, out / "fig_cost.png")



def fig_swarm(out: Path) -> str:
    """Auction timing against injected round-trip latency.

    The single-host experiment could not produce this figure at all: with every
    peer on one loopback interface there is no link to delay. Each point here is
    a container with its own network namespace and `tc netem` on its interface,
    so the independent variable is a real property of a real link.
    """
    import json as _j
    pts = []
    for exp in ("exp2-swarm-containers", "exp2-swarm-netem-10ms",
                "exp2-swarm-netem-25ms", "exp2-swarm-netem-50ms"):
        try:
            run = _run(exp); a = _csv(run, "auctions")
        except Skip:
            continue
        lat = float(_j.loads((run / "config.json").read_text())
                    ["params"].get("latency_ms") or 0.0)
        pts.append((lat, a["first_bid_ms"].mean(), a["last_bid_ms"].mean(), len(a), run.name))
    if len(pts) < 2:
        raise Skip("need at least two container swarm runs to plot a response")
    pts.sort()
    x = np.array([p[0] for p in pts], float)
    first = np.array([p[1] for p in pts], float)
    last = np.array([p[2] for p in pts], float)

    fig, ax = plt.subplots(figsize=(6.8, 3.4))
    ax.plot(x, first, color=CATEGORICAL[0], marker=MARKERS[0], label="first bid arrives")
    ax.plot(x, last, color=CATEGORICAL[1], marker=MARKERS[1], label="last bid arrives")
    # A request and its reply each cross the delayed link, so the expected
    # slope is 2 ms of added latency per 1 ms of injected one-way delay.
    ax.plot(x, first[0] + 2 * x, color=INK_2, linestyle=":", linewidth=1.4,
            label="first bid + 2x injected delay (round trip)")
    for xi, yi in zip(x, last):
        ax.annotate(f"{yi:.0f}", xy=(xi, yi), xytext=(0, 9), textcoords="offset points",
                    ha="center", fontsize=8, color=CATEGORICAL[1])
    ax.set_xlabel("Injected one-way latency per link (ms, tc netem)")
    ax.set_ylabel("Milliseconds after broadcast")
    ax.set_title("Auction timing versus link latency, container topology")
    ax.set_xticks(x)
    ax.set_ylim(bottom=0)
    style.legend_top(ax, ncol=3)
    n = sum(p[3] for p in pts)
    style.caption(ax, f"{n} auctions over {len(pts)} latency settings, 3 nodes - each peer in "
                      f"its own network namespace with a distinct address; one kernel, so this "
                      f"is link delay, not a wide-area path")
    return style.finish(fig, out / "fig_swarm.png")


FIGURES = {
    "ttft": fig_ttft,
    "auction": fig_auction,
    "verification": fig_verification,
    "score_dist": fig_score_dist,
    "paraphrase": fig_paraphrase,
    "gas": fig_gas,
    "cost": fig_cost,
    "swarm": fig_swarm,
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--only", choices=sorted(FIGURES), action="append")
    ap.add_argument("--out", type=Path, default=C.FIGURES_DIR)
    args = ap.parse_args(argv)

    style.apply()
    args.out.mkdir(parents=True, exist_ok=True)
    made, skipped = [], {}
    for name in (args.only or list(FIGURES)):
        try:
            p = FIGURES[name](args.out)
            made.append(p)
            print(f"  drew   {name:12s} -> {Path(p).name}")
        except Skip as e:
            skipped[name] = str(e)
            print(f"  SKIP   {name:12s}  {e}")
        except Exception as e:
            skipped[name] = f"{type(e).__name__}: {e}"
            print(f"  ERROR  {name:12s}  {type(e).__name__}: {e}", file=sys.stderr)

    print(f"\n{len(made)} figure(s) -> {args.out}")
    if skipped:
        print(f"{len(skipped)} skipped:")
        for n, why in skipped.items():
            print(f"  - {n}: {why}")
    (args.out / "figures.json").write_text(json.dumps({"made": made, "skipped": skipped}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
