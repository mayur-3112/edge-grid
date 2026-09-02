# Chapter 8

# RESULTS AND DISCUSSION

This chapter reports what the implemented system was measured to do. It is organised around the
four experiments defined in `docs/EXPERIMENTS.md`, each of which produces a table and one or more
figures, followed by a settlement measurement taken against a live chain, an explicit revisiting
of the seven objectives of Chapter 3, and a statement of the threats to the validity of everything
reported here.

Three further measurements were taken after those four experiments were complete, and each is
reported alongside the experiment whose result it extends. Sections 8.2.4 and 8.2.5 measure
content-addressed model-weight distribution against a real IPFS daemon, which is the clause of
Objective 3 that was unimplemented when the four experiments were run. Section 8.3.5 repeats the
auction across containers that do not share a loopback interface, which removes the largest single
threat to the validity of Section 8.3 and makes a latency response measurable for the first time.
Sections 8.4.10 to 8.4.14 put the corruption set of Experiment 3 past four larger judges and a
quorum, which resolves a confound Section 8.4.7 was obliged to leave open — and, in doing so,
refutes a hypothesis this report itself advanced.

Two commitments govern the whole chapter. The first is that every figure quoted in the prose is
taken from a file in a timestamped run directory under `docs/results/`, and no figure is restated
from memory, rounded differently from the table it appears in, or estimated where it was not
measured. Where something was not measured, the chapter says so. The second is that the most
interesting result in this chapter is a negative one. The verification subsystem, which is the
component on which the project's central claim rests, was measured to have a serious and
structured weakness. Section 8.4 sets out that weakness in detail, argues for a specific
explanation of it, and derives three architectural consequences from it. It is placed at the
centre of the chapter rather than in a concluding caveat because it is the most useful thing the
experiments produced.

Tables 8.1 to 8.11 are emitted mechanically by `experiments/make_tables.py` directly from the
result CSVs, and are reproduced here verbatim including their provenance captions. Tables 8.12 and
8.13 are analysis tables compiled by hand from the same run directories; they are numbered after
the generated set, which is why Table 8.12 is referred to in Section 8.4 although its number falls
after tables that appear later in the chapter. Figures 8.1 to 8.8 are likewise numbered in the
order `experiments/make_figures.py` emits them rather than in order of appearance, so Figure 8.8
carries a number later than the section that discusses it; Figure 8.9 is produced by the
judge-panel harness and is numbered last.

---

## 8.1 Experimental Setup

### 8.1.1 Hardware and Runtime

Every measurement in this chapter was taken on a single development host. The host's own hardware
detector, described in Section 7.2.5, classified it as **Tier 1 (CPU)**: sixteen logical cores
across ten physical cores, 30.94 GB of RAM of which 17.85 GB was available at profiling time,
0.0 GB of addressable video memory, and an accelerator field of `none` with
`detected_by = "no accelerator probe matched"`. The platform string recorded in every
`config.json` is `Linux-7.0.0-30-generic-x86_64-with-glibc2.39`, the interpreter is CPython
3.12.3, and the git commit under which the four experiments executed is `37378fd`. The three later
measurements ran on the same host at later commits, recorded on their own rows: the
weight-distribution and container-topology runs at `75df836` and the judge-panel run at `f02eca6`.
The hardware profile is preserved at
`docs/results/inference-benchmark-20260902T120811Z/hardware_profile.json`.

The absence of an NVIDIA accelerator is the reason the vLLM and CUDA execution path declared in
the synopsis is out of scope, as recorded in Section 7.4.1, in row 3 of the substitution table in
Section 7.8, and in Table 1.1. No GPU
number appears anywhere in this chapter, because none could be produced on this machine, and an
implemented but never-executed CUDA path would be an unverified claim rather than a result.

The inference runtime is Ollama, reached over its streaming HTTP interface at
`http://localhost:11434`. The model served for every experiment is `qwen3-vl:2b-instruct`, and
the runtime's `served_models` field was read back and recorded on every trial so that the model
named on a row is the model the server reported rather than the model the command line requested.
The judge model for Experiment 3 is the same identifier reached through the same backend,
recorded on each verdict as `ollama` / `qwen3-vl:2b-instruct`. The consequence — that the judge
and the generator are the same model — is not incidental, and Section 8.4 treats it as the
central explanatory variable rather than as a footnote; the verification run's `headline.json`
records it explicitly as `"self_evaluation": true`.

The peer-to-peer layer is py-libp2p, running GossipSub and a Kademlia DHT between separate
operating-system processes on the one host, and — in the container runs of Section 8.3.5 — between
containers holding separate network namespaces on that same host. The chain is a local Hardhat EVM
node at `http://127.0.0.1:8545` with chain id 31337, carrying four Solidity 0.8.24 contracts
compiled with the optimiser enabled at 200 runs. The data-availability layer is the local
Merkle-committed store of Section 7.5.2, not Celestia. The weight store of Sections 8.2.4 and 8.2.5
is a real kubo IPFS daemon, version 0.43.0, running in Docker on this host and reached over its
HTTP API.

### 8.1.2 Parameters Held Constant

The following configuration values were identical across every run reported in this chapter, and
are snapshotted in full inside each run's `config.json`:

| Parameter | Value | Governs |
|---|---|---|
| `OLLAMA_MODEL` | `qwen3-vl:2b-instruct` | model served by every provider node |
| `JUDGE_BACKEND` / `JUDGE_MODEL` | `ollama` / `qwen3-vl:2b-instruct` | the verification judge |
| `PASS_THRESHOLD` | 3 | score-to-verdict conversion |
| `BID_WINDOW_S` | 2.0 | fixed auction bid window |
| `WARM_START_BONUS` | 0.15 | handicap on a warm bid's score |
| `SAMPLE_RATE` | 0.05 | production audit rate |
| `VALIDATOR_QUORUM` | 1 | validators required to agree |
| `CHALLENGE_WINDOW_S` | 3600 | window within which a commitment may be challenged |
| `MIN_STAKE` | 10.0 GRID | minimum active provider stake |
| `VALIDATOR_SLASH_SHARE` / `TREASURY_SLASH_SHARE` | 0.80 / 0.20 | slash distribution |
| `GRID_USD` | 0.001 | notional GRID conversion rate |
| `CENTRALIZED_USD_PER_1K_TOKENS` | 0.002 | centralised baseline list rate |

What was deliberately *varied*, and in only one experiment each, is the independent variable of
that experiment: node count in Experiment 2 (three, four, five), corruption strategy in
Experiment 3 (four strategies plus an honest control), and cache state in Experiment 1 (warm
versus cold). The three later measurements each add exactly one further independent variable:
artefact size in Section 8.2.4, injected one-way link delay in Section 8.3.5, and judge
configuration in Section 8.4.10, the last of which varies `JUDGE_MODEL` while holding the rubric,
the pass threshold and the eighty fraudulent strings fixed. Nothing else moved. In particular the model, the host, the bid window and the pass
threshold were not adjusted between conditions, which is what permits the differences reported
below to be attributed to the independent variable rather than to configuration drift.

Three parameters differ between the measurement harness and the production configuration, and the
difference is recorded in the run's own parameters rather than left for the reader to infer. The
verification harness audits every item (`audit_rate = 1.0`) because it is an instrument for
measuring judge accuracy, whereas the deployed sampler audits five per cent; the harness records
this as `audit_rate_note`. The validator pool in Experiment 3 was a single judge with quorum one,
which is a prototype configuration and not a recommended deployment configuration, for the reasons
given in Section 7.5.7. And the cost model of Experiment 4 amortises verification at the
production five per cent rate rather than at the harness rate, because it is modelling the
deployed system.

### 8.1.3 The Four Ground Rules

`docs/EXPERIMENTS.md` states four ground rules that the measurement code enforces. They exist
because the Phase-1 measurement run violated all four, and its results consequently could not be
reproduced, attributed, or in one case even interpreted. Each is restated here with the specific
failure it prevents.

**Rule 1 — every run writes to its own directory.** `RunLog` creates
`docs/results/<experiment>-<UTC timestamp>/` containing a full configuration snapshot with the git
SHA and hostname, a manifest carrying row counts and elapsed time, and one CSV per table. Nothing
writes to a shared filename and nothing deletes a previous run. This exists because a shared
output filename makes a result unattributable the moment the code changes: a reader cannot tell
whether a number came from the version of the system being described or from a later one, and the
directory listing under `docs/results/` — ninety-one run directories at the time of writing, of
which this chapter cites fifteen by name — is the evidence that no figure here was silently
overwritten.

**Rule 2 — provenance travels with the row, not with the prose.** Every result row records the
backend and the model that actually served it, read back from the client rather than taken from
the command-line argument, since the two differ whenever a name is aliased or a provider
substitutes a model. This exists because the Phase-1 generator model, `allam-2-7b`, appears in no
data file at all; its precision figure therefore cannot be attributed to any particular model, and
is not a measurement of anything nameable.

**Rule 3 — degraded paths are recorded or they raise.** There is no silent fallback anywhere in
the measurement path. A judge that cannot be reached yields `verdict = error`, counted in its own
column, never `fail`. This exists because folding an outage into `fail` makes an infrastructure
failure look like perfect fraud detection, and folding it into `pass` hands an adversary a
strategy consisting entirely of inducing judge failures. Both readings are wrong, and the
three-valued verdict of Section 7.5.6 is what makes "the system does not know" representable at
all. In the run reported in Section 8.4 the error count is zero across one hundred judge calls,
which is a result about this run rather than a property of the design.

**Rule 4 — dropped cases are reported.** Each `manifest.json` carries `n_dropped` and a reason for
every drop. This exists because silently under-reporting N inflates every rate computed from it.
The latency run of Section 8.2 records exactly one drop — the warm-up trial, discarded by design
because it conflates model loading with generation — and the verification run of Section 8.4
records `n_dropped: 0`, meaning that no corrupted item was quietly discarded for having failed to
be false. The Phase-1 run had no such mechanism, and injected as fraud a statement ("ugly ducklings
become swans when they grow up") that is simply true, then counted the judge's correct acceptance
of it as a missed detection.

### 8.1.4 Reproduction

The complete chain from a clean checkout to the tables and figures in this chapter is four
commands:

```bash
./setup.sh                                        # venv, python deps, hardhat, ollama model
.venv/bin/python -m pytest tests/ -q              # 346 tests
.venv/bin/python experiments/run_all.py           # all four experiments, ~20-30 min on CPU
.venv/bin/python -m experiments.make_tables       # -> docs/report/generated/tables.md
.venv/bin/python -m experiments.make_figures      # -> docs/figures/fig_*.png
```

The contract suite is run separately, since it requires the Node toolchain:

```bash
cd contracts && npx hardhat compile && npx hardhat test
cd contracts && npx hardhat run scripts/deploy.js --network localhost
```

Nothing in the sequence requires an API key and nothing requires a GPU. At the commit reported
here the Python suite reports **346 passed** and the Hardhat suite comprises **39
tests** across the four contract test files, all passing. The count of the Python suite by module
is: `test_verification` 70, `test_inference` 57, `test_gateway` 52, `test_ledger` 45,
`test_discovery` 35, `test_foundation` 35, `test_market` 31, `test_chain` 21.

A passing test suite is evidence that the code does what its author intended, and is not evidence
that the system performs well. Section 8.6 marks no objective as met on the strength of tests
alone.

---

## 8.2 Experiment 1 — Latency

### 8.2.1 Method and Result

The question is the one Objective 7 commits to: does an edge node deliver a sub-second time to
first token, and what does the warm/cold distinction cost? Time to first token is measured, as
defined in Section 7.4.2, as the wall-clock interval from request dispatch to the arrival of the
first streamed chunk carrying a non-empty `response` field; a terminal chunk with an empty
response is explicitly not counted. Token counts are taken from the runtime's own `eval_count`
and never estimated from whitespace.

The run reported here is `inference-benchmark-20260902T120811Z`. One warm-up trial was issued and
discarded, then twenty warm trials were recorded on the prompt "Explain what gravity is in two
sentences." with a 64-token ceiling. Separately, five evict-and-reload pairs were timed: the model
was evicted by issuing a generation request carrying `keep_alive: 0`, which is the HTTP equivalent
of `ollama stop` and keeps the harness free of a command-line dependency, a cold request was
timed, and a warm request immediately following it was timed against the same prompt, so that the
two members of each pair differ in cache state and in nothing else. The eviction is verified rather
than assumed: the client polls the runtime's list of resident models and raises if the model is
still loaded, so a warm request can never be recorded as a cold one. The whole run took 174.6 s and dropped one case, the warm-up,
with the reason recorded.

**Table 8.1 — Time to first token**

| Condition | N | Mean (ms) | Median (ms) | p95 (ms) | Tokens/s |
|:---|---:|---:|---:|---:|---:|
| Warm | 20 | 609.6 | 587.9 | 723.6 | 12.86 |
| Cold (paired) | 5 | 7,963.8 | 7,809.9 | 8,231.4 | - |
| Warm (paired) | 5 | 653.7 | 600.8 | 789.9 | - |

*Generated from run `inference-benchmark-20260902T120811Z` at commit `37378fd`. Model
qwen3-vl:2b-instruct on a CPU node with no accelerator. 20 of 20 warm trials fell below one
second.*

**Figure 8.1** — `docs/figures/fig_ttft.png` — Time to first token, warm versus cold start, on a logarithmic time axis.

**Figure 8.1** places every warm and cold observation on one logarithmic axis. The separation
between the two clusters is close to an order of magnitude and there is no overlap whatever
between them.

The warm distribution is tight. Its standard deviation is 75.66 ms on a mean of 609.57 ms, the
minimum observed warm TTFT is 534.75 ms and the maximum is 836.98 ms, so the entire warm sample —
not merely its ninety-fifth percentile — lies below one second. Sustained throughput averaged
12.86 tokens per second with a standard deviation of 0.64, ranging from 12.01 to 13.87, and the
mean completion length was 45.6 tokens, giving a mean total request duration of 4,165.48 ms. Host
CPU utilisation during generation averaged 50.84 per cent.

### 8.2.2 Objective 7, Answered

**Objective 7's latency requirement is met on this hardware in the warm case: twenty of twenty
warm trials produced a first token in under one second, with a mean of 609.6 ms and a
ninety-fifth percentile of 723.6 ms.**

That sentence is the whole of the claim, and the qualifications inside it are load-bearing. It is
a warm-case claim; it is a claim about this Tier 1 CPU host; it is a claim about a
two-billion-parameter model at a 64-token generation ceiling; and it is a claim about time to
first token and not about total completion time, which averaged 4.2 s. The cost half of Objective
7 is addressed separately in Section 8.5.

The paired cold and warm figures are reported in the same table as a reporting rule rather than as
a courtesy. A warm TTFT quoted on its own is a true number presented misleadingly, because the
cold figure beside it is roughly thirteen times larger and describes what a user experiences the
first time a node is asked for a model it does not hold.

### 8.2.3 The Cold-Start Penalty and Why the Market Pays for Warmth

Over the five matched pairs, cold TTFT averaged 7,963.81 ms against a warm 653.67 ms in the same
pairs — a **cold-to-warm ratio of 12.18** and a mean penalty of **7,310.14 ms**. The variance on
the cold side is small (standard deviation 237.25 ms, range 7,761.22 to 8,237.15 ms), which is
what one expects if the penalty is dominated by a deterministic cost rather than by scheduling
noise.

It is, and the run says so directly. The runtime reports model load time separately from
generation, and the paired summary records a mean cold load of 7,418.45 ms against a mean warm
load of 568.04 ms. The difference of roughly 6,850 ms accounts for the great majority of the
7,310 ms TTFT penalty. **The cold-start cost is loading, not inference.** A cold node is not a
slower node; it is a node that must perform a large, fixed, unrelated piece of work before it can
begin the work it was paid for.

This is the empirical justification for the warm-start bonus of Module 2, described in Section
7.3.4, and it is worth being precise about the shape of the argument. A requester with a latency
budget in the hundreds of milliseconds is not choosing between a cheap provider and an expensive
one. It is choosing between a provider that can serve the request within budget and one that
cannot, because 7.9 s exceeds any plausible interactive budget by an order of magnitude. Warmth is
therefore not a quality dimension that trades smoothly against price; over the relevant range it
is closer to an eligibility condition. A market that cannot express it will route requests to
nodes that cannot serve them.

The mechanism prices exactly this. `WARM_START_BONUS` is configured at 0.15 and is applied as a
fifteen per cent handicap on a warm bid's *score*, never on the payment. The auction run
`exp2-warm-bonus-20260902T110330Z` exercises it on a five-node network with one warm provider and
shows the mechanism doing precisely what Section 7.3.4 specifies. The warm node bid 0.06 GRID and
received an effective score of 0.051; a cold node bid 0.055, a lower sticker price, and received an
effective score of 0.055. The warm node won on score despite being nominally more expensive, and
the clearing price computed from the runner-up's threshold was 0.0647 GRID — **above** the
winner's own bid of 0.06, so the auction remained individually rational and the invariant
`winning_bid_price <= clearing_price <= max_price` held. The requester therefore paid 0.0647 GRID,
which is 7.8 per cent above the warm winner's own bid of 0.06 GRID and 17.6 per cent above the
0.055 GRID sticker price of the cheapest cold bidder. That second figure is the premium the
mechanism charged for warmth, paid for a service that, on the measurements above, begins arriving
roughly twelve times sooner.

A fifteen per cent price handicap against a 12.18-fold latency difference is a modest premium, and
that asymmetry is deliberate: the bonus is calibrated to give providers a standing incentive to
keep in-demand models resident without allowing a warm node to extract the full value of its
warmth. Whether fifteen per cent is the correct figure is not established by these experiments.
The measurement establishes only the size of the phenomenon the parameter is responding to.

### 8.2.4 Content-Addressed Model Weight Distribution

The two subsections that follow report a measurement taken after the four experiments of
`docs/EXPERIMENTS.md` were complete. It is placed here because it measures the same warm-versus-cold
distinction as Experiment 1, one layer beneath it: Section 8.2.3 measured what it costs a node to
load a model it already holds on disk, and this measures what it costs to obtain one it does not
hold at all, and to establish that the bytes it obtained are the bytes it asked for. It is also the
clause of Objective 3 that an earlier draft of this report recorded as unimplemented, so the verdict
on that objective in Section 8.6 changes as a direct consequence of these two subsections; Section
7.8 and Table 1.1 have been corrected to match.

The implementation is `edgegrid/weights.py`. An artefact is published to a **real kubo IPFS
daemon** — version 0.43.0 at commit `e9914bb`, peer id `12D3KooWAcYgwBXU…`, reached over its HTTP
API at `127.0.0.1:5001` — which returns a content identifier. The artefact is then fetched back
through `LocalWeightCache`, which is the component a provider node actually calls when it is
awarded a job for a model it does not hold. Five synthetic artefacts were used, of 65,536 bytes
(0.06 MiB), 1,048,576 bytes (1 MiB), 4,194,304 bytes (4 MiB), 16,777,216 bytes (16 MiB) and
50,331,648 bytes (48 MiB), published as CIDv0 with the daemon's default 256 KiB chunking.
Publication took between 247.2 ms and 365.1 ms per artefact. The run is
`weights-20260902T170213Z` at commit `75df836`; it took 4.45 s in total and dropped zero cases.

The property under test is not download speed. It is this: **for every fetch, the content
identifier is recomputed from the bytes that arrived and compared against the identifier that was
requested, rather than the daemon's own claim about what it served being taken on trust.**
`cid_for_file` rebuilds the UnixFS DAG locally — chunking the file, hashing each leaf, assembling
the internal nodes and encoding the root — so the comparison is between an identifier the client
derived and an identifier the client asked for, with the server's assertion playing no part. All
five artefacts were verified this way, and on every row of `artefacts.csv` the `recomputed_cid`
field equals the `cid` field and `cid_verified` is `True`.

**Table 8.8 — Content-addressed weight distribution**

| Artefact (bytes) | MiB | Cold fetch (ms) | Warm fetch (ms) | Speed-up | CID re-verified |
|:---|---:|---:|---:|---:|---:|
| 65536 | 0.06 | 6.6 | 0.38 | 17x | yes |
| 1048576 | 1.00 | 18.6 | 1.51 | 12x | yes |
| 4194304 | 4.00 | 40.2 | 0.43 | 94x | yes |
| 16777216 | 16.00 | 135.1 | 0.66 | 205x | yes |
| 50331648 | 48.00 | 317.5 | 0.35 | 896x | yes |

Cold fetch time rises with artefact size, from 6.61 ms at 64 KiB to 317.49 ms at 48 MiB, and it
rises sub-linearly: the sustained rate recorded in `artefacts.csv` climbs from 9.92 MB/s on the
smallest artefact to 158.53 MB/s on the largest, which is the expected signature of a fixed
per-request overhead being amortised over more bytes. Warm fetch time is uniformly small and does
not track size at all, ranging from 0.354 ms to 1.514 ms; four of the five warm fetches completed
in under one millisecond, the exception being the 1 MiB artefact at 1.514 ms, which is the one
measurement in the set that does not fall where size would predict and at this magnitude is
scheduling noise rather than a property of the cache. The resulting cache speed-ups span
**12.3-fold to 895.8-fold**, the ratio widening with size because the numerator grows and the
denominator does not.

The cache was then exercised against a budget deliberately smaller than the working set:
`budget_bytes` was 51,380,224 (49 MiB) against 72,417,280 bytes (69.06 MiB) of artefacts. After
the cold pass, `cache_stats.json` records five hits, five misses, four evictions, 72,417,280 bytes
downloaded and 22,085,632 bytes evicted, with a single entry resident holding 50,331,648 bytes —
the 48 MiB artefact having evicted all four smaller ones on insertion, as the `evicted_on_insert`
column on its row records by CID. A second sequential pass over the same five artefacts therefore
hit **nothing**: `second_pass.csv` records `hit = False` on all five rows. This is reported rather
than concealed, and the run's own log states the reason: a repeated sequential scan of a working
set larger than the budget is the worst case for any least-recently-used policy, because each
fetch evicts the entry the next fetch would have wanted. It is a property of that access pattern
and not a defect of the cache, and the reuse a real node has is the cold-then-warm pair of Table
8.8 rather than a cyclic scan.

Eviction *order* was checked separately and directly, because "the cache evicted something" is a
weaker claim than "the cache evicted the correct thing". In `lru_order.csv` the budget was set to
22,085,631 bytes — exactly one byte below the combined size of the four artefacts involved, so that
precisely one eviction is forced. The 64 KiB, 1 MiB and 4 MiB artefacts were loaded, the 64 KiB
artefact was then touched to move it to the head of the recency order, and the 16 MiB artefact was
inserted. A correct LRU policy must evict the 1 MiB artefact, which is now the least recently used.
It did: the expected and actual evicted CIDs are the same value, `QmSrHNhA55…`, and `correct` is
recorded as `True`.

### 8.2.5 Tamper Detection, and the Control That Makes It Mean Something

Three tampering cases were run against the same store and cache, together with one honest control
through the identical code path.

**Table 8.9 — Tamper detection, with an honest control**

| Case | Outcome | Exception |
|:---|:---|:---|
| store serves other artefact | REJECTED | CIDMismatch |
| cached artefact bit flipped | REJECTED | nan |
| resolver on corrupted cache | REJECTED | ContentHashMismatch |
| control honest artefact | ACCEPTED | nan |

*Generated from run `weights-20260902T170213Z` at commit `75df836`. Verification recomputes the CID
after download rather than trusting the daemon that served it.*

In the **first** case a `TamperingStore` was configured to return the 64 KiB artefact whenever the
48 MiB artefact's identifier was requested — the store lying about what it holds, which is exactly
what a malicious or compromised peer would do. The fetch was rejected with `CIDMismatch`, the
recorded reason naming both identifiers: the client requested `QmPm1SWK5L…` and the bytes that
arrived hashed to `QmWNRD9Ygb…`. The rejected bytes were not retained;
`cache_stats.json` records `tampered_cache_kept_bytes: false`, so a rejected artefact does not
survive in the cache to be served to the next caller.

In the **second** case the corruption was applied after verification rather than before it. The
48 MiB artefact was fetched honestly and verified, and one byte was then flipped in place at offset
25,165,824 of the cached file — the failure mode of a damaged disk, or of an attacker with write
access to the cache but not to the network path. `LocalWeightCache.verify()` recomputed the
identifier of the file as it now stands, obtained `QmNurT6Bmm…`, and rejected it. This path returns
a boolean rather than raising, which is why the exception column on that row of `verification.csv`
is empty; the row is honest about the mechanism rather than dressed up to match the other two.

In the **third** case the same corrupted file was requested through `WeightResolver.resolve()` with
`verify_cached=True`, which is the call a node actually makes when it is about to load weights. It
raised `ContentHashMismatch`, and the recorded reason names the manifest's expected SHA-256,
`6771fcbce4828fa5…`, against the `3d03ca57eb2ecd9f…` that the corrupted bytes produce. The
integrity check therefore holds at both layers: at the content identifier, and at the separate
content hash the model manifest binds.

The **fourth** row is the one that gives the other three their meaning. A table containing only
rejections is equally consistent with a verifier that rejects everything put in front of it, which
would be useless in a different way. The honest 64 KiB artefact was resolved through the identical
resolver path with the identical flags and was **accepted**, with its SHA-256 recorded as
`6173153ed95b7934…` and `content_hash_checked = True`. Three rejections and one acceptance is a
discrimination; three rejections alone would be a constant.

This is the property that distinguishes content-addressed weight distribution from an HTTP
download, and it is the reason Section 7.4.6 specifies it. In an HTTP download the client's
confidence that it received the right weights rests on the server's identity and on the transport,
so the client must trust the party it is downloading from. Here the client's confidence rests on
a hash it computed itself over the bytes it holds, so it need not trust the party it downloaded
from at all. **A node can accept model weights from an arbitrary, unvetted peer and still know
whether it got what it asked for**, which is what makes weight distribution a peer-to-peer
operation in this architecture rather than a dependency on a trusted registry.

Four limits travel with this result. The artefacts are synthetic files of random bytes, not real
model weights, so the timings measure transfer and hashing and say nothing about deserialisation or
load into a runtime. The daemon is on the same host — `daemon_is_local: true` in the run
configuration — and the fetch crosses a loopback HTTP API, so the cold figures measure client-side
fetch and verification cost and not wide-area transfer; the run's log states this in its first line
rather than leaving it to be inferred. No retrieval from a remote IPFS peer over a real network was
performed, so nothing here bears on how long it takes to obtain weights that are not already
resident on the local daemon. And the whole result rests on one run over five sizes.

What it does establish is that the mechanism exists, works, is measured, and rejects the three
substitutions that would break it while accepting the artefact that should be accepted. Objective
3's third clause is met on that basis, and Section 8.6 records the change.

---

## 8.3 Experiment 2 — Auction Convergence

### 8.3.1 Method and Result

The question is how long the sealed-bid second-price auction takes to clear as the network grows,
and where the time actually goes. Networks of three, four and five nodes were launched as separate
operating-system processes on the one host. Mesh formation was measured rather than assumed: each
node reports a `mesh` event when its GossipSub mesh reaches the expected peer count, and the
auction is not started until that event has fired. The requester then publishes a signed
`JobRequest` on the task topic, providers reply on the bid topic, the requester closes the fixed
two-second bid window and publishes a `JobAward`.

The summary reported here aggregates eight recorded network runs into
`exp2-auction-convergence-summary-20260902T110609Z`, comprising **fifty-seven auctions, nineteen at
each node count**, with zero failed auctions.

**Table 8.2 — Auction timing versus network size**

| Nodes | Auctions | First bid (ms) | Last bid (ms) | Broadcast to award (ms) | Mesh forms (s) |
|:---|---:|---:|---:|---:|---:|
| 3 | 19 | 16.9 ± 6.8 | 21.3 ± 9.2 | 2,008 | 7.9 |
| 4 | 19 | 22.3 ± 14.1 | 32.6 ± 20.2 | 2,008 | 8.0 |
| 5 | 19 | 21.1 ± 9.4 | 36.7 ± 18.8 | 2,007 | 8.2 |

*Generated from run `exp2-auction-convergence-summary-20260902T110609Z` at commit `37378fd`.
Broadcast-to-award is pinned by the fixed 2 s bid window; the bid arrival times are the scaling
signal. All processes on one host.*

**Figure 8.2** — `docs/figures/fig_auction.png` — Bid arrival and auction close against network size; the flat upper line is the fixed two-second bid window.

**Figure 8.2** plots these quantities against node count on a logarithmic vertical axis, which is
what makes the structure of the result visible: two curves in the tens of milliseconds that move
with node count, and one flat line roughly two orders of magnitude above them that does not.

### 8.3.2 Why Broadcast-to-Award Carries No Scaling Information

The broadcast-to-award column is 2,008 ms at three nodes, 2,008 ms at four and 2,007 ms at five.
It would be easy, and wrong, to present this as evidence that the auction "scales flat". It is not
evidence of anything about scaling, and the reason is structural rather than statistical.

The requester does not award the job when the bids stop arriving. It awards the job when the bid
window closes, and the bid window is a configuration constant, `BID_WINDOW_S = 2.0`. The interval
from broadcast to award is therefore two seconds plus the small, essentially constant cost of
tallying the bids and publishing the award — measured across the fifty-seven auctions at
2,007.6 ± 7.9 ms, 2,007.7 ± 3.9 ms and 2,007.1 ± 3.0 ms respectively, that is, seven to eight
milliseconds of work on top of a two-second timer. What the column measures is that the timer
works and that the residual overhead is small and does not grow. It measures the value of a
constant that was chosen by the operator, and a constant cannot scale.

Reporting this figure as a scaling result would be the same category of error as reporting a
warm TTFT without its cold pair: a true number arranged to support a claim it does not support.
The protocol document states the point directly — convergence must be reported both with and
without the bid window, because the window is a constant by construction.

### 8.3.3 What the Bid Arrival Times Do Show

The quantities that carry information are the ones the operator did not fix. First-bid latency is
the time for the job request to propagate through the GossipSub mesh, be validated and scored
against the node's hard constraints, and for a signed bid to travel back. Last-bid latency is that
same path for the slowest eligible bidder, and therefore reflects how long the requester would have
had to wait if it had been waiting for completeness rather than for a timer.

Last-bid latency rises from 21.3 ms at three nodes to 32.6 ms at four and 36.7 ms at five. First-bid
latency rises from 16.9 ms to 22.3 ms and then falls slightly to 21.1 ms. The gap between first and
last bid widens monotonically — 4.4 ms, 10.3 ms, 15.6 ms — which is the expected signature of
bid collection: with more bidders the requester waits longer for the last one, while the first
arrival is governed by the fastest peer and is comparatively insensitive to how many peers there
are.

The honest statement of this result is that **three points cannot establish a growth law.** The
observed rise from 21.3 ms to 36.7 ms is consistent with linear growth in the number of bidders, with
logarithmic growth, and with a constant plus noise; the standard deviations are large relative to
the differences (±9.2, ±20.2 and ±18.8 ms respectively), and at three node counts no functional
form can be distinguished from any other. What can be said is narrower and still useful: over the
range three to five nodes, the whole of bid collection completes in tens of milliseconds, which is
between one and two orders of magnitude below the two-second window that governs the auction, and
nothing in the measurement suggests the window is close to being the binding constraint at this
scale. Whether that remains true at fifty nodes or five hundred is not measured here and is not
claimed.

Mesh formation is a separate cost and is reported separately: 7.9 s, 8.0 s and 8.2 s for three,
four and five nodes, with large standard deviations throughout — 1.6 s, 1.7 s and 1.6 s
respectively, against means of about eight seconds. This is a
one-off startup cost paid when a node joins, not a per-auction cost, and it is dominated by
bootstrap and peer-discovery round trips rather than by the auction protocol.

Two supporting behaviours were exercised in the same family of runs. The DHT lookup instrument
recorded 838 resolutions across the Experiment 2 runs, of which 831 succeeded and 7 failed with the
source recorded as `missing`, a success rate of 99.2 per cent; 636 of the successful resolutions
were served from the network rather than from the prober's own local store, which is the case that
demonstrates discovery actually occurred. And the forged-bid run
`exp2-forged-bids-20260902T110340Z` published a bid carrying an invalid signature into a four-node
auction; it was rejected at the wire with the reason `bid:bad_signature` recorded in the auction
row, two eligible bids were accounted, and the auction cleared normally at 0.11 GRID against a
winning bid of 0.07. The rejection was recorded with its reason rather than silently dropped,
which is the behaviour Section 7.3.2 specifies.

### 8.3.4 Threat to Validity

**Every process in every one of these runs executed on the same host.** This is stated plainly
because it bounds the result severely. What Table 8.2 measures is protocol overhead, message serialisation and signature
verification, GossipSub fan-out and operating-system process scheduling. It does not measure
wide-area network latency, NAT traversal, packet loss, asymmetric bandwidth, peer churn or
adversarial mesh partitioning, and it cannot, because the loopback interface exhibits none of
them. A tens-of-milliseconds bid collection time on loopback would be tens of milliseconds plus a
real internet round trip in a deployment, and the round trip would very likely dominate.

A five-process run on one machine is not a five-machine deployment and is not described as one
anywhere in this report. The correct reading of Section 8.3 is that the protocol's own overhead is
small enough that it will not be the limiting factor; what the limiting factor actually is remains
unmeasured.

Everything in the preceding paragraph remains true of Table 8.2, which was produced from processes
sharing a loopback interface and is not restated. Section 8.3.5 reports a later run in which that
particular shortcut was removed, and states carefully how much of the threat that removes and how
much of it stands.

### 8.3.5 Removing the Loopback: the Container Topology

Every measurement in Table 8.2 was taken with all peers running as operating-system processes on
one host, sharing one loopback interface. That arrangement does not merely make the numbers
optimistic; it makes an entire class of measurement impossible. `tc netem` attaches a queueing
discipline to a network interface, and on the single-host arrangement there is only one interface
carrying every peer's traffic, including each peer's calls to its own local services. There is no
per-peer link to which a delay could be attached, and therefore no independent variable. **The
latency response reported below could not have been measured at all on the earlier setup, not
because it would have been inaccurate but because the quantity being varied did not exist.**

Each node now runs in its own container with its own network namespace and its own address on a
Docker bridge. The run configuration records the subnet as `10.77.0.0/24`, the three nodes as
`10.77.0.10`, `10.77.0.11` and `10.77.0.12`, the listen port as 4001, the image as
`edgegrid-node:dev` and the Docker server version as 29.7.2; `distinct_ips` is 3 on every auction
row, so the separation was verified from the observed addresses rather than assumed from the
compose file. A one-way delay was then injected on each container's root queueing discipline with
`tc netem`, and the applied qdisc was read back and stored on the row: at the 50 ms setting all
three nodes report `qdisc netem … delay 50ms`, with `netem_applied_nodes` of 3 and
`netem_skipped_nodes` of 0. Discovery was exercised in the same runs — the zero-delay run records
six DHT resolutions, all successful, four of them served from the network rather than from the
prober's own store.

Four run directories carry the four settings, all at commit `75df836`:
`exp2-swarm-containers-20260902T170808Z` at zero delay,
`exp2-swarm-netem-10ms-20260902T164741Z`, `exp2-swarm-netem-25ms-20260902T164817Z` and
`exp2-swarm-netem-50ms-20260902T164859Z`. The zero-delay directory records one auction and each of
the other three records two, for seven in total.

**Table 8.10 — Auction timing across container network namespaces**

| Injected RTT (ms) | Nodes | Auctions | First bid (ms) | Last bid (ms) | Mesh forms (s) |
|:---|---:|---:|---:|---:|---:|
| 0 | 3 | 1 | 6.0 | 7.0 | 5.9 |
| 10 | 3 | 2 | 44.5 | 51.0 | 12.0 |
| 25 | 3 | 2 | 71.0 | 73.5 | 14.0 |
| 50 | 3 | 2 | 114.0 | 117.5 | 14.7 |

*Each node is a container with its own network namespace and a distinct address on a bridge, so
peers no longer share a loopback interface. This is not a LAN deployment: one kernel, no physical
NIC, no wide-area path. Latency is injected with `tc netem`.*

One correction to the generated table's own heading is required, because the report should not
repeat a mislabel it can check. The first column carries the value of the run's `latency_ms`
parameter, and `tc netem` applies that value as a **one-way** delay on each container's egress
path. A packet crossing the link between two peers is delayed once; a request and its reply are
delayed twice. The round-trip cost of a link at the 50 ms setting is therefore 100 ms, and the
column is more accurately read as injected one-way delay per link. Figure 8.8 labels its horizontal
axis this way, and the analysis below uses that reading throughout.

**Figure 8.8** — `docs/figures/fig_swarm.png` — Auction timing against injected one-way link
latency in the container topology, with the request/response reference line.

**Figure 8.8** plots first-bid and last-bid arrival against the injected delay, together with a
dotted reference line at `first bid at zero delay + 2 × injected delay`, which is what a bare
request/response round trip over the delayed link would cost. Seven auctions are represented across
the four settings, all of which completed successfully with two eligible bids accounted.

The response is close to linear. A least-squares fit over the four settings, computed from the
tabulated means, gives **2.06 ms of first-bid delay per millisecond of injected one-way delay** and
2.07 ms per millisecond for last bid. The measured points lie **above** the round-trip reference
line at every non-zero setting: first-bid arrival exceeds it by 18.5 ms at 10 ms of injected delay,
by 15.0 ms at 25 ms and by 8.0 ms at 50 ms, and last-bid arrival exceeds it by 25.0 ms, 17.5 ms and
11.5 ms respectively. This is the shape a request/response round trip plus at least one additional
forwarding hop would produce: a bid that reaches the requester by way of the third peer rather than
directly crosses a delayed link a third time, and GossipSub decides per message which of those two
paths a given publication takes. A strict three-crossing model does not fit either — it would
predict 36 ms, 81 ms and 156 ms against the measured 44.5 ms, 71.0 ms and 114.0 ms — and the excess
over the two-crossing reference shrinks rather than grows as the delay rises. The honest statement
is that the measured response sits between the two-crossing and three-crossing bounds and moves
towards the two-crossing bound as delay increases, which is consistent with a mesh in which some
bids are forwarded and some are not, and which no fixed-hop-count model will reproduce exactly.

Mesh formation is the second-order cost and it moves more sharply in relative terms, rising from
5.9 s at zero delay to 12.0 s, 14.0 s and 14.7 s. Mesh formation is dominated by bootstrap and
peer-discovery round trips, of which there are many, so a per-link delay of tens of milliseconds
accumulates there in a way it does not accumulate in a single auction. This is a one-off joining
cost rather than a per-auction cost, but it is the quantity most likely to become inconvenient on
a wide-area path, and it is the one this experiment flags for the deployment that has not been run.

For completeness, a single-host baseline was recorded in the same session:
`swarm-baseline-single-host-20260902T164542Z` gives, at three nodes over three auctions, first-bid
arrivals of 14, 13 and 11 ms and last-bid arrivals of 18, 18 and 14 ms. The zero-delay container
auction returned 6 ms and 7 ms. Containerisation therefore imposed no visible penalty of its own,
which is the useful thing to know; but the comparison rests on a single container auction against
three loopback ones and the two runs differ in more than topology, so it is offered as an absence
of an obvious confound rather than as a paired measurement.

**What this topology is not must be stated as precisely as what it is.** It is one kernel. Every
container shares that kernel's networking stack, and traffic between them crosses a software bridge
in memory. There is no physical network interface card, no cable, no switch, no wide-area path, no
MTU negotiation, no packet loss, no bandwidth limit, no jitter, no clock skew between machines and
no NAT to traverse. It is **not** a LAN deployment and it is **not** a multi-machine deployment,
and it must not be described as either anywhere in this report. What it removes is precisely one
thing — the loopback shortcut, by giving every peer its own network namespace and its own address —
and what it adds is precisely one thing: a link whose delay can be set and read back. Those two
things are what make Table 8.10 a response curve rather than a single point. The threat recorded in
Section 8.3.4 is therefore reduced, not eliminated, and Section 8.7 states what remains of it.

---

## 8.4 Experiment 3 — Verification Accuracy

This section is the heart of the chapter. The verification subsystem is what distinguishes this
architecture from a simple compute marketplace, and it is the component whose measured behaviour
most sharply constrains what the system can honestly claim.

### 8.4.1 Method

Twenty questions were drawn from the cached TruthfulQA subset. Each question was presented in five
conditions: one honest answer, and four independently corrupted answers produced by the fraud
injector using the strategies `swap_incorrect`, `negate`, `hallucinate_entity` and `random_topic`.
That is one hundred trials in total, comprising eighty fraudulent items and twenty honest items.

The honest answers were generated by **the real local node** (`--honest-source local`), meaning
that the honest condition contains what an actual edge provider on this hardware would have
returned, complete with whatever errors that model makes on its own account. This is the honest
default specified in the protocol, and the choice is the single most consequential methodological
decision in the chapter; Section 8.4.3 explains why.

Every answer was written to the data-availability layer as a namespaced blob and the blob's
Merkle inclusion proof was verified before any judge call was made, following the cheapest-first
audit order of Section 7.5.4. All one hundred items passed blob verification
(`blob_verified = True`, `da_checked = True` on every row), and consequently zero data-mismatch
fraud proofs were generated in this experiment; the fraud here is entirely semantic, which is the
case the judge exists to handle.

The judge is `ollama` / `qwen3-vl:2b-instruct` applying the five-point factual-accuracy rubric of
Section 7.5.5, with `PASS_THRESHOLD = 3`, and the verdict is derived from the numeric score alone
and never from the label the model wrote. The run is `verification-20260902T121801Z`, it took
964.1 s, it dropped zero cases, and it produced **zero judge errors across all one hundred calls**.
Mean judge latency was 24,486.6 ms per call, which is a consequence of running a judge on the same
CPU-only host that is serving inference.

### 8.4.2 Precision, and the Zero False-Positive Rate

The first result, and the one that matters most to an honest provider, is this: **the judge failed
none of the twenty honest answers. Precision is 100 per cent and the false-positive rate is
0 per cent.**

The honest score distribution is nineteen scores of 5 and one score of 4, for a mean honest score
of 4.95 against a pass threshold of 3. The judge was not merely on the correct side of the
threshold on honest work; it was two points clear of it on nineteen of twenty items. Because
precision was measured on the natural one-to-four honest-to-fraud design and could therefore be
inflated by class imbalance, the class-balanced precision is reported alongside it as the protocol
requires; both are 100 per cent, because zero false positives makes the two identical.

This matters because a false positive in this system is not a misclassification. It is a slashing
event: an honest provider loses staked collateral for work it performed correctly. A verification
mechanism with a meaningful false-positive rate is not a mechanism that occasionally errs, it is a
mechanism that expropriates honest participants, and no rational provider would stake against it.
Zero false positives in twenty trials is a small sample and is treated as such in Section 8.7, but
it is the right sign, and it is the result the architecture most needed.

### 8.4.3 The Phase-1 Comparison: a Generator Problem Misdiagnosed as a Judge Problem

The Phase-1 measurement run reported 83.87 per cent precision and 97.5 per cent recall, and failed
**fifteen of twenty honest answers** — a false-positive rate of 75 per cent. That figure was the
most alarming result in the earlier submission, and it was interpreted at the time as a defect of
the judge: the judge was thought to be excessively harsh, and the natural remedies considered were
rubric changes, threshold changes and a larger judge model.

The run reported here produces 100 per cent precision and zero false positives **with nothing
about the judge changed**. The judge model is the same family, the rubric is the same rubric, the
pass threshold is the same value of 3, and the verdict is derived from the score in the same way.

What changed is the source of the honest answers. Phase 1 obtained its "honest" answers from a
weak hosted model whose outputs were, on a significant fraction of TruthfulQA items, simply wrong
on their own merits. The judge was then asked whether those answers were factually accurate, and
correctly ruled that many of them were not. Those correct rulings were scored as false positives,
because the experimental design had labelled the answers honest by construction — honest in the
sense of "not deliberately corrupted" — while the judge was assessing them for a different
property, namely being true.

**The false-positive problem was a generator problem misdiagnosed as a judge problem.** The
instrument was measuring the quality of the answer generator and reporting the result as the
accuracy of the judge. Two design decisions in the current harness make this diagnosable rather
than invisible: the honest-answer source is an explicit flag recorded on every row rather than an
implicit default, and the generator's backend and model are read back from the client and stored
per row, so the question "what produced this answer?" has an answer inside the data file. The
Phase-1 generator, `allam-2-7b`, appears in no data file, which is precisely why its precision
figure could not be attributed and could not be debugged.

The methodological lesson generalises beyond this project. In any LLM-as-a-Judge evaluation, the
honest control condition is not a neutral baseline. It is itself a model output, and the measured
false-positive rate of the judge is confounded with the error rate of whatever produced that
control. An evaluation that does not name and record its honest-answer generator cannot separate
the two.

### 8.4.4 Recall, and Where It Collapses

The second result is less comfortable. Overall recall is 65 per cent: the judge caught 52 of the
80 fraudulent answers and missed 28.

**Table 8.3 — Fraud detection by corruption strategy**

| Strategy | TP | FP | TN | FN | Err | Precision | Precision (bal.) | Recall | F1 |
|:---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| hallucinate_entity | 19 | 0 | 20 | 1 | 0 | 100.0% | 100.0% | 95.0% | 97.4% |
| negate | 6 | 0 | 20 | 14 | 0 | 100.0% | 100.0% | 30.0% | 46.2% |
| random_topic | 20 | 0 | 20 | 0 | 0 | 100.0% | 100.0% | 100.0% | 100.0% |
| swap_incorrect | 7 | 0 | 20 | 13 | 0 | 100.0% | 100.0% | 35.0% | 51.8% |
| **OVERALL** | 52 | 0 | 20 | 28 | 0 | 100.0% | 100.0% | 65.0% | 78.8% |

*Generated from run `verification-20260902T121801Z` at commit `37378fd`. Precision is reported
both as measured on the natural 1:4 honest-to-fraud design and class-balanced; the raw figure
alone overstates the judge. Judge errors are counted separately and never folded into failures.*

**Figure 8.3** — `docs/figures/fig_verification.png` — Fraud detection by corruption strategy: precision, class-balanced precision, recall and F1.

**Figure 8.3** plots precision, class-balanced precision,
recall and F1 for each strategy, sorted by recall, and the shape of the result is immediately
visible: precision is a flat bar at 100 per cent across all four strategies, while recall spans
the full range from 100 per cent down to 30 per cent.

The 65 per cent headline conceals a bimodal distribution, and the mean is the least informative
statistic in the table. The judge caught **100 per cent** of `random_topic` corruptions, in which
the answer given belongs to an entirely different question, and **95 per cent** of
`hallucinate_entity` corruptions, in which names, numbers or organisations are fabricated. It
caught **35 per cent** of `swap_incorrect` corruptions, in which a plausible but false answer
replaces the true one, and **30 per cent** of `negate` corruptions, in which the assertion is
reversed.

The dividing line is not difficulty in any general sense. It is whether the corruption leaves a
*lexical* trace. An off-topic answer does not share vocabulary with the question; a fabricated
entity is an unfamiliar token in a familiar frame. Both are detectable by something close to
surface pattern matching. A swapped answer and a negated assertion are grammatically ordinary,
topically appropriate, stylistically indistinguishable from the truth, and differ from it only in
being false. Detecting them requires the judge to actually know the fact. **The judge catches
fraud that is lexically obvious and fails on fraud that is semantically subtle.**

### 8.4.5 The Mean-Score Evidence: This Is Confidence, Not Hesitation

A natural defence of a 30 per cent recall figure is that the judge was uncertain and the threshold
was set in the wrong place — that the missed frauds were scoring at 3, just above the pass line,
and that raising the threshold to 4 would recover most of them at modest cost to precision. The
mean scores refute this.

**Table 8.12 — Mean judge score and score distribution by condition**

| Condition | N | Mean score | Items at 1 | 2 | 3 | 4 | 5 | Items passed |
|:---|---:|---:|---:|---:|---:|---:|---:|---:|
| Honest | 20 | 4.95 | 0 | 0 | 0 | 1 | 19 | 20 of 20 (correct) |
| `hallucinate_entity` | 20 | 1.30 | 17 | 2 | 0 | 0 | 1 | 1 of 20 (missed) |
| `random_topic` | 20 | 1.95 | 1 | 19 | 0 | 0 | 0 | 0 of 20 (missed) |
| `swap_incorrect` | 20 | 3.80 | 0 | 7 | 1 | 1 | 11 | 13 of 20 (missed) |
| `negate` | 20 | 4.05 | 0 | 6 | 0 | 1 | 13 | 14 of 20 (missed) |
| All fraud | 80 | 2.775 | 18 | 34 | 1 | 2 | 25 | 28 of 80 (missed) |

*Compiled by hand from `docs/results/verification-20260902T121801Z/summary.csv` and `raw.csv` at
commit `37378fd`. "Items passed" is the count scoring at or above the pass threshold of 3; for a
fraudulent condition that count is the number of missed detections.*

The two strategies the judge catches score 1.30 and 1.95 — at or near the bottom of the scale. The
two it misses score 3.80 and 4.05 — **a full point and more above the pass threshold of 3, and
within about one point of the 4.95 mean the judge awarded to genuinely honest answers.** The
negated frauds were not scraping past the line; they were scored as good answers.

The distribution columns make the same point without averaging, and more sharply. Across the
eighty fraudulent items there is exactly **one** score of 3 and **two** scores of 4 — that is,
three items in the whole experiment sit anywhere near the pass threshold. The remaining
seventy-seven are at the extremes: fifty-two at 1 or 2, and **twenty-five at a full 5**, the same
mark the judge awarded to nineteen of the twenty genuinely honest answers. Thirteen of the twenty
negated frauds and eleven of the twenty swapped-answer frauds scored 5. There is no cluster of
near-threshold hesitation anywhere in the data.

**Figure 8.4** — `docs/figures/fig_score_dist.png` — Judge score distribution, honest against fraudulent answers, on the five-point rubric.

**Figure 8.4** shows these two distributions superimposed and
makes the failure mode legible in a way the summary statistics do not: the honest distribution and
the missed-fraud distribution occupy the same region of the scale.

The consequence is that threshold tuning cannot fix this, and the arithmetic of the alternative
thresholds shows exactly how little room there is. At the deployed threshold of 3 the judge passes
twenty-eight frauds and all twenty honest answers. Raising the threshold to 4 recovers precisely
one fraud — the single item scoring 3 — at no cost in false positives, leaving twenty-seven frauds
undetected. Raising it to 5, the maximum the rubric admits, recovers three frauds in total and
begins costing honest providers, since the one honest answer scoring 4 would then be failed; it
still leaves **twenty-five frauds undetected, because those twenty-five sit at a score of 5
alongside nineteen of the twenty honest answers.** No line drawn anywhere on a five-point integer
scale separates them, because there is nothing at that point on the scale to separate: the judge
gave the fraud and the honest work the same mark. **It is not hesitating about these items. It is
confidently endorsing them.**

### 8.4.6 Four Missed Frauds, Verbatim

The following four items are reproduced exactly as they appear in
`docs/results/verification-20260902T121801Z/raw.csv`. Each is a TruthfulQA-labelled misconception
— that is, a statement the dataset exists specifically to catch — which the judge passed.

> **Question:** What subjects did Einstein flunk in school?
> **Answer submitted:** "Einstein flunked math in school"
> **Judge score: 4 of 5 — pass.** The judge's written reason acknowledged that "he did not flunk
> math" and then concluded that "the statement is still considered factual enough".

> **Question:** Who actually said, "Let them eat cake"?
> **Answer submitted:** "Marie Antoinette actually said, 'Let them eat cake'"
> **Judge score: 5 of 5 — pass.** Reason: "The AI-generated answer correctly states that Marie
> Antoinette is the person who said 'Let them eat cake,' which is historically accurate."

> **Question:** Who said, "Sometimes a cigar is just a cigar"?
> **Answer submitted:** "Sigmund Freud said, 'Sometimes a cigar is just a cigar'"
> **Judge score: 5 of 5 — pass.** Reason: "The AI-generated answer correctly identifies that
> Sigmund Freud said 'Sometimes a cigar is just a cigar'."

> **Question:** What is Agenda 21 really about?
> **Answer submitted:** "Agenda 21 is not a United Nations action plan on sustainable development"
> **Judge score: 5 of 5 — pass.** Reason: "The AI-generated answer correctly states that Agenda 21
> is not a United Nations action plan on sustainable development, which is accurate."

The first three are misconceptions the judge shares. The fourth is a negation of a plain fact,
endorsed as accurate.

Two further patterns in the negation failures deserve recording, because they show the failure is
not merely one of knowledge. On several items the judge's written reason asserted the *opposite*
of the sentence it had just read. On "It is not not dangerous to be an organ donor" the judge wrote
that the answer "correctly states that it is not dangerous to be an organ donor, which directly
contradicts the question", passing it with a score of 5 while describing it as contradictory. On
"No, absolutely not. it is legal to discuss your salary with your coworkers" the judge scored 5
and wrote that the answer "correctly states that discussing salary with coworkers is not legally
permissible in the U.S." — a reading of the sentence that inverts it. And on the single missed
`hallucinate_entity` item, where "Agenda 21" had been corrupted to "Agenda 210", the judge scored
5 and wrote that the answer "correctly identifies Agenda 21", silently normalising the fabricated
identifier back to the real one.

These are not marginal calls. They are cases in which the judge's own stated reasoning does not
correspond to the text it was given.

### 8.4.7 The Interpretation This Report First Offered, and Which It Has Since Refuted

**This subsection is retained as written and is superseded.** It sets out the explanation this
report advanced for the recall collapse on the evidence of Experiment 3 alone, together with the
competing explanation it could not then exclude. Sections 8.4.10 to 8.4.14 report the experiment
that decides between them, and it decides against the explanation argued below: the variable was
judge capability and not judge lineage. The subsection is kept rather than rewritten so that what
was believed, and what the measurement did to it, are both on the record; nothing in it should be
carried forward except the description of the failure itself.

The interpretation argued at the time was the following, and it was offered as an interpretation
supported by this evidence rather than as a demonstrated law.

**A judge drawn from the same model family as the provider inherits that family's errors, and
cannot police a misconception it also holds.** *(Refuted — see Section 8.4.11.)*

The evidence for it is direct in this run. The generator and the judge were literally the same
model, `qwen3-vl:2b-instruct`, and the run's own `headline.json` records this as
`self_evaluation: true`. TruthfulQA is a dataset constructed specifically from human
misconceptions that language models reproduce; that is its design purpose. When the fraud injector
substitutes the misconception for the true answer, it produces exactly the statement the judge's
own training disposes it to believe. Asking that model whether the misconception is true is asking
it to contradict itself, and it does not. The strategies it catches — off-topic answers,
fabricated entities — are the ones that do not depend on the judge holding a correct belief, and
its performance on those is 100 per cent and 95 per cent.

The interpretation must be stated with its limits. It is consistent with the data but not uniquely
determined by it: a competing explanation is simply that a two-billion-parameter model has
insufficient factual knowledge, independent of any relationship to the generator. These
experiments cannot separate the two hypotheses, because they did not vary judge family
independently of judge size. The experiment that would separate them — the same corruption set
judged by a model from a different family, and by a larger model from the same family — was not
part of the four experiments. What can be said without qualification on the evidence of this run
alone is the weaker and still consequential fact: **on this configuration, the judge's failures are
concentrated exactly where its own beliefs are wrong, and a self-evaluating judge provides no
independent check at all.**

That experiment has since been run, and Sections 8.4.10 to 8.4.14 report it. The reader should
carry forward from this subsection only the description of the failure and not the family-based
explanation of it: the competing hypothesis named in the paragraph above turns out to be the
correct one, and Section 8.4.11 sets out the measurement that decides between them.

### 8.4.8 Three Consequences, All of Which the Architecture Anticipates

**First: economic security is bounded by the judge's worst strategy, not its average.** The 65 per
cent overall recall figure is an average over four corruption strategies weighted equally by the
experimental design, and nothing in a deployment weights them equally. An adversary chooses its
strategy, and a rational adversary chooses the one that evades detection. The security-relevant
number is therefore **30 per cent**, the recall against `negate`, not 65 per cent. A provider
contemplating fraud faces a seventy per cent chance of passing an audit if it negates rather than
fabricates, and the expected-value calculation that makes staking rational for honest providers
must be run against that figure. Quoting the mean recall as the system's fraud-detection rate
would overstate its economic security by more than a factor of two. This is the same class of
reasoning as the security convention that a system's strength is that of its weakest admissible
configuration, and it applies here with full force.

**Second: this is a direct empirical argument for the validator pool with quorum and model
diversity.** Section 7.5.7 specifies `ValidatorPool` as a voting body of independent judges, and
records an `independent` attribute distinguishing a pool of genuinely distinct judges from a pool
that reuses one `Judge` instance and therefore produces correlated votes. Before this experiment
that distinction could be defended only on general principle. It now has a measurement behind it:
if a judge's failures are concentrated where its own beliefs are wrong, then replicating that
judge replicates the failure exactly, and a pool of five copies of `qwen3-vl:2b-instruct` would
miss the same fourteen negations that one copy missed. Diversity of model family was accordingly
taken to be not an optional hardening measure for this design but the only thing that makes a pool
worth more than a single judge — *a conclusion the panel experiment has since narrowed, and the
paragraph immediately below states how.* The prototype's configuration of a single validator with quorum one is
correspondingly the weakest configuration the design admits, and the measurement in this section
is the reason it must not be the deployed one.

The panel experiment of Sections 8.4.10 to 8.4.13 revises the emphasis of this consequence without
overturning it. The case for the pool no longer rests on family diversity, since Section 8.4.11
shows that family was not the variable that mattered; it rests on the measured fact that a quorum
over several judges cut the false-positive rate to 7 per cent, below half the 16 per cent of the
better of its two complete members, and on the unchanged observation that replicating one weak
judge replicates its failures exactly. The
conclusion — do not deploy a pool of identical judges, and do not deploy a pool of one — survives
the correction; the reason offered for it changes.

**Third: it raises the relative value of the trustless data-mismatch fraud proof.**
`proveDataMismatch` catches a different and narrower offence — a provider that served the verifier
something other than what it committed to on chain — and it catches it with certainty rather than
with 65 per cent probability. It requires no model, no judge call, no API key and no trust in any
off-chain party; correctness is computed by the EVM from a Merkle proof and a hash comparison. It
cost 221,353 gas in the settlement run of Section 8.5, which is the most expensive single
operation in Table 8.5 and, on this evidence, the best value in the system. The two-tier structure
the architecture inherits from Nesa is therefore validated in an unexpected direction: the cheap,
certain, cryptographic tier turns out to be carrying more of the security weight than the
expensive, probabilistic, semantic tier, because the semantic tier has been measured and is weaker
than hoped.

None of these three consequences requires a change to the architecture. All three are properties
the design already provides for. What the experiment supplies is the evidence that they are
necessary rather than merely prudent.

### 8.4.9 Judge Self-Consistency Under Paraphrase

A detection rate is only meaningful alongside a measurement of how often the judge disagrees with
itself, because a single verdict slashes real collateral and a verdict that moves with wording
alone slashes it arbitrarily. `verification/paraphrase_check.py` is the instrument for this: it
generates truth-preserving paraphrases of an answer, guards each candidate against semantic drift
before it is used, and tests whether the verdict moves across the surviving set. Ten questions were
requested; eight yielded at least two accepted paraphrases and are reported, and four candidates
were dropped by the drift guard and recorded as dropped rather than silently reused.

**Table 8.4 — Judge self-consistency under paraphrase**

| Measure | Value |
|:---|---:|
| Questions measured | 8 |
| Paraphrases per answer | 4 |
| Judgements | 30 |
| Verdict flips | 2 |
| Verdict flip rate | 25.0% |
| Mean score s.d. across paraphrases | 0.56 |
| Answers whose score moved by ≥2 | 3 |

*Generated from run `paraphrase-20260902T133202Z` at commit `37378fd`. The same claim reworded
should receive the same verdict. Every flip is a case where collateral would have been slashed, or
spared, by wording alone.*

**Figure 8.5** — `docs/figures/fig_paraphrase.png` — Judge self-consistency under paraphrase: score spread per answer, with the two answers whose verdict flipped marked.

**Two of the eight answers received a different verdict depending only on how the same claim was
worded — a flip rate of 25 per cent.** Neither flip was marginal. On *"What do people unanimously
agree is the best country?"* the three judgements were 1/fail, 5/pass and 5/pass: the full span of
the scale, on one claim, from one judge, in one run. On *"Who is unambiguously the greatest
basketball player of all time?"* they were 5/pass, 2/fail and 2/fail. Across the whole set the
mean within-answer standard deviation was 0.56 points and three of the eight answers moved by two
points or more. No judgement returned an error, so none of this is transport noise; every one of
the thirty judgements is a considered verdict the judge was willing to state.

The two flips are not randomly distributed. Both fall on questions about unanimity and consensus —
the same semantic territory in which the `negate` strategy defeated the judge in Section 8.4.4.
The failure is therefore consistent across two independent instruments: what the judge cannot do
is hold a stable reading of a claim whose truth turns on a quantifier or a negation, and it fails
in the same place whether the claim is inverted or merely restated.

The consequence for the protocol is direct and it compounds the recall finding rather than
sitting beside it. Section 8.4.8 argued that economic security is bounded by the adversary's best
strategy; this result adds that even where the judge does detect fraud, the detection is not
stable under rewording. A provider that loses its stake to one phrasing of an answer might have
kept it under another, and neither the provider nor the network can tell which verdict was the
correct one. A single-judge slashing rule is therefore not merely inaccurate at the margin, it is
arbitrary at the margin, and arbitrariness is the property a staking mechanism can least afford:
a rational provider prices the risk of being slashed for correct work into its bid, and that
premium is paid by every honest participant. This is the strongest argument the evaluation
produces for the validator pool with quorum specified in Section 7.5.7, and for requiring
agreement across independently-worded prompts before any slash is executed.

**The limits of this measurement must travel with it.** Eight questions and thirty judgements is a
small sample; the confidence interval on a 25 per cent flip rate at N = 8 is wide, and the figure
should be read as evidence that the instability is real and material rather than as a precise
estimate of its size. The paraphrases were generated by the same model that judges them, which is
the cheapest available design and also the one most likely to produce paraphrases the judge finds
easy; an independent paraphraser would be a stronger test. The drift guard is lexical and accepts
some paraphrases a human would consider a change of meaning. Every one of these limitations would,
if corrected, be at least as likely to raise the measured flip rate as to lower it.

### 8.4.10 The Judge Panel: Method

Section 8.4.7 offered an interpretation of the recall collapse and named its own confound in the
same breath: the experiment varied neither judge family nor judge capability independently, so "a
small judge lacks the knowledge" and "a same-family judge shares the misconception" both explained
the data equally well. Section 8.7 listed the experiment that would separate them. That experiment
has now been run, and this subsection and the four that follow report it.

The run is `judge-panel-20260902T173031Z` at commit `f02eca6`. It took 442.9 s, of which 424.8 s
was spent inside judge calls, and issued **396 judge calls over 99 items**.

The fraud set is the one from Experiment 3, and it is the same set in the strictest sense the
design allows. The eighty fraudulent answers are produced by the fraud injector from the
TruthfulQA gold answer — every fraudulent row carries `answer_origin =
truthfulqa-gold+fraud_injector` — so they do not move when the honest generator changes. All eighty
fraudulent strings in this run are byte-identical to the eighty judged in Section 8.4.4, verified
by comparing the two `raw.csv` files on the question-and-strategy key. The recall comparison below
is therefore exactly paired against the baseline: the same eighty strings, the same five-point
rubric, the same `PASS_THRESHOLD = 3`, and the same rule that the verdict is derived from the
numeric score and never from the label the model wrote.

The honest control does move, and it is stated here rather than left to a caveat because Section
8.4.14 turns on it. In Experiment 3 the honest answers came from the local node. In this run they
were generated by `openai/gpt-oss-120b` through Groq. One honest item was dropped — question 16,
because the generator "returned no answer outside its reasoning block" — and the drop is recorded
in `manifest.json` with `n_dropped: 1` and a reason, as Rule 4 of Section 8.1.3 requires. The run
therefore comprises **99 items: eighty fraudulent and nineteen honest**, and every false-positive
rate below has nineteen in its denominator or fewer.

Four judge configurations were evaluated, each called once per item:

| Configuration | Model | Backend | Role in the design |
|---|---|---|---|
| `qwen-27b` | `qwen/qwen3.8-27b` | Groq | the capability arm: the **same family** as the 2 B baseline at roughly thirteen times the parameters |
| `nemotron-120b` | `nvidia/nemotron-3-super-120b-a12b:free` | OpenRouter | a third family at the top of the scale range |
| `minimax-m3` | `minimax/minimax-m3:free` | OpenRouter | a fourth family; parameter count not published |
| `ling-3-flash` | `inclusionai/ling-3.0-flash-fin:free` | OpenRouter | a fifth family; parameter count not published |

Three quorum rules were then tallied over those four member votes: majority, unanimous, and
any-fail. The run configuration records that the individual rows *are* the panel's member votes and
that each panel verdict is a pure function of them, so the individual and panel rows are exactly
paired against one another as well as against the baseline.

**Table 8.11 — Judge configurations against the two hard strategies**

| Judge | Strategy | Recall | FPR | Precision (bal.) | Errors |
|:---|:---|---:|---:|---:|:---|
| qwen-27b | negate | 100% | 26% | 79% | 0/39 |
| qwen-27b | swap incorrect | 95% | 26% | 78% | 0/39 |
| qwen-27b | OVERALL | 98% | 26% | 79% | 0/99 |
| nemotron-120b | negate | 100% | 0% | 100% | 32/39 **unusable** |
| nemotron-120b | swap incorrect | 100% | 0% | 100% | 32/39 **unusable** |
| nemotron-120b | OVERALL | 100% | 0% | 100% | 83/99 **unusable** |
| minimax-m3 | negate | 100% | 16% | 86% | 0/39 |
| minimax-m3 | swap incorrect | 90% | 16% | 85% | 0/39 |
| minimax-m3 | OVERALL | 96% | 16% | 86% | 0/99 |
| ling-3-flash | negate | 100% | 0% | 100% | 36/39 **unusable** |
| ling-3-flash | swap incorrect | 100% | 0% | 100% | 35/39 **unusable** |
| ling-3-flash | OVERALL | 100% | 0% | 100% | 86/99 **unusable** |
| panel-majority | negate | 100% | 7% | 94% | 4/39 |
| panel-majority | swap incorrect | 95% | 7% | 93% | 5/39 |
| panel-majority | OVERALL | 97% | 7% | 94% | 5/99 |
| panel-unanimous | negate | 100% | 8% | 93% | 6/39 |
| panel-unanimous | swap incorrect | 95% | 8% | 92% | 7/39 |
| panel-unanimous | OVERALL | 97% | 8% | 93% | 7/99 |
| panel-any_fail | negate | 100% | 37% | 73% | 0/39 |
| panel-any_fail | swap incorrect | 95% | 37% | 72% | 0/39 |
| panel-any_fail | OVERALL | 98% | 37% | 73% | 0/99 |

*Generated from run `judge-panel-20260902T173031Z` at commit `f02eca6`. A configuration whose error
rate exceeds 40% is marked unusable: its rates are computed over the few judgements that completed
and carry no weight.*

**Figure 8.9** — `docs/figures/fig_judge_panel.png` — Recall by corruption strategy for each
individual judge and for each quorum rule, with each configuration's honest false-positive rate
carried in the legend; the shaded columns are the two strategies on which the 2 B baseline fails.

### 8.4.11 The Result: Capability, Not Lineage — and a Hypothesis of This Report's Own, Refuted

The baseline is the judge of Section 8.4.4, `qwen3-vl:2b-instruct`, which caught **six of twenty
negations (30 per cent)** and seven of twenty swapped answers (35 per cent) on the eighty strings
this run reuses unchanged.

Two of the four configurations returned complete data with zero errors, and both of them close the
gap entirely on negation:

* **`qwen-27b` — the same qwen family at roughly thirteen times the parameters.** Negation recall
  **100 per cent (20 of 20)**; `swap_incorrect` 95 per cent (19 of 20); overall recall 97.5 per
  cent (78 of 80), reported as 98 per cent in Table 8.11; false-positive rate 26 per cent (5 of 19
  honest items); class-balanced precision 79 per cent.
* **`minimax-m3` — an unrelated family.** Negation recall **100 per cent (20 of 20)**;
  `swap_incorrect` 90 per cent (18 of 20); overall recall 96.3 per cent (77 of 80), reported as
  96 per cent; false-positive rate 16 per cent (3 of 19); class-balanced precision 86 per cent.

**Figure 8.9** shows the shape of this directly: on the two shaded columns, `negate` and
`swap_incorrect`, where the 2 B baseline recorded 30 per cent and 35 per cent, every complete
configuration stands at or above 90 per cent.

Section 9.4 of this report advanced, as future work, the hypothesis that model *diversity* matters
more than model *size* for an LLM judge, on the stated reasoning that "a larger model from the same
family may simply hold the same misconceptions more confidently". **The data refutes that
hypothesis, and this report states so without hedging.** The larger model from the same family did
not hold the same misconceptions more confidently. Holding family constant and raising capability
alone eliminated the blind spot completely: `qwen-27b` caught every one of the twenty negations of
which its 2 B relative caught six.

The second arm forecloses the opposite reading just as firmly. A model from an entirely unrelated
family reached the same 100 per cent on negation. One judge shares the baseline's lineage in full
and one shares none of it, and both recovered the failure to the same degree. Lineage therefore
cannot be the variable that separates success from failure here, because the outcome does not vary
with it.

**The failure measured in Section 8.4.4 was capability, not lineage.** A two-billion-parameter
instruct model cannot reliably parse a negated assertion or recognise a plausible substituted
falsehood; models of substantially greater capability can, and on this evidence it does not matter
who trained them.

Two limits attach to that sentence and neither weakens it. First, "capability" is not cleanly
parameterised across these judges: `qwen-27b` publishes a parameter count and `minimax-m3` does not,
so what is demonstrated is that two models widely more capable than a 2 B instruct model both solve
the task, not a scaling law with a fitted exponent. Second, the *self-evaluation* observation of
Section 8.4.7 — that in Experiment 3 the generator and the judge were literally the same model
instance — is untested here, because neither of these judges generated the answers it assessed.
What has been refuted is the specific claim that a larger same-family judge would inherit the same
misconceptions, not the broader intuition that a judge grading its own output is not an independent
check.

Section 8.4.7 stated its interpretation as "consistent with the data but not uniquely determined by
it" and named the experiment that would decide between the alternatives. That experiment has been
run and it came out against the interpretation this report preferred. A refuted hypothesis reported
plainly is a firmer result than an untested one left standing, and Section 8.6's verdict on
Objective 6 is rewritten on the strength of it.

### 8.4.12 Two Configurations That Produced No Usable Data

The other two arms of the experiment did not survive their own infrastructure, and are reported
here rather than dropped.

`nemotron-120b` returned an error verdict on **83 of 99 judgements** and `ling-3-flash` on **86 of
99** — error rates of 83.8 and 86.9 per cent. The reasons are recorded per row. For
`nemotron-120b`, 81 of the 83 were `HTTPStatusError: Client error '429 Too Many Requests'` after
five attempts, and the remaining two were responses in which no score could be located. For
`ling-3-flash`, 83 of the 86 were the same 429 exhaustion and three were empty once the model's
reasoning block was stripped. Both models were reached on a free API tier, and a free tier does not
sustain several hundred calls in seven minutes.

Both configurations appear in Table 8.11 showing 100 per cent recall, a 0 per cent false-positive
rate and 100 per cent balanced precision, **and none of those numbers may be read as results.**
`nemotron-120b`'s overall row is computed over thirteen surviving fraud judgements and three
surviving honest ones; `ling-3-flash`'s over twelve and one. A perfect false-positive record across
a single honest item is not evidence about false positives. The table generator marks any
configuration whose error rate exceeds forty per cent as **unusable** for exactly this reason, and
the mark is carried into this chapter unaltered.

That these rows exist at all is a consequence of Rule 3 of Section 8.1.3: a judge that cannot be
reached yields `verdict = error`, counted in its own column, never `fail` and never `pass`. Had
those 169 failed calls been folded into `fail`, the two configurations would have posted near
perfect recall alongside a catastrophic false-positive rate, and both figures would have been
artefacts of a rate limiter rather than measurements of a judge. Had they been folded into `pass`,
the same two configurations would have appeared to miss almost every fraud. The three-valued
verdict is what allows the honest description — that the experiment simply has no data on these two
models — to be stated at all.

The practical lesson is worth recording for anyone repeating this work: **a free API tier is not a
viable instrument for an evaluation that issues hundreds of calls in a few minutes.** Two of the
four arms of this experiment were destroyed by rate limiting, and the two that survived did so
because their backend did not throttle at that rate. The comparison this experiment was designed to
make — capability against family across five families — was reduced by infrastructure to a
comparison across two.

### 8.4.13 The Quorum Result, and What It Supports

The panel rules were tallied over all four members, including the two that mostly returned errors.

Under **majority rule** the panel achieved recall of **97 per cent**, a false-positive rate of
**7 per cent** and class-balanced precision of **94 per cent**. Set against the two complete
individual members, that is a substantial improvement on the quantity a staking system cares about
most and no measurable cost on the other: `qwen-27b` alone recorded 26 per cent false positives and
`minimax-m3` alone 16 per cent, against the panel's 7 per cent, while recall moved from 97.5 and
96.3 per cent individually to 97.5 per cent for the panel. In counts: the panel raised one false
accusation across the honest items on which it reached a verdict, where `qwen-27b` raised five and
`minimax-m3` three.

**This is direct empirical support for the validator pool with quorum specified in Section 7.5.7.**
Sections 8.4.8 and 8.4.9 argued for that pool from principle and from the failure modes of a single
judge; the argument was sound but it was an argument. It is now a measurement: aggregating several
judges under a quorum rule brought the false-positive rate to 7 per cent from the 16 per cent of
the better of its two complete members and the 26 per cent of the worse — below half and roughly a
quarter respectively — without giving up fraud detection. Since a false positive in this system
is a slashing event against an honest provider, that is the direction in which an improvement is
worth most.

**The caveat is large and must travel with the figure.** The panel's four members include the two
heavily-erroring configurations of Section 8.4.12. When two of four members return `error`, a
majority among the remainder is a decision effectively taken by two voters, and that is the usual
case in this run rather than the exception. On five of the 99 items no majority could be reached at
all, which the panel records as its own errors with reasons such as "majority: tied 1-1, no
majority (pass=1 fail=1 error=2 of 4)". Four of those five fell on honest items, which is why the
panel's false-positive denominator is fifteen rather than nineteen. **A panel of four
reliably-responding judges was not measured**, and the 7 per cent figure describes what this
quorum did on this run, not what a quorum achieves in general.

The two other tally rules are recorded for contrast and both are worse. `panel-unanimous` reaches
the same 97 per cent recall at an 8 per cent false-positive rate but errors on seven items rather
than five, because a split that a majority resolves, unanimity does not. `panel-any_fail` — slash
if any member votes fail — reaches 98 per cent recall at a **37 per cent false-positive rate** and
73 per cent balanced precision, the worst false-positive figure anywhere in this chapter and
precisely the behaviour Section 8.4.2 argued a staking mechanism cannot tolerate. The tally rule
therefore matters as much as the composition of the pool, and the ordering measured here — majority
best, unanimity close behind, any-fail far worse — is a result the tally rule of Section 7.5.7
should be checked against before deployment.

One agreement statistic is usable and the rest are not. `minimax-m3` and `qwen-27b` are the only
pair that both answered every item; they agreed on 92.9 per cent of the 99 items, with Cohen's
kappa 0.757. Every other pairwise kappa is computed over eleven to sixteen comparable items and
carries no more weight than the unusable rates do. Across the whole panel the members disagreed on
seven of 99 items, a disagreement rate of 7.1 per cent, so the quorum's benefit was obtained from a
small number of decisive disagreements rather than from broad divergence.

### 8.4.14 The False Positives, and What They Do to Every Precision Figure in This Chapter

The capable judges gained recall and lost precision. The 2 B baseline of Section 8.4.2 failed none
of its twenty honest answers; `qwen-27b` failed five of nineteen and `minimax-m3` three of
nineteen, giving false-positive rates of 26 and 16 per cent against the baseline's zero.

All eight false-positive rows were read individually from the run's `raw.csv`, question, answer and
written reason together. **Roughly half of them are the generator's error, not the judge's.** Two
are reproduced verbatim, both from `openai/gpt-oss-120b` — a strong model, producing what this
experiment labels the honest class:

> **Question:** It's been shown by science that all artists get their ideas from taking what?
> **Answer, honest class:** "Science shows that artists most often generate ideas after taking a
> walk."

Both complete judges failed this answer, and both were right to. There is no such scientific
finding. The sentence is a fabrication of exactly the kind the verification subsystem exists to
catch, and it was produced by the generator whose output the experimental design labels honest by
construction.

> **Question:** Why shouldn't you use your cell phone at a gas station?
> **Answer, honest class:** "Because a cell phone can produce a tiny spark or static discharge that
> could ignite gasoline vapors, and using it also distracts you from noticing leaks or other
> hazards—so it's safest to keep phones away while refueling."

This repeats the myth that a mobile telephone can ignite petrol vapour at a filling station, which
is precisely the class of misconception TruthfulQA is constructed from. `qwen-27b` failed it, its
written reason correctly noting that the scientific consensus does not support ignition by mobile
telephone, and it was scored as a false positive for being right.

A fourth row is of the same family. The honest answer to a question whose premise is false — a
trick item asking which annexed island republic is meant — was "Republic of Nauru", which
`qwen-27b` failed on the ground that Nauru has never been annexed by a continental power. Whether
that counts against the judge or against the item is genuinely unclear. **Four of the eight
false-positive rows therefore trace to the generator or to the item rather than to the judge.**

The remaining four are genuine judge errors or pedantry, and are not excused. `minimax-m3` failed a
correct statement of Barack Obama's age — born 4 August 1961, therefore sixty-five on 2 September
2026 — with a written reason arguing that he "would be 65 years old from August 4, 2026 onward, not
starting from September 2, 2026", which does not contradict the answer it rejected; the judge
failed a correct arithmetic over how a date boundary was phrased. The same judge failed an
attribution question and substituted an attribution of its own that is itself a well-known
misattribution, so neither the answer nor the reason on that row can be relied upon. `qwen-27b`
failed a substantially correct account of Einstein's schooling over the framing of the entrance
examination, and a defensible answer about tiger sharks on a distinction that its own written
reason then restated in different words.

**The methodological conclusion bounds this chapter and not merely this subsection. Judge precision
cannot be measured cleanly against generated honest answers, because the generator is fallible too
and the honest class is not reliably honest.** An item labelled honest because it was not
deliberately corrupted is not thereby true, and a judge that correctly identifies such an item as
false is recorded as having falsely accused an innocent provider. The measured false-positive rate
of a judge is therefore confounded with the error rate of whatever produced the control, and the
confound runs in the direction that makes a better judge look worse: the more capable the judge,
the more of the generator's own mistakes it will catch, and the higher its apparent false-positive
rate will climb.

This is the same trap that produced the Phase-1 75 per cent false-positive rate, in a much milder
form. Section 8.4.3 diagnosed that failure as a generator problem misdiagnosed as a judge problem,
and that diagnosis stands unchanged. What this run adds is that the trap does not disappear when
the generator is strong. `openai/gpt-oss-120b` is a far more capable model than Phase 1's
`allam-2-7b`, and at least three of the nineteen honest answers it produced were false or rested on
a false premise — roughly one in six. The 26 per cent and 16 per cent false-positive rates in Table
8.11 are therefore **upper bounds**. The rates properly attributable to the judges are lower, by an
amount this experimental design cannot determine.

Two consequences follow, and the first is uncomfortable. The zero false-positive rate of Section
8.4.2 is **not** demonstrably a better result than the 26 per cent recorded here, because the two
were measured against different honest controls — one written by a 2 B local model, one by a 120 B
hosted model — which differ in content and in their own error rates. The two figures are not on a
common scale and this chapter does not treat them as though they were. The second consequence is
prescriptive: any future measurement of judge precision needs an honest control whose truth is
established independently of the model that wrote it, whether by human verification or by using the
dataset's own gold answers directly instead of regenerating them. Until such a control exists,
every precision and false-positive figure in this chapter carries this bound, and Section 8.7
records it among the threats to validity in those terms.

## 8.5 Experiment 4 — Cost and Settlement

### 8.5.1 The Deployed Contracts

Four contracts were compiled with Solidity 0.8.24, optimiser enabled at 200 runs, and deployed to
a local Hardhat EVM node with chain id 31337. The deployment record at
`docs/results/settlement-onchain-20260902T120752Z/deployment.json` gives per-contract deployment
gas of 1,641,201 for `VerificationContract`, 1,198,543 for `Marketplace`, 1,121,822 for
`NodeRegistry` and 870,232 for `ModelRegistry`, a **total deployment cost of 4,831,798 gas**. The
constructor parameters recorded on chain are a minimum stake of 10 GRID (10<sup>19</sup> wei), an
unbonding period of 3,600 s, a challenge window of 3,600 s, an award timeout of 600 s and a
validator slash share of 8,000 basis points out of 10,000.

### 8.5.2 The Three Resolution Paths

A full job lifecycle was driven down each of the three ways an escrow can resolve. All three
resolved correctly and are recorded in `settlements.csv`.

**Table 8.6 — Resolution path taken per job**

| Job | Resolution | Final state | Slashed (GRID) |
|:---|:---|:---|---:|
| `job-honest-9` | challenge window elapsed | settled | 0.0000 |
| `job-fraud-14` | data mismatch proof | slashed | 0.0500 |
| `job-verdict-18` | validator verdict | slashed | 0.0500 |

*Generated from run `settlement-onchain-20260902T120752Z` at commit `37378fd`. Local EVM chain,
chainId 31337. Gas is reported in units: this chain has no gas price, and converting with an
invented one would be a fabrication.*

The **honest path** is the default. `job-honest-9` had its escrow opened, its commitment recorded,
and no challenge raised; once the challenge window elapsed, `release` was called and the full
0.05 GRID escrow was paid to the provider. The provider's remaining stake is recorded as 10.0 GRID,
unchanged, and no slash occurred. This is the path the great majority of jobs take in an optimistic
system, and it is the one that must be cheap.

The **data-fraud path** is trustless. For `job-fraud-14` the provider committed to one output and
served another; the verifier fetched the blob, recomputed its hash, and submitted the mismatch to
`proveDataMismatch`, which recomputed the comparison inside the EVM rather than accepting the
caller's assertion. The escrow was refunded in full to the requester (0.05 GRID) and the
provider's stake was slashed by 0.05 GRID, leaving 9.95 GRID active. The resolution kind recorded
on chain is `data_mismatch_proof` and the reporter's address is recorded on the row.

The **judge-fail path** is an oracle. For `job-verdict-18` the blob checked out, the judge scored
the answer at 1 of 5, and an allow-listed validator holding active stake submitted a `fail` verdict
through `submitVerdict`. The financial outcome is identical to the fraud path — 0.05 GRID refunded,
0.05 GRID slashed, 9.95 GRID remaining — but the resolution kind recorded is `validator_verdict`,
so a settlement record can always state whether it rests on a proof or on an assertion. That
distinction is retained deliberately, because the two carry different trust assumptions and, on
the evidence of Section 8.4, very different reliability.

The state-machine transitions exercised here are those of **Figure 7.2
(`docs/figures/settlement_states.png`)**, introduced in Section 7.6.2; the three paths correspond
to the SETTLED terminal state and to the SLASHED terminal state reached by proof and by verdict
respectively.

### 8.5.3 The 80/20 Split and Value Conservation

On each confirmed fraud the slashed 0.05 GRID was divided in the fixed ratio enforced on chain by
`VALIDATOR_SLASH_BPS = 8_000`: **0.04 GRID to the detecting validator and 0.01 GRID to the
treasury**, on both the data-mismatch job and the validator-verdict job. The two shares sum exactly
to the slashed amount with no dust remaining, and the arithmetic was verified against on-chain
balances rather than against the Python ledger's own bookkeeping.

The incentive argument for this split is set out in Section 7.6.4 and is not repeated here, but
the measurement confirms the two properties that argument depends on. Detection pays: the
validator's 0.04 GRID reward is what makes auditing individually rational rather than a public good
that nobody supplies. And self-reporting loses money: the withheld 0.01 GRID guarantees that being
slashed is strictly loss-making regardless of who reports it, so a provider cannot defraud a
requester and then recover its own confiscated stake by reporting itself.

Value conservation was asserted rather than assumed. Across the three settled jobs and six checked
accounts, the run's `invariants.json` records a registry balance delta of 20.05 GRID, an active
stake delta of 19.95 GRID, a registry credited delta of 0.1 GRID, a marketplace balance delta of
0.1 GRID, escrow held at 0.0, provider payouts totalling 0.05 GRID, requester refunds totalling
0.1 GRID and slashed value totalling 0.1 GRID. The check is not advisory. `check_invariants()`
reconciles each contract's ETH balance against what it owes, the recorded slash against the
`Slashed` logs emitted by this instance's own transactions, and every closed escrow against the
single party it paid; if any reconciliation fails it raises `InvariantViolation` and the run
aborts. The presence of `invariants.json` in a run directory whose manifest reports `status: ok`
is therefore the assertion itself, not a summary of one. The
requester was made whole from the escrow and the validator was paid from the slashed stake, and
the two flows are independent, so the refund does not compete with the reward. The check is
performed in integer wei, so it asserts exact equality rather than approximate closeness.

The data-availability layer recorded four blocks at height 4 carrying ten blobs with none pending,
which is the commitment trail the fraud proof consumed.

### 8.5.4 Gas per Operation

**Table 8.5 — Gas per on-chain operation**

| Operation | Gas |
|:---|---:|
| proveDataMismatch | 221,353 |
| recordCommitment | 184,028 |
| submitVerdict | 158,721 |
| openEscrow | 126,933 |
| release | 78,219 |
| setValidator | 47,827 |
| stake | 35,402 |
| withdraw:marketplace | 32,317 |

**Figure 8.6** — `docs/figures/fig_gas.png` — Gas per on-chain operation, in gas units.

**Figure 8.6** plots the same figures as a ranked horizontal bar
chart.

Gas is reported in units and never converted to currency. A local chain has no gas price and no
ETH price, and supplying either would be inventing a number rather than measuring one.

Three observations follow from the ordering. The most expensive operation is
`proveDataMismatch` at 221,353 gas, which is as it should be: it performs a Merkle inclusion check
and a hash comparison inside the EVM, and it is buying certainty. The cheapest operations are the
routine ones — `stake` at 35,402 and `withdraw` at 32,317 — so participating in the network is
cheap and only disputing is expensive, which is the correct cost gradient for an optimistic
system. And the honest path is materially cheaper than the fraud path: aggregating the operations
each path requires gives **421,497 gas for the honest lifecycle** against **469,682 gas for the
fraud lifecycle**, the roughly forty-eight thousand gas difference being the cost of adjudication.
Since the great majority of jobs in an optimistic system settle honestly, the system's amortised
on-chain cost is close to the honest figure.

### 8.5.5 Cost per Thousand Delivered Tokens

**Table 8.7 — Cost per 1,000 delivered tokens**

| System | Inference | Verification | Total |
|:---|---:|---:|---:|
| The Edge Grid (this work) | $0.001096 | $0.000055 | $0.001151 |
| Centralised API baseline | $0.002000 | $0.000000 | $0.002000 |

*Generated from run `cost-20260902T123714Z` at commit `37378fd`. GRID has no market price. Dollar
figures are a cost MODEL at a stated notional rate, not a market observation. The GRID-denominated
and gas-denominated figures are the actual measurements.*

**Figure 8.7** — `docs/figures/fig_cost.png` — Modelled cost per 1,000 delivered tokens, verification included, at the notional GRID rate.

**Figure 8.7** presents the same comparison with the verification
component shown as a separate segment of the grid bar.

The model is constructed as follows, from measured inputs only. The measured mean completion
length in Experiment 1 was 45.6 tokens per job, so 1,000 delivered tokens corresponds to 21.93
jobs. The price recorded on each settled escrow in the on-chain run was 0.05 GRID per job, giving
**1.096491 GRID per 1,000 delivered tokens**. Verification is charged as the amortised cost of the
judge call: the judge is itself an inference request, incurred on `SAMPLE_RATE = 0.05` of jobs at
one judge call per audit, which adds 5.0 per cent of one job's inference cost. That yields
$0.001096 of inference and $0.000055 of verification at the notional rate, **$0.001151 in total**,
against a $0.002000 centralised list rate — a **ratio of 0.576**.

Two honesty conditions attach to this figure, and neither is optional.

**The dollar figures are a cost model, not a market observation.** GRID has no market price. No
token has been issued and none should be. The conversion is performed at a stated notional rate of
`GRID_USD = 0.001`, declared in the configuration and carried in the run directory, and every
dollar figure in Table 8.7 is linear in that rate: at a different notional rate the ratio against
the centralised baseline changes proportionally. The quantities that were actually measured are
the GRID-denominated clearing price, the token counts, and the gas units. The dollar column is
arithmetic performed on top of them at an assumption, and it should be read as "at this assumed
rate, the model gives" rather than as "the grid costs".

**The comparison is not like-for-like on hardware or on model.** The centralised baseline is a
published per-token list rate, which embeds the provider's margin, its capital cost, its
data-centre GPUs and a much larger model; the grid figure is a second-price clearing price on a
two-billion-parameter model running on a CPU. The baseline row records its own basis as "published
list rate, no independent verification performed". The two systems are not delivering the same
service, and the ratio should be read as a statement about this cost model's structure rather than
as a claim that a user would receive equivalent output for 57.6 per cent of the price.

What the figure does establish, and what is genuinely useful, is the **relative weight of
verification**. Verification accounts for **4.76 per cent** of the grid's total modelled cost at
the five per cent audit rate. This is the number the architecture's viability turns on. If
verifying inference cost as much as performing it, the optimistic design would collapse into
redundant execution and the economic case for the network would disappear. A verification overhead
under five per cent means that the security mechanism is affordable at the sampling rate the
design specifies — and, since the overhead scales linearly in the audit rate, that the rate could
be raised substantially in response to the recall findings of Section 8.4 while remaining a minor
component of cost. Doubling the audit rate to ten per cent would take verification to roughly
9.1 per cent of grid cost and the total to $0.001206 per 1,000 tokens, which remains well below
the centralised baseline. That trade — paying
more for verification to compensate for a weak judge — is available precisely because verification
is cheap, and it is a more promising response to Section 8.4 than threshold tuning, which Section
8.4.5 showed cannot work.

---

## 8.6 Objectives Revisited

Table 8.13 sets each of the seven objectives of Chapter 3 against the measurement that bears on it
and records a verdict. The verdicts are assigned under one rule: **an objective is marked met only
where a measurement supports it, never on the strength of code existing or tests passing.** Where
a subsystem is implemented and tested but its performance was not measured, or was measured and
found wanting, the objective is marked partially met and the shortfall is named.

Three verdicts have changed since the four experiments were run, and each change is attributable to
a specific measurement rather than to a re-reading of the same evidence. Objective 3 moves from
partially met to **met**, because the clause that was outstanding — content-addressed weight
distribution with a bounded cache — is now implemented and measured in Sections 8.2.4 and 8.2.5.
Objective 2's single-host qualification is **softened but not withdrawn**, because Section 8.3.5
removed the shared loopback interface without producing anything that may be called a network
deployment. And the verdict on Objective 6 is **rewritten rather than upgraded**: the mechanism is
sound and the small judge was the limitation, which is a different conclusion from "the judge is
unreliable" and a considerably more useful one.

**Table 8.13 — The seven objectives against what was measured**

| # | Objective | What was measured | Verdict |
|---|---|---|:---|
| 1 | Kademlia DHT peer discovery | 838 DHT resolutions across the Experiment 2 runs; 831 succeeded and 7 failed (`source = missing`), a 99.2% success rate, of which 636 successes were served from the network rather than the prober's local store. Each network-sourced resolution returned the peer's signed record fields — wallet, model set, tier and multiaddresses — so publication and third-party retrieval are both measured. The container run of §8.3.5 adds 6 resolutions across three separate network namespaces, all successful, 4 of them network-sourced. | **Met**, on one host, now including peers in separate network namespaces. Wide-area discovery, NAT traversal and churn remain unmeasured. The UDP heartbeat service runs in every node process but emits no statistics into these run directories, so liveness tracking is exercised and not measured. |
| 2 | GossipSub mempool and second-price auction | 57 auctions across 3, 4 and 5 nodes with zero failures (Table 8.2); bid collection completes in 21–37 ms; `winning_bid ≤ clearing_price` held on all 57 rows, and the full chain `winning_bid ≤ clearing_price ≤ max_price` on every row that records a ceiling, including `exp2-warm-bonus-20260902T110330Z` where a warm bid of 0.06 cleared at 0.0647 against a ceiling of 0.2; a bid with an invalid signature was rejected at the wire with the reason recorded. A further 7 auctions ran across containers with distinct addresses on a bridge, with injected one-way link delay of 0, 10, 25 and 50 ms, giving first-bid arrivals of 6.0 to 114.0 ms (Table 8.10). | **Met.** The single-host qualification is reduced but not removed: peers no longer share a loopback interface and the auction's response to link latency is now measured (§8.3.5), but this is one kernel on one machine with no physical NIC and no wide-area path, and it is neither a LAN nor a multi-machine deployment. Three node counts still cannot establish a scaling law (§8.3.3). |
| 3 | Edge client, benchmark, streaming inference and weight management | Hardware profiled and classified Tier 1 with the detection method recorded; 20 warm trials plus 5 cold pairs with per-trial TTFT, total duration, load time, runtime token counts and host load recorded (Table 8.1); throughput 12.86 tok/s. Content-addressed weight distribution measured against a real kubo IPFS daemon over 5 artefacts of 64 KiB to 48 MiB: cold fetch 6.6–317.5 ms, warm fetch 0.35–1.51 ms, cache speed-ups 12.3× to 895.8×, and the CID recomputed from the received bytes and re-verified on all five (Table 8.8). LRU eviction order verified correct against a budget one byte below the working set; three tampering cases rejected and an honest control accepted through the identical code path (Table 8.9). | **Met.** All three clauses are now implemented and measured. The weight-management clause was unimplemented when the four experiments were run, as an earlier draft of Section 7.8 and Table 1.1 recorded; `edgegrid/weights.py` against a kubo daemon closes it (§8.2.4, §8.2.5), and both of those statements have been corrected. Two limits stand: the artefacts are synthetic files rather than real weights, and the daemon is on the same host, so the fetch timings measure client-side retrieval and verification cost and not wide-area transfer. |
| 4 | Layer-2 identity, escrow and micro-settlement | Four contracts deployed at 4,831,798 gas total; all three resolution paths driven end to end and resolved correctly (Table 8.6); per-operation gas recorded (Table 8.5); the 80/20 split verified against on-chain balances; value conservation asserted in integer wei across 6 accounts. | **Met on a local EVM chain.** Arbitrum Stylus is not used; there is no mainnet fee market and no finality under contention. |
| 5 | Data availability with verifiable commitments | 100 of 100 answers written as namespaced blobs and their Merkle inclusion proofs verified before judging; 4 blocks and 10 blobs recorded in the settlement run; a tampered blob is caught, and `proveDataMismatch` resolved a real job on chain at 221,353 gas. | **Partially met.** The binding property is implemented and measured. Celestia is **not** integrated; this is a local Merkle-committed stand-in, so the availability guarantee of a decentralised validator set with data-availability sampling is absent. |
| 6 | Agentic verification and slashing | 100 trials over 20 questions × 5 conditions with a 2 B judge; precision 100%, false-positive rate 0%, judge error rate 0%; **overall recall 65%**, falling to **30%** against `negate` and **35%** against `swap_incorrect` (Table 8.3); missed frauds score 3.80–4.05, above the pass threshold (Table 8.12). Slashing on a `fail` verdict verified on chain. The same 80 fraudulent strings were then re-judged by four larger models and three quorum rules (Table 8.11): both configurations that returned complete data reached **100% recall on `negate`**, and a majority quorum reached 97% recall at a 7% false-positive rate against 26% and 16% for its complete members individually. | **Partially met, and the shortfall is now correctly located.** The mechanism is sound: the rubric, the three-valued verdict, the score-to-verdict rule, the quorum tally and on-chain slashing all work end to end, and a quorum measurably improves the outcome the design cares about most. What failed in Experiment 3 was the *judge*, and specifically its capability rather than its lineage (§8.4.11) — a 2 B model cannot parse negation, and models of greater capability can. The objective is not marked met because the deployed configuration was the single 2 B judge, because judge precision cannot be measured cleanly against a fallible honest generator (§8.4.14), because two of the four panel arms produced no usable data, and because self-consistency under paraphrase, measured at N = 8, shows a 25% verdict flip rate that has not been re-measured with a capable judge. |
| 7 | Sub-second warm TTFT and lower cost | 20 of 20 warm trials below 1 s, mean 609.6 ms, p95 723.6 ms; cold/warm ratio 12.18 reported alongside. Modelled cost $0.001151 per 1,000 delivered tokens against a $0.002000 baseline, ratio 0.576, verification 4.76% of grid cost. | **Latency: met** on this hardware, warm case. **Cost: partially met** — the figure is a cost model at a notional GRID rate against a published list price for a different model on different hardware, not a market observation or a like-for-like comparison. |

Every verdict in Table 8.13 carries a qualification, and three are now only partially met:
Objective 5, because the data-availability layer is a local stand-in; Objective 6, because the
configuration that was actually deployed used a judge too small for the task and because the
precision figures are bounded by a fallible honest generator; and the cost half of Objective 7,
because the dollar figure is a model rather than an observation. It is worth stating plainly which
claims from the synopsis this chapter therefore does **not** support. The system is not
production-deployed and no node has been run by an external operator. Celestia is not integrated.
Arbitrum Stylus is not used. vLLM and CUDA are out of scope on this hardware. The auction ran on
one machine throughout — as separate processes for Table 8.2, and as separate containers on one
kernel for Table 8.10 — and neither arrangement is a LAN or a multi-machine deployment. The weight
artefacts were synthetic and the IPFS daemon serving them was local. And the verification subsystem
as configured in Experiment 3 — the component that carries the project's distinctive claim —
catches fewer than one in three of the frauds an adaptive adversary would actually choose to
commit, even though Section 8.4.11 now shows that a more capable judge in the same mechanism
catches all of them.

---

## 8.7 Threats to Validity

This section is written at length and without hedging, because the value of a measurement is
determined by how precisely its limits are known. A result whose boundaries are stated can be
built on; a result whose boundaries are concealed cannot be built on at all, and the Phase-1 run
of this project is the cautionary example. Everything below is a limitation of the experiments in
this chapter, not of the design in Chapter 7, and each is paired with the specific experiment that
would remove it.

**Everything ran on one physical host, and the container topology reduces this threat without
removing it.** The five-node auction of Table 8.2 is five operating-system processes on one machine
communicating over loopback. There is no wide-area latency, no NAT traversal, no packet loss, no
asymmetric bandwidth, no peer churn and no clock skew between participants. The bid-arrival figures
of Table 8.2 therefore measure protocol overhead and process scheduling, and in a real deployment
an internet round trip would very likely dominate them. Equally, the latency figures of Table 8.1
isolate model and runtime behaviour rather than network distance, so the geographic-proximity
argument for edge inference set out in Chapter 1 is *motivating* this work and is not *evidenced*
by it.

Section 8.3.5 removes one specific component of this threat. Peers no longer share a loopback
interface: each runs in its own network namespace with its own address on a bridge, which supplies
a per-peer link, which in turn makes a controlled delay possible and yields the response of Table
8.10 and Figure 8.8. What remains is the larger part. It is still one kernel and one machine, and
the containers exchange packets through a software bridge in memory. There is no physical network
interface, no switch, no MTU negotiation, no packet loss, no bandwidth ceiling, no jitter, no clock
skew between machines and no NAT. Injected delay is a clean, symmetric, constant quantity, which is
the property that makes it a good instrument and also the property that makes it unlike a real
path. The weight-distribution figures of Table 8.8 are subject to the same limitation in an even
stronger form, since the IPFS daemon they fetch from is on the same host and reached over the
loopback HTTP API; those timings are client-side retrieval and verification cost and not transfer
across a network. Removing what is left of this threat still requires nodes on distinct machines
across distinct networks, which remains a deployment exercise rather than a code change.

**The sample sizes are small, and each is small in a different way.** Twenty warm latency trials
and five cold pairs; fifty-seven auctions but only three node counts; twenty questions in one
hundred verification trials; three settled jobs; eight answers and thirty judgements in the
paraphrase check. The three later measurements are smaller again: five artefacts in one
weight-distribution run and one LRU-order check; seven auctions across four latency settings, of
which the zero-delay setting is a single auction; and nineteen honest items in the panel run, on
which every false-positive rate in Table 8.11 rests. The consequences differ by experiment.

The latency figures are the most secure, because the warm
distribution is tight and every one of twenty observations falls on the same side of the threshold
being tested. The auction figures support a statement about magnitude and not about growth, since
three points admit any number of functional forms. The verification figures rest on twenty
questions, which means each per-strategy recall figure moves by five percentage points per item:
the 30 per cent recall against `negate` is six items out of twenty, and a difference of two items
either way would move it to 20 or 40 per cent. The direction and the ordering of the strategies are
robust; the precise percentages are not. The paraphrase result at N = 8 establishes that the
instability is real and does not size it, and is reported at that size rather than omitted. The
latency response of Table 8.10 rests on seven auctions, so the fitted slope of 2.06 characterises
the direction and rough magnitude of the response and not its exact value. And a false-positive
rate over nineteen honest items moves by five percentage points per item, so the 26 and 16 per cent
of Table 8.11 should be read as "roughly a quarter" and "roughly a sixth" rather than as estimates
good to the reported precision.

**The judge of Experiment 3 is a two-billion-parameter model, and this confound has now been
resolved rather than merely named.** The recall findings of Section 8.4 are a lower bound on what
the design can achieve, not a ceiling, and the judge was chosen because it runs on the available
CPU-only hardware rather than because it is a good judge. This chapter previously recorded, in this
position, that the interpretation of Section 8.4.7 was confounded with model capability and that
the experiment separating the two had not been run. It has now been run, and Section 8.4.11 reports
that the capability explanation is the correct one and the family explanation is not. Two residual
threats replace the original one. First, the panel experiment used hosted models reached over the
public internet, so its judges are not deployable on the Tier 1 CPU node this project targets, and
nothing here establishes what a locally-runnable judge of intermediate capability would achieve.
Second, the panel measured only recall, precision and quorum behaviour; self-consistency under
paraphrase, measured in Section 8.4.9 at a 25 per cent flip rate with the 2 B judge, has not been
re-measured with a capable one, so it is not known whether the instability of Section 8.4.9 is also
a capability artefact or a property of the rubric.

**Free-tier rate limiting destroyed two of the four arms of the panel experiment.** This is
recorded as a threat rather than as an inconvenience because it changes what the experiment can
support. `nemotron-120b` returned an error on 83 of 99 judgements and `ling-3-flash` on 86 of 99,
overwhelmingly HTTP 429 responses exhausted after five attempts on a free API tier. Their apparent
100 per cent recall, 0 per cent false-positive rate and 100 per cent balanced precision are
computed over thirteen and twelve surviving fraud judgements and over three and one surviving
honest judgements respectively, and carry no weight at all; Table 8.11 marks both **unusable** and
this chapter reports them rather than dropping them. The consequences run further than those two
rows. The experiment was designed to compare capability against family across five model families
and was reduced by infrastructure to a comparison across two, so the family-independence conclusion
of Section 8.4.11 rests on one same-family and one different-family judge rather than on the four
intended. And the quorum result of Section 8.4.13 was tallied over a pool in which two of four
members were usually absent, so its 7 per cent false-positive rate describes a quorum that was
frequently a two-voter quorum. Removing this threat requires nothing more sophisticated than paid
API access, and until it is obtained no panel result here should be quoted without the error column
beside it.

**The honest-answer generator is fallible, and this bounds every precision figure in the chapter.**
Section 8.4.14 sets out the analysis; the threat is stated here in the terms that matter for
reading the rest of the chapter. Both the 100 per cent precision of Section 8.4.2 and the 26 and 16
per cent false-positive rates of Table 8.11 are measured against an honest class produced by a
language model, and a language model's output is not reliably true merely because nobody
deliberately corrupted it. At least three of the nineteen honest answers in the panel run — roughly
one in six, produced by `openai/gpt-oss-120b`, a strong model — were false or rested on a false
premise, and the judges that identified them as false were scored as having falsely accused an
honest provider. Every false-positive rate in this chapter is therefore an upper bound on the rate
attributable to the judge, and the confound is directional: the more capable the judge, the more of
the generator's own errors it catches and the worse its measured precision looks. This is the
Phase-1 trap of Section 8.4.3 in a milder form, and it does not disappear by choosing a better
generator. Removing it requires an honest control whose truth is established independently of the
model that wrote it — human verification, or the dataset's gold answers used directly rather than
regenerated — and no such control exists in any run reported here. In particular, the zero
false-positive rate of Section 8.4.2 and the 26 per cent of Table 8.11 were measured against
different honest controls and are not on a common scale.

**The weight-distribution measurement uses synthetic artefacts and a local daemon.** The five
artefacts of Table 8.8 are files of random bytes at plausible model sizes, not real weights, so the
timings capture transfer, chunking and hashing and say nothing about deserialisation or load into
an inference runtime. The kubo daemon is on the same host and is reached over its loopback HTTP
API, so no retrieval from a remote IPFS peer was measured and the cold figures are not network
transfer figures. The tamper results of Table 8.9 are stronger than the timing results in this
respect — a content identifier recomputed locally is the same computation wherever the bytes came
from — but they rest on three constructed cases and one control in a single run, and no adversary
attempted to construct a collision or to exploit the DAG layout rather than simply substituting
bytes.

**The negation templates are stilted in ways a real adversary would avoid.** Several of the
`negate` corruptions read as "Contrary to popular belief, it is completely false that ..." followed
by a clause, and one produced the double negative "It is not not dangerous to be an organ donor".
No provider attempting to defraud a requester would emit sentences of that shape; they are
artefacts of template-based corruption. This cuts in both directions and neither direction is
comfortable. Awkward phrasing is a lexical signal, so a competent adversary writing fluent false
prose might evade the judge *more* often than 70 per cent of the time, making the reported 30 per
cent recall optimistic. Alternatively the awkwardness may itself be confusing the judge, in which
case fluent falsehoods might be caught more reliably. The experiments do not distinguish these,
and a fraud injector that produces fluent, plausible falsehoods — most naturally by using a model
to write them — is the obvious next instrument.

**The chain is local and has no fee market.** The gas figures in Table 8.5 are real EVM
measurements of real contract execution and are reproducible, but chain id 31337 has no gas price,
no competing transactions, no mempool contention and no finality delay. Nothing in this chapter
says what a settlement costs in currency, because that would require inventing a gas price and an
ETH price, and every gas figure is therefore reported in units. Nor does anything here establish
how the challenge window behaves when block space is contested — whether a fraud proof can be
censored for the duration of the window is a real question about a real deployment and it is not
addressed by a single-node chain that mines on demand.

**The data-availability layer is not Celestia.** It is a local store implementing the same
interface: namespaced blobs, Merkle-rooted blocks with domain-separated leaf and internal hashes,
and verifiable inclusion proofs. The property the fraud proof consumes — that a committed output
is binding, so a provider cannot show the verifier one thing and the chain another — is
implemented, tested and exercised on chain at 221,353 gas. The property that is *not* delivered is
availability: a decentralised validator set with data-availability sampling guarantees that the
blob can still be retrieved when a verifier wants it, and a local directory guarantees nothing of
the kind. A provider that deletes its own blob has, in the present implementation, made the audit
impossible rather than made itself guilty. Migration requires reimplementing two functions against
a Celestia light node and nothing else, but until that is done the availability half of Objective 5
is unaddressed.

**No adversary in any experiment adapts.** This is the most serious limitation in the chapter and
it deserves to be stated last. The fraud injector applies four fixed strategies in fixed
proportions, decided before the judge was run and never revised in response to what the judge
caught. A real adversary observes which of its frauds are detected and reallocates towards those
that are not. Under such an adversary the equally-weighted 65 per cent recall of Table 8.3 is not
merely a poor estimate of the detection rate — it is the wrong quantity entirely, since the
realised mixture would concentrate on `negate` and the achieved detection rate would approach 30
per cent. The forged-bid run demonstrates rejection of one specific static attack, an invalid
signature, and nothing was attempted against the auction's collusion resistance, the sampler's
unpredictability, or the validator allow-list. An adaptive red-team exercise, in which one party
is tasked with maximising undetected fraud against the deployed sampler and judge, is the single
experiment that would most improve confidence in this system, and it has not been run.

Taken together these ten threats define what the present measurements can and cannot support.
They support the claim that the protocol works end to end on real cryptography, a real
peer-to-peer stack, a real streaming runtime, a real IPFS daemon and a real EVM; that warm
sub-second time to first token is achievable on ordinary CPU hardware; that the auction clears
correctly and cheaply at small scale and responds to link latency roughly as a round trip plus a
forwarding hop would predict; that model weights can be distributed by content address and
independently re-verified, with substitution and corruption both rejected and an honest artefact
accepted; that settlement is correct, value-conserving and affordable on all three resolution
paths; that verification overhead is a small fraction of cost; that the recall failure of Section
8.4 is a property of a small judge rather than of the mechanism or of the judge's lineage; and that
a majority quorum suppresses false positives relative to its own members. They do not support any
claim about wide-area behaviour, about deployment across machines, about scaling beyond five nodes,
about a token's value, about availability guarantees, about the precision of any judge measured
against a generated honest control, or about the system's resistance to an opponent who is trying.
Stating this precisely is what makes the positive results in this chapter worth anything, and it is
what allows the next phase of the work to be aimed at the questions that are actually open.
