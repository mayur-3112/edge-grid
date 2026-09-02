# Gateway — OpenAI-compatible front door + operator console

The gateway is the only component a developer of an application on the Edge Grid
has to know about. It speaks the OpenAI chat-completions wire format, so migrating
is a base-URL change:

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:8000/v1", api_key="unused")
client.chat.completions.create(model="qwen3-vl:2b-instruct",
                               messages=[{"role": "user", "content": "hello"}])
```

Behind that endpoint every request runs the real pipeline:

```
signed JobRequest ──> auction (edgegrid.market) ──> streaming inference (ollama)
        ──> DA commitment (merkle proof) ──> sampled verification (LLM judge)
        ──> settlement (stake / slash / escrow)
```

## Run it

```bash
.venv/bin/python -m uvicorn gateway.app:app --port 8000
```

Then open <http://localhost:8000/> for the operator console, or
<http://localhost:8000/docs> for the generated OpenAPI page.

## Modes: `p2p` vs `local`

Every response carries `x-edgegrid-mode`, and the JSON body carries the same in
its `edgegrid.mode` field. There are exactly two values and the gateway never
guesses:

| mode | when | what it means |
|---|---|---|
| `p2p` | a transport module (`edgegrid.p2p`, `edgegrid.swarm`, …) exposes `open_grid(bus)` **and it connects** | bids arrived over a real libp2p swarm |
| `local` | anything else | `LocalGrid` — the same pipeline, one process |

`GET /health` reports `mode_reason` with the verbatim outcome of **every** transport
attempt, so nobody has to take the label on trust — and so a module that exists and
crashes on import is named as a crashed transport rather than being masked by a
later one that merely has no `open_grid`:

```
"mode_reason": "no p2p transport opened a grid; serving from the in-process
 LocalGrid, which clears bids through the same edgegrid.market auction but over one
 process rather than a swarm. attempts: edgegrid.p2p: not importable
 (ModuleNotFoundError: No module named 'edgegrid.p2p') | edgegrid.swarm: not
 importable (ModuleNotFoundError: No module named 'edgegrid.swarm')"
```

Every response carries `x-edgegrid-mode` and `x-edgegrid-version`, including 404s,
422s and unhandled errors — the responses an operator is most likely to be reading.

### What `LocalGrid` really is

Five nodes with distinct hardware profiles, prices and stakes. In local mode the
following are **real, not simulated**:

- **Identity.** Each node has a real secp256k1 key and the real libp2p PeerID that
  key would produce on a live host (`16Uiu2HAk…`). Keys are derived deterministically
  from a label so peer ids and stake balances survive a restart. They are development
  keys and are not written to disk; a real node uses `Identity.load_or_create`.
- **Signatures, bound to the registry.** Every `JobRequest`, `Bid`, `JobAward`,
  `InferenceResult`, `Commitment` and `Verdict` is signed and verified. A bid whose
  signature does not recover to its wallet is rejected by the auction with reason
  `bad_signature` — but that check alone proves only who owns the wallet *named in
  the bid*, which the bidder writes itself, and nothing about which peer bid. The
  requester holds the node registry, so `LocalGrid.admission_reason` binds the
  claimed `bidder_peer_id` to the wallet registered for it (and checks the stake
  floor and the claimed stake) before the market ever ranks the bid. Every refusal
  is reported on the job record, never silently dropped.
- **The auction.** The gateway does **not** implement a clearing rule. It gathers
  signed bids and calls `edgegrid.market.evaluate`, which owns eligibility, ranking
  and the threshold (Vickrey) clearing price. One definition, one module.
- **Inference.** A real streaming call to Ollama. TTFT is measured at the first
  token off the socket; token counts come from the runtime's `eval_count`, never
  from `len(output.split())`. If no token ever arrives there is no TTFT, and the job
  fails with that reason rather than reporting total wall-clock in its place. If the
  runtime did not usefully time the generation — ollama reports `eval_duration` of
  1000 ns for a one-token response, which divides out to 10^6 tokens/sec —
  `tokens_per_sec` is recorded as 0 with a note and excluded from the aggregate mean,
  never divided out of a clock artefact.
- **The DA commitment.** A real blob in `edgegrid.da` with a real Merkle inclusion
  proof. Two blobs go into each block — a provenance record and the raw output —
  because a block with a single leaf has an empty proof that proves nothing. The
  verifier re-fetches the blob, recomputes the hash and checks the proof before it
  calls a judge. The three outcomes are kept apart (`LocalGrid.check_da`): a store
  that cannot return the blob or a proof is an **outage** — verdict `error`, escrow
  held, stake untouched — while bytes that do not match the commitment, or a proof
  that misses the block root, are **evidence** — verdict `fail`, provider slashed.
  Collapsing the two into one boolean is how a storage failure slashes an honest
  node.
- **Verification.** A real LLM judge call to the configured backend. If it is
  unreachable, misconfigured or unparseable the verdict is `error` — never a pass,
  never a fail, never a silent mock.
- **Settlement.** Real stake balances, an 80/20 validator/treasury slash split, and
  value conservation. An operator audit reverses the prior settlement's value
  movement *and* marks its ledger row `reversed`, so summing the ledger cannot
  double-count a payout that no longer exists.

And one thing is **modelled**, labelled on every single job record:

> There is one machine, so there is one inference runtime. All five nodes bid, but
> whichever node wins, the tokens are physically produced by this host's Ollama.
> Each record carries `execution.attributed_to_winner` and `execution.executed_by_peer_id`,
> and the event stream emits a `log` warning whenever the winner is not the host
> node. The TTFT and token counts are true measurements of *this machine*,
> attributed to the winning node. No number here is a network measurement.

Host telemetry is measured, never modelled: if psutil cannot be read, the host's
`cpu_count` / `ram_gb` are the schema's unset `0` with `host_hardware_error` set and
`hardware_measured: false` on the node view, and `cpu_percent` / `ram_available_gb`
are `null` rather than `0.0` — a dashboard cannot tell "idle" from "we could not
look" if both render as zero.

## API

### OpenAI-compatible

| route | notes |
|---|---|
| `POST /v1/chat/completions` | streaming and non-streaming. Extra OpenAI fields are accepted and ignored. |
| `GET /v1/models` | models any node serves, plus `edgegrid_warm_providers`. |

Edge Grid extensions to the request body — an OpenAI client never sends them, and
the gateway honours them when present:

| field | default | meaning |
|---|---|---|
| `max_price` | `1.0` | price ceiling in GRID. Bids above it are rejected with `price_over_max`. |
| `max_latency_ms` | `30000` | TTFT budget a bid must promise to meet. |
| `verify` | `false` | force verification of this job rather than leaving it to sampling. |

`model` may be `default`, `auto` or `edgegrid`, which resolve to `config.OLLAMA_MODEL`.
The resolution is reported in `edgegrid.notes` and in the response's `model` field —
it is never a silent substitution. Any other unknown model is a `404` that lists what
*is* available.

Non-streaming responses carry an extra top-level `edgegrid` block (OpenAI clients
ignore unknown keys) with the provider, bid count, clearing price, TTFT, DA root,
verdict, settlement state and any degradation notes. In streaming mode the same
block rides on the final chunk, next to `usage`, before `data: [DONE]`.

A failure *after* the response headers are on the wire cannot become a 4xx, so a
mid-stream failure is emitted as `data: {"error": {...}}` followed by `[DONE]`
rather than being swallowed to keep the stream looking clean.

### Operator

| route | returns |
|---|---|
| `GET /health` | mode, mode reason, runtime reachability, judge config, stages |
| `GET /api/nodes` | node roster with live telemetry and signature validity |
| `GET /api/jobs?limit=` | recent job records, newest first |
| `GET /api/jobs/{id}` | one full job record — bids, award, result, commitment, verdict, settlement, notes |
| `POST /api/jobs/{id}/verify` | operator audit: force verification and re-settle |
| `GET /api/stats` | aggregate counters, TTFT distribution, verdict split, ledger totals |
| `GET /api/settlements` | ledger records plus per-node stake, earnings, and `totals` over rows an audit has not reversed |
| `GET /api/events` | SSE stream of pipeline events (the dashboard's data feed) |
| `GET /api/config` | the full config snapshot, minus the Groq key |

`POST /api/jobs/{id}/verify` reverses the previous settlement's value movement
before applying the new one, so an audit can never double-count a payout or a slash.
It also marks the superseded ledger row `reversed`: the row stays for the audit
trail but stops counting as value that moved, and `totals` on `/api/settlements`
excludes it. Without that, summing the ledger counts a reversed payout twice.

## Events

`GET /api/events` opens an SSE stream. On connect it sends a named `hello` event
with a full snapshot, then replays the last N events (`?replay=`, default 60), then
streams live. Event types: `job.created`, `job.stage`, `bid`, `award`,
`inference.start`, `inference.done`, `commit`, `verdict`, `settlement`, `node`, `log`.

Every event carries a monotonic `seq`, so a client can tell it missed something
rather than silently rendering a gap. A subscriber that stops reading is dropped
from the tail — never blocked, because a stalled browser must not be able to stall
the inference pipeline — and is told so with a `log` event.

## Dashboard

`gateway/static/` — vanilla JS, no npm, no build step, no CDN, no webfonts. It
works with no network beyond the gateway itself, and renders truthful empty states
when the gateway is down rather than a blank page.

Panels: live node table, job feed with a five-stage tracker per job, an auction view
showing the bid ladder with the second-price clearing marked (and rejected bids with
their reason), the pass/fail/**error** verification split, the settlement ledger with
stake movement, and a raw event tail. The console panel posts to the real
`/v1/chat/completions` streaming endpoint, so what you watch in the dashboard is the
same code path a customer hits.

Style: charcoal surfaces, exactly two accents (pink `#e4587d`, mint `#66d1b5`),
square corners, hard-offset shadows only, system monospace, no emoji. A test asserts
the palette, the absence of blurred shadows, the absence of emoji, and that no asset
references an external origin.

## Tests

```bash
.venv/bin/python -m pytest tests/test_gateway.py -q            # contract tests
.venv/bin/python -m pytest tests/test_gateway.py -q -m live    # + real ollama
```

Contract tests stub **only** the two calls that leave the process (the Ollama stream
and the judge). The auction, signing, DA layer, sampling and settlement under test
are the real implementations. The `live` test runs the whole thing against the real
Ollama, because a passing stub test proves nothing about whether tokens are actually
produced.

## SDK

`sdk/edgegrid_sdk.py` — `EdgeGrid(base_url).complete(...)` / `.stream(...)` plus
`.nodes()`, `.jobs()`, `.stats()`, `.audit(job_id)`. It returns the pipeline evidence
alongside the text (provider, clearing price, TTFT, verdict, settlement state, and
any degradation notes). Demo:

```bash
.venv/bin/python -m sdk.edgegrid_sdk
```
