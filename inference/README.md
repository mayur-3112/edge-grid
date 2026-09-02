# Edge Inference Engine (Module 3, Objective 7)

Owns: the streaming Ollama wrapper, hardware benchmarking, hardware-tier classification,
and the signed `InferenceResult` that goes back over P2P.

- `engine.py` — `InferenceEngine`: streaming generation, real TTFT, real token counts, warm
  detection, named failure modes.
- `benchmark.py` — TTFT/throughput benchmark, cold-vs-warm measurement, `classify_tier()`,
  `hardware_profile()`, and an opt-in hosted baseline.

## Why this is streaming

Time to *first* token is the project's only quantified promise. The previous engine called
Ollama with `"stream": false`, which makes TTFT not merely unmeasured but **structurally
unmeasurable** — with a non-streaming call the HTTP response does not exist until generation
has finished, so the earliest observable moment is the last one. It also reported
`tokens_generated = len(output.split())`, a word count that runs 30–40% below the real token
count for English prose.

Both are fixed by consuming Ollama's NDJSON stream: TTFT is stamped at the first chunk whose
`response` field is non-empty (the final `done` chunk carries an empty `response`, and counting
it would silently mis-time empty generations), and `eval_count` / `prompt_eval_count` /
`eval_duration` are read off the final chunk, so token counts and throughput come from the
runtime's own tokenizer and clock.

## Measured on this machine

16 logical cores, 30.9 GB RAM, **no GPU**, `qwen3-vl:2b-instruct` (2.1B, Q4_K_M), 64 max tokens,
8 warm trials (warmup discarded) + 3 cold/warm pairs per run.

Six runs, taken at different machine loads, because a single run of this benchmark on a shared
CPU is not a measurement of the node — it is a measurement of the node *and* whatever else was
running. All six are in `docs/results/`; none has been deleted or overwritten.

| run | load avg | warm TTFT mean | median | p95 | warm `load_ms` mean | tok/s | cold TTFT mean | ratio |
|---|---|---|---|---|---|---|---|---|
| `…T110619Z` | ~8 | **545 ms** | 549 ms | 590 ms | 457 ms | 11.95 | 7122 ms | 8.0× |
| `…T101341Z` | idle | 798 ms | 795 ms | 926 ms | 585 ms | 9.39 | 8913 ms | 13.2× |
| `…T101953Z` | light | 992 ms | 1034 ms | 1355 ms | — | 5.83 | 10576 ms | 14.9× |
| `…T105148Z` | ~13 | 3019 ms | 873 ms | 9447 ms | — | 10.63 | 9643 ms | 9.2× |
| `…T105620Z` | ~11 | 5006 ms | 5402 ms | 9936 ms | 698 ms | 11.80 | 8888 ms | 1.7× |
| `…T104545Z` | ~7 | 6434 ms | 6294 ms | 11285 ms | — | 11.96 | (aborted) | — |

The `…T110619Z` row is the best warm figure measured on this machine and the only one taken with
the full provenance columns in place: 8 of 8 trials warm, 0 excluded, 0 rows with missing runtime
counters, `served_models = ['qwen3-vl:2b-instruct']`.

### What is actually robust, and what is not

An earlier version of this file claimed the **13–15× cold/warm ratio** as "the robust result".
The later runs falsify that: at load average 11 the ratio collapses to **1.7×**. The ratio is not
robust, because its denominator is not.

What *is* stable across every run is the **cold model load**, which Ollama reports itself as
`load_duration`:

| | `…110619Z` | idle `…101341Z` | loaded `…105148Z` | loaded `…105620Z` |
|---|---|---|---|---|
| cold `load_duration` mean | 6627 ms | 8364 ms | 9148 ms | 7766 ms |
| warm `load_duration` mean | 804 ms | 585 ms | 908 ms | 853 ms |
| TTFT penalty (cold − warm) | 6226 ms | 8235 ms | 8590 ms | 3514 ms |

**Loading this model costs 6.6–9.1 s and that figure moves far less with machine load than warm
TTFT does** (which spans 545 ms to 5006 ms across the same runs). The ratio
moves only because warm TTFT is squeezed by CPU contention — and the `load_ms` column proves the
squeeze is contention rather than a failed eviction: in `…105620Z` the warm half reports 853 ms of
`load_duration`, so the model genuinely was resident while TTFT was 5374 ms.

That is the honest basis for `C.WARM_START_BONUS`: routing to a warm node saves a roughly
**fixed ~8 s of model load**, which is worth most when the node is otherwise idle and worth
proportionally less when the node is saturated. A bonus expressed as a fixed latency credit is
defensible on this data; one expressed as a fixed multiplier is not.

### Sub-second TTFT

Warm TTFT is sub-second **on a lightly loaded machine only** (545 ms mean / 590 ms p95 at load
average 8; 798 ms mean / 926 ms p95 on the earlier idle run). Under
concurrent load it is not: 3.0 s and 5.0 s means on the two contended runs. The `load_ms` summary
column is printed next to the headline so this is visible rather than inferred — on a genuinely
warm path it should be near zero, and when it is not, a "warm" TTFT silently contains a model load.

## Hosted baseline

`--baseline` times an OpenAI-compatible endpoint (default: Groq's `/v1`) the same way — TTFT at
the first streamed chunk with content. **With no API key configured it skips and says so**, in
the terminal and in `manifest.json`'s `dropped` list:

```
hosted baseline
  SKIPPED: no API key configured (GROQ_API_KEY unset); hosted baseline skipped, not estimated
```

It never estimates a cloud latency. The edge-vs-cloud comparison is the experiment; fabricating
the other side of it would decide it by fiat.

No `GROQ_API_KEY` exists in this environment, so the SSE parser has still never made a real
request — but it is no longer untested. It is exercised against a recorded provider stream, and
that recording caught a live bug: real providers (Groq included) end the stream with a usage frame
carrying an **empty `choices` list**, and the original parser indexed `choices[0]`
unconditionally, so the first real run would have died with an `IndexError` *after* the timings
had already been taken. The parser now skips choice-less frames, raises on an error frame or an
unparseable frame instead of skipping it, and raises if a trial streamed no content at all rather
than recording a `None` TTFT.

## Hardware tiers

`classify_tier()` is the Tier 1/2/3 classifier `NodeRecord.tier` promised. It probes
`nvidia-smi`, then `rocm-smi`, then `platform` for Apple Silicon — **no torch dependency**,
because a node should not have to install CUDA userspace to announce that it is a CPU node.

| tier | rule |
|---|---|
| `CPU` (1) | no accelerator probe matched |
| `LOW_GPU` (2) | accelerator present with < 16 GB of GPU-addressable memory |
| `DISCRETE_GPU` (3) | ≥ 16 GB of GPU-addressable memory |

Tier 3 is defined by capacity rather than by bus, so Apple Silicon's unified memory counts —
what the market pays for is the ability to hold a large model resident. `detect_accelerator()`
returns a `detected_by` field so a tier claimed in a `NodeRecord` can be audited rather than
believed. On this machine it returns `HardwareTier.CPU`, `detected_by = "no accelerator probe
matched"`.

`hardware_profile()` returns the `NodeRecord` fields (`cpu_count`, `ram_gb`, `vram_gb`,
`tokens_per_sec`, `tier`, `models`, `warm_models`). Every one of them is read back from the
machine or the runtime, never declared:

- `tokens_per_sec` is **measured** (a warmup call, then a timed one) — a bid quoting an invented
  throughput is a bid the node cannot honour. When the measurement fails the value is `0.0` with a
  non-empty `tokens_per_sec_error` beside it.
- `models` comes from `/api/tags` and `warm_models` from `/api/ps`. An earlier version set both to
  `[the model just benchmarked]`, which advertised a model list nobody had checked: on this machine
  it claimed one model where four are pulled. Both keys are always present, with a `models_error`
  when they could not be read.

The profile also records `backend`, `host` and `served_model`, so a `NodeRecord` built from it can
be traced to the runtime that produced it.

## Failure modes

No call ever returns a plausible-looking fake result. Each failure raises its own exception,
all under `InferenceError`:

| exception | cause |
|---|---|
| `OllamaUnavailableError` | connection refused / DNS / reset |
| `ModelNotFoundError` | runtime is up, model is not pulled (HTTP 404) |
| `InferenceTimeoutError` | request exceeded `C.INFERENCE_TIMEOUT_S` |
| `OllamaProtocolError` | unparseable chunk; stream ended with no `done` chunk; a runtime that served a **different model** than was asked for; an `/api/ps` response with no readable `models` list; a token count the runtime never reported |
| `EmptyOutputError` | generation produced no tokens, so there is no TTFT to report |

Ollama can also report a failure *mid-stream* with HTTP 200; that is detected and raised too.
`GenerationStats.ttft_ms` is `Optional` and stays `None` on an empty generation — the stream is
reported faithfully — while `run()` refuses to build an `InferenceResult` from it.

Three of those are about refusing a *plausible default* rather than a hard error:

- **A counter the runtime did not send is named, not zeroed.** `eval_count == 0` cannot by itself
  distinguish an empty generation from a runtime that never reported one, and the second silently
  becomes "0 tokens at 0 tok/s" in the results table. Missing counters are listed in
  `GenerationStats.missing_counters` and in the CSV's `missing_counters` column, and `run()`
  refuses to sign a token count that was never measured.
- **An unreadable `/api/ps` is not "nothing is loaded".** Defaulting it to `[]` turns "I could not
  read the runtime's state" into the confident claim "the model is cold" — which mis-prices a bid,
  and lets `unload()` certify an eviction it never observed. Both now raise.
- **`check_warm=False` leaves warmth unknown, not `False`.** `GenerationStats.warm` is `Optional`,
  and `run()` refuses to stamp a warm flag it never read into a signed message.

## Warmth

`is_warm(model)` asks Ollama's `/api/ps` rather than remembering locally, because the process
that evicts a model (an idle `keep_alive` expiry, another client loading something else) is not
this one. A stale local flag would mean a mispriced bid. `unload(model)` evicts and then
**verifies** the eviction, raising if the model is still resident, so a trial labelled cold
cannot secretly be warm. `cold_vs_warm()` checks the *other* half too: if the second request of a
pair comes back cold, it raises rather than reporting a ratio whose warm term was never warm.

Two practical notes from running this on a contended machine:

- `keep_alive: 0` marks the model expired immediately, but `/api/ps` keeps listing it until the
  model runner process actually exits, which under load can take longer than the default 10 s
  wait. `unload()` now prints each resident model's `expires_at` when it gives up, so
  "another client is keeping it alive" (`expires_at` in the future) is distinguishable from
  "the runner has not exited yet" (`expires_at` in the past). `--unload-wait` raises the budget.
- `benchmark()` labels each trial's `phase` from the warmth `/api/ps` actually reported, not from
  the label the function would like it to have. Ollama can evict mid-run; a trial that came back
  cold is written to the CSV as `cold`, dropped from the warm summary with a recorded reason, and
  counted in `n_excluded_not_warm`, because averaging an 8-second model load into a
  sub-second-TTFT claim is how the headline number stops being true.

## Usage

```python
from edgegrid.identity import Identity
from inference.engine import InferenceEngine

ident = Identity.load_or_create("node-a")
with InferenceEngine(identity=ident, peer_id=ident.address) as eng:
    # streaming, for a gateway proxying to a client
    for token in eng.stream_tokens("Explain gravity.", max_tokens=64):
        print(token, end="", flush=True)

    # or the whole job, as the signed wire message
    result = eng.run(job_id, prompt, max_tokens=64)   # -> schemas.InferenceResult
```

`stream_tokens()` yields token text and returns a `GenerationStats` through
`StopIteration.value`; `collect()` wraps that when only the measurements are wanted. `run()`
builds the `InferenceResult`, sets `output_hash = sha256_hex(output)`, and signs it with the
node's `Identity` so `edgegrid.identity.verify_message(result, address)` holds.

## Tests

```bash
python -m pytest tests/test_inference.py            # 57 tests, HTTP mocked
python -m pytest tests/test_inference.py -m live    # the one test needing a live Ollama
```

The mock transport emits NDJSON chunks with real pauses between them, so the TTFT assertions
are about ordering and timing rather than about a stubbed number: a leading empty-`response`
chunk must not be mistaken for the first token, and tokens must reach the caller before the
stream closes.

`benchmark()`, `cold_vs_warm()` and `_trial()` are covered against a **stateful** fake runtime
(`FakeOllama`) that loads and evicts models the way Ollama does, so the cold/warm and phase-label
logic is exercised rather than stubbed. All three functions previously had no coverage at all,
which is why a hardcoded `phase = "warm"` and a pinned `tokens_per_sec` could both sit in the code
with a green suite.

Every guarantee above is checked by reverting it and confirming a test goes red — the two original
tests named for connection-refused and timeout raised from the `/api/ps` warmth check, the first
request the engine makes, so they never reached `stream_tokens`'s own error handling; deleting that
handling entirely left the whole suite green. The replacements answer `/api/ps` normally and fail
on `/api/generate`.
