# Experiment protocol

Four experiments produce every number in the paper and in Chapter 8 of the report.
This file is the protocol; `experiments/run_all.py` is the implementation. If the two
disagree, the code is wrong — fix the code, not this document.

## Ground rules

These exist because the Phase-1 run violated all four and its results could not be reproduced.

1. **Every run writes to its own directory.** `RunLog` creates `docs/results/<experiment>-<UTC>/`
   containing `config.json` (full config snapshot + git SHA + hostname), `manifest.json`
   (row counts, elapsed time, dropped cases, status) and one CSV per table. Nothing ever
   writes to a shared filename and nothing ever deletes a previous run.
2. **Provenance travels with the row, not with the prose.** Every result row records the
   backend and the model that actually served it, read back from the client — never the
   CLI argument, which can differ from what ran. The Phase-1 run's generator model
   (`allam-2-7b`) appears in no data file, which is why its precision figure cannot be
   attributed to anything.
3. **Degraded paths are recorded or they raise.** No silent fallback. A judge that cannot be
   reached yields `verdict=error`, counted separately — never `fail`, which would make an
   outage look like perfect fraud detection.
4. **Dropped cases are reported.** `manifest.json` carries `n_dropped` and the reason for each.
   Never silently under-report N.

## Reproducing everything

```bash
./setup.sh                                  # toolchain, venv, ollama model
.venv/bin/python experiments/run_all.py     # all four, ~20-30 min on CPU
.venv/bin/python experiments/make_figures.py  # regenerates docs/figures/*.png from the CSVs
```

Individual experiments take `--help`. Nothing requires an API key; nothing requires a GPU.

---

## Experiment 1 — Latency (time to first token)

**Question.** Does an edge node meet Objective 7's sub-second TTFT, and what does the
warm/cold split cost?

**Why TTFT and not total latency.** TTFT is what a streaming user perceives, and it is the
only latency figure the objective commits to. It is measurable only from a streaming
runtime: the Phase-1 engine set `stream=False`, so total latency was the only thing it could
observe and Objective 7 was never actually tested. TTFT here is the wall-clock from request
dispatch to the first chunk carrying a non-empty `response` field.

**Method.** For each model and each node: one discarded warm-up, then N >= 20 trials.
Report mean, median and p95. Separately, evict the model (`ollama stop`) and time a cold
first request; repeat >= 5 times. Token counts come from the runtime's `eval_count`, never
from `len(output.split())`.

**Baseline.** A hosted OpenAI-compatible endpoint over the same prompts. With no API key
configured the baseline is **skipped and recorded as skipped** — it is never estimated.

**Outputs.** `latency.csv` (per trial), `latency_summary.csv` (per model/node/state),
figure `fig_ttft.png`.

**Reporting rule.** Cold and warm must be reported together. Warm TTFT alone is a true number
presented misleadingly, and the cold figure is roughly an order of magnitude larger.

---

## Experiment 2 — Auction convergence

**Question.** How long does the sealed-bid second-price auction take to clear as the network
grows, and where does the time actually go?

**Method.** Launch N ∈ {3, 4, 5} nodes as separate OS processes. Wait for the GossipSub mesh
to form (measured, not assumed). The requester publishes a signed `JobRequest`; providers bid;
the requester closes the window and publishes the `JobAward`. Record, per trial:
mesh formation time, first-bid latency, bid count, and broadcast→award wall-clock.
Repeat >= 10 trials per N.

**Controls.** Fixed bid window (`BID_WINDOW_S`), fixed model, identical hardware — so the
independent variable really is node count. Convergence excludes the bid window itself, which
is a constant by construction; report both with and without it.

**Outputs.** `auction.csv`, `auction_summary.csv`, figure `fig_auction.png`.

**Threat to validity, stated in the paper.** All nodes run on one machine, so this measures
protocol and scheduling overhead, not wide-area network latency. A 5-process run on one host
is not a 5-machine deployment and must not be described as one.

---

## Experiment 3 — Verification accuracy

**Question.** How reliably does an LLM-as-a-Judge separate honest inference from fraudulent
inference, and at what false-positive cost to honest providers?

**Method.** A TruthfulQA subset of N questions, each in five conditions: honest, plus four
injected corruptions (`swap_incorrect`, `negate`, `hallucinate_entity`, `random_topic`).
The judge scores 1–5 and returns pass / fail / **error**.

**Honest-answer source is an explicit flag,** recorded in every row:
- `reference` — TruthfulQA's `best_answer`. Clean labels, but trivially easy; the harness
  prints that caveat and the paper must repeat it.
- `local` — real generation from the local Ollama node. The honest default: it is what an
  actual edge node would return.
- `groq` — a hosted generator, for comparison with the Phase-1 run.

**Fraud validity check.** A corruption that accidentally yields a *true* statement is dropped
and logged, not counted as a missed detection. The Phase-1 run injected "ugly ducklings become
swans when they grow up" as fraud; it is simply true, and scoring it as a miss was wrong.

**Metrics.** TP/FP/TN/FN, precision, recall, F1, accuracy, and mean score per condition —
computed **per strategy against that strategy's own denominators**. The Phase-1 harness reused
the global honest set for every strategy row, which is why all four rows reported an identical
FP=15/TN=5.

**Two precisions are reported, not one.** The raw precision over the natural 1:4 honest-to-fraud
design, *and* a class-balanced precision. The Phase-1 headline of 83.87% was an artifact of that
imbalance; balanced, the same data gives roughly 57%. Report both or neither.

**Errors are their own column.** Never folded into fail.

**Outputs.** `verification.csv`, `verification_summary.csv`, figures `fig_verification.png`,
`fig_score_dist.png`.

---

## Experiment 4 — Cost and settlement

**Question.** What does a verified inference cost on the grid versus a centralised API, once
verification overhead and slashing are accounted for?

**Method.** Replay the settled jobs from Experiments 1–3 through the ledger. Cost per 1k
tokens on the grid = clearing price (second price, in GRID, converted at `GRID_USD`) divided by
tokens delivered, **plus the amortised verification cost** — the judge is itself an inference
call, incurred on `SAMPLE_RATE` of jobs, and omitting it would flatter the grid. Compare against
`CENTRALIZED_USD_PER_1K_TOKENS`. Report on-chain gas per settlement from the deployed contracts.

**Also measured.** Value conservation: across every settled and slashed job, total in must equal
total out across provider payouts, requester refunds, validator rewards and treasury. This is
asserted, not assumed — the Phase-1 simulation did not conserve value at all (a paid provider's
stake was unchanged, because nobody was ever actually paid).

**Outputs.** `settlement.csv`, `cost_summary.csv`, figure `fig_cost.png`.

**Honesty note for the paper.** GRID has no market price. Any dollar figure is a notional
conversion at a stated rate and must be labelled as such — it is a cost *model*, not a
market observation.

---

## What these experiments do not show

State this in the paper's threats-to-validity section rather than waiting to be asked:

- One physical machine. No wide-area latency, no NAT traversal, no real network churn.
- A local EVM chain. Real gas *semantics* and real measured gas, but no mainnet fee market
  and no finality under contention.
- A local Merkle-committed DA layer, not Celestia. The binding property is real; the
  availability guarantee of a decentralised validator set is not.
- One judge model, un-finetuned. Judge accuracy is a lower bound, and judge
  self-consistency under paraphrase is measured separately (`verification/paraphrase_check.py`)
  precisely because a single verdict decides real money.
- Small N. Every reported figure carries its N and, where meaningful, a confidence interval.
