# Cited experiment runs

Result runs are not committed by default: they accumulate quickly and most are smoke
runs. The directories kept here are the ones Chapter 8 of the report cites, so
every number in the report can be traced to the run and the commit that produced it.

Each directory holds `config.json` (the full configuration snapshot plus the git SHA
and hostname), `manifest.json` (row counts, elapsed time, dropped cases, status), and
one CSV or JSON summary per table.

| Run | Experiment | Reported in |
|---|---|---|
| `inference-benchmark-20260902T120811Z` | 1 — latency | Table 8.1, Figure 8.1 |
| `exp2-auction-convergence-summary-20260902T110609Z` | 2 — auction | Table 8.2, Figure 8.2 |
| `verification-20260902T121801Z` | 3 — verification | Tables 8.3, 8.12; Figures 8.3, 8.4 |
| `paraphrase-20260902T133202Z` | 3 — judge self-consistency | Table 8.4, Figure 8.5 |
| `settlement-onchain-20260902T120752Z` | settlement on chain | Tables 8.5, 8.6; Figure 8.6 |
| `cost-20260902T123714Z` | 4 — cost | Table 8.7, Figure 8.7 |
| `weights-20260902T170213Z` | weight distribution | Tables 8.8, 8.9 |
| `exp2-swarm-containers-20260902T170808Z` | container topology, 0 ms | Table 8.10, Figure 8.8 |
| `exp2-swarm-netem-10ms-20260902T164741Z` | container topology, 10 ms | Table 8.10, Figure 8.8 |
| `exp2-swarm-netem-25ms-20260902T164817Z` | container topology, 25 ms | Table 8.10, Figure 8.8 |
| `exp2-swarm-netem-50ms-20260902T164859Z` | container topology, 50 ms | Table 8.10, Figure 8.8 |
| `swarm-baseline-single-host-20260902T164542Z` | single-host baseline for §8.3.5 | §8.3.5 |
| `judge-panel-20260902T173031Z` | judge panel and quorum | Table 8.11, Figure 8.9 |

`index.jsonl` lists every run ever made, including the ones not kept here, with its
status and row counts — so a reader can see how many runs there were, not only the
ones that are cited.

To regenerate the tables and figures from these runs:

```bash
make paper      # docs/report/generated/*.md
make figures    # docs/figures/fig_*.png
```

The `da/` subdirectory of a run holds copies of the data-availability blobs. Those are
regenerable and are not committed.
