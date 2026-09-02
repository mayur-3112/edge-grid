# CHAPTER 7

# PROPOSED SYSTEM

## 7.1 System Architecture Overview

The Edge Grid is proposed as a decentralised physical infrastructure network (DePIN) that
procures large-language-model inference from independently owned consumer machines, verifies
the outputs those machines produce, and settles payment for the work without any central
coordinator. The system is organised as five interdependent logical modules. Each module is a
separately runnable subsystem with its own entry point, its own tests and its own recorded
results, and each is joined to its neighbours by a small number of strictly typed messages
rather than by shared mutable state. The overall organisation of the five modules, the client
layer above them and the blockchain layer beneath them is shown in
the architecture figure.

**Figure 7.1** — `docs/figures/architecture.png` — System architecture. Solid arrows carry data; dashed arrows carry value or proofs.


The five modules are the Decentralized Discovery Module, which answers the question *which
machines exist and what can they do*; the Hybrid Market Protocol Module, which answers *which
of them should run this particular job and at what price*; the Edge Inference Engine Module,
which actually generates the tokens and measures how long it took; the Agentic Verification
Module, which decides whether the answer that was produced deserves to be paid for; and the
Blockchain Settlement Module, which moves the money and, where necessary, confiscates the
stake of a provider that has been proven dishonest.

Two architectural decisions bind these five modules into one system, and both of them are
worth stating before the modules are described individually.

The first is that **every message on every hop is a declared, validated, signed type**. The
module `edgegrid/schemas.py` is the single source of truth for the nine message kinds that
cross module boundaries: `NodeRecord`, `Heartbeat`, `JobRequest`, `Bid`, `JobAward`,
`InferenceResult`, `Commitment`, `Verdict` and `SettlementRecord`. These are implemented as
pydantic models configured with `extra="forbid"`, so a subsystem that emits an unexpected
field is rejected at the boundary that receives it instead of causing silent semantic drift
weeks later. Each of these types carries a `canonical()` method which serialises the message
with sorted keys, no whitespace and the `signature` field excluded, giving a byte string that
is stable across processes and across machines and can therefore be signed and re-verified by
anybody. The developer-facing schema reference in `shared/schemas.md` is generated
mechanically from this module and is never edited by hand, which removes the usual failure
mode in which documentation and wire format diverge.

The second is that **authority in the system always derives from a key, never from a network
position**. There is no privileged bootstrap node, no trusted scheduler and no administrator
account that can move value. A node's ability to advertise itself, to bid, to be awarded work,
to be paid, and to be slashed all trace back to possession of one secp256k1 private key. This
is what allows the network to be permissionless: joining the grid requires generating a key
and posting stake, not obtaining permission.

The client layer sits above Module 1 and Module 2 and consists of three surfaces: a Python SDK
(`sdk/edgegrid_sdk.py`), a FastAPI REST gateway exposing an OpenAI-compatible
`/v1/chat/completions` endpoint (`gateway/app.py`), and an operator dashboard served by the
same gateway process and fed by a sequence-numbered server-sent-event bus
(`gateway/events.py`). The significance of the OpenAI-compatible surface is that the migration
story claimed for the system — that an existing application adopts the grid by changing a base
URL — is only meaningful if the endpoint genuinely implements that contract, including
streaming chunk objects, usage accounting and the terminating `data: [DONE]` sentinel, which
it does.

The remainder of this chapter describes each module in turn, then traces one job through all
of them, and finally sets out in Section 7.8 exactly where the implemented system differs from
the design that was proposed in the synopsis.

---

## 7.2 Module 1 — Decentralized Discovery

The purpose of Module 1 is to allow a node that has just been switched on in an arbitrary
household to become discoverable, and to allow a requester that has never met that node to
learn what it can do and whether it is currently alive, without either party consulting a
central directory. The module is implemented in `discovery/node.py` and
`discovery/heartbeat.py`, resting on the identity primitives in `edgegrid/identity.py` and the
hardware classifier in `inference/benchmark.py`.

### 7.2.1 Cryptographic Identity

Every node generates a single ECDSA keypair on the secp256k1 curve, implemented over the
`eth-keys` library in `edgegrid/identity.py`. From this one keypair the node derives three
distinct capabilities that in a conventional system would require three separate credentials:

1. **A stable network identity.** The 32 private key bytes are used to seed the libp2p host
 keypair, so the node's libp2p `PeerID` is a deterministic function of its key. A node that
 restarts comes back with the same PeerID and therefore the same DHT key.
2. **A settlement identity.** The public key yields a checksummed Ethereum address by the
 standard derivation. This address is the account that stakes collateral in `NodeRegistry`,
 that is named as the provider in an escrow, and that is slashed if fraud is proven against
 it.
3. **Message authentication.** Every message the node puts on the wire is signed over its
 canonical bytes, and the recipient recovers the signer's address from the signature and
 compares it with the wallet address the message itself claims.

The consequence of unifying these three is that reputation, stake and network presence cannot
be separated. A node cannot discard a poor history by reappearing under a new PeerID while
retaining its stake, because the stake is held against the address that the PeerID is derived
from. Keys are persisted with mode `0600` under `~/.edgegrid/<name>.key`, so identity — and
therefore accumulated stake — survives a restart.

The verification helpers are deliberately written to fail closed. `verify()` returns `False`
for a malformed signature rather than raising, so a peer cannot crash a node by sending
malformed bytes; but it returns `False` for an absent signature as well, so an unsigned
message is never treated as an unauthenticated-but-acceptable message.

### 7.2.2 The Kademlia DHT and the Signed NodeRecord

Static node metadata is published into a Kademlia distributed hash table provided by
py-libp2p 0.7.0. Each node runs its DHT in `SERVER` mode and stores its signed `NodeRecord`
at the key `/edgegrid/node/<peer_id>`. The record carries the node's PeerID, its wallet
address, its public key, its dialable multiaddresses (both the libp2p TCP address and the UDP
heartbeat endpoint), its hardware tier, the list of models it is willing to serve, its logical
CPU count, its installed RAM, its VRAM, its measured token throughput and its posted stake.
Kademlia gives `O(log n)` lookup, which is the property that makes the registry scale without
a directory server.

A record is not accepted merely because it arrives. The module registers a namespace validator
(`NodeRecordValidator`) with the DHT, and py-libp2p applies it on both `put_value` and every
record returned by `get_value`. The validator rejects a record whose key does not lie in the
`/edgegrid/node/` namespace, a record that does not parse as a `NodeRecord`, a record whose
`peer_id` field does not match the key it is stored under, and — most importantly — a record
whose signature does not verify against the wallet address it claims. A forged capability
advertisement therefore cannot enter the value store at all; it is rejected at the DHT
boundary rather than by whichever consumer happens to read it later. Where two records for the
same key are in circulation, the validator's `select()` method resolves the conflict in favour
of the newest `updated_ms`, which gives a node a way to correct its own advertisement without
introducing a mechanism by which one node can overwrite another's.

A subtle but load-bearing implementation detail is that a transport connection to a peer does
not by itself place that peer in the DHT routing table. The node therefore runs a maintenance
loop that explicitly adds every connected peer to the routing table, republishes its own
record, and resolves the records of peers it has not yet seen. Without this loop `put_value`
has no peer to replicate to and `get_value` has no peer to ask, and the DHT silently degenerates
into a local dictionary that appears to work in a single-process test. To make this
distinguishable in the recorded evidence, `lookup_record()` reports whether a value was already
replicated locally or had to be fetched from the network, so an experiment can demonstrate
genuine distributed resolution rather than a node reading back what it wrote itself.

### 7.2.3 The UDP Heartbeat

Dynamic node state is carried on a completely separate mechanism: signed UDP datagrams
broadcast every `HEARTBEAT_INTERVAL_S` seconds, configured at 5.0 seconds. A `Heartbeat`
message carries a monotonically increasing sequence number, the node's currently available RAM
and VRAM, its instantaneous CPU utilisation, its one-minute load average, the set of models
currently resident in memory, the number of in-flight jobs and a health flag. These values are
read from `psutil` at the moment of transmission; when `psutil` cannot supply a figure the
heartbeat reports zero together with `healthy=False` rather than fabricating a plausible
number.

Heartbeat endpoints are learned rather than configured. A node advertises `/ip4/<ip>/udp/<port>`
inside `NodeRecord.multiaddrs`; the discovery layer parses that endpoint out of the DHT record
and hands it to the heartbeat service. The DHT thus bootstraps the heartbeat mesh, after which
the heartbeat mesh tracks liveness independently.

Each datagram is signed with the same secp256k1 key. On receipt, the signer's address is
recovered and compared against the wallet address in that peer's DHT record. A datagram whose
signer contradicts the known record is dropped and counted as `bad_signature`. A datagram from
a peer for which no record has yet been resolved is accepted but flagged `verified=False` and
counted separately, so a consumer can distinguish "live and cryptographically attributed" from
"live, source not yet confirmed" — an honest distinction that a boolean liveness flag would
destroy. Replayed or reordered datagrams are detected by sequence number and discarded without
overwriting fresher state. A peer is considered alive if a healthy heartbeat was received
within a TTL that defaults to three intervals, that is fifteen seconds.

### 7.2.4 Why Static and Dynamic State Are Split Across Two Mechanisms

The separation of the DHT record from the heartbeat is a deliberate design decision and not an
implementation accident, and the reasoning behind it is central to Module 1.

A Kademlia record is *replicated, slow and comparatively expensive*. Writing a value causes it
to be stored at several peers close to the key in the identifier space; reading it may require
several iterative lookups. That cost is entirely appropriate for facts that change on the
timescale of days: a node's wallet address, its hardware tier, its core count, the set of
models it is prepared to serve. It is entirely inappropriate for facts that change every few
seconds. Pushing live utilisation through Kademlia would mean republishing every node's record
continuously, which would consume the network's bandwidth in maintenance traffic; and the
reader would still receive a value that was true at some point in the recent past, because
replication is not instantaneous. In other words the DHT would be paying a high price for a
guarantee it cannot deliver.

Liveness has the opposite profile. It is cheap to produce, it is only interesting to the small
set of peers that might actually award this node a job, and it is worthless the moment it goes
stale. A plain signed datagram at a fixed interval is the correct instrument: it costs one UDP
packet per peer per interval, it carries no replication obligation, and its staleness is
directly observable from the timestamp.

The split also has a market consequence, which is the reason it matters beyond networking
hygiene. The single field that most affects the outcome of an auction is the set of models a
node currently has *resident in memory*, because that determines whether the job incurs a cold
start. That field is exactly the fastest-moving one in the system — a model can be evicted at
any time by an idle timeout or by another client loading something else. Module 1 therefore
sources warm-model state exclusively from the heartbeat and never from the DHT. Had it been
read from the DHT, the market would be pricing a fact that was minutes out of date, and the
warm-start bonus described in Section 7.3 would systematically misprice bids.

### 7.2.5 Hardware Tier Classification

At registration a node benchmarks itself and classifies its hardware into one of three tiers,
implemented as `HardwareTier` in `edgegrid/schemas.py` and computed by `classify_tier()` in
`inference/benchmark.py`:

| Tier | Name | Definition |
|---|---|---|
| Tier 1 | `CPU` | No accelerator detected by any probe |
| Tier 2 | `LOW_GPU` | An accelerator is present with less than 16 GB of addressable memory |
| Tier 3 | `DISCRETE_GPU` | An accelerator with at least 16 GB of GPU-addressable memory |

Detection is performed by `detect_accelerator()`, which probes `nvidia-smi`, then `rocm-smi`,
then Apple Silicon unified memory, and reports which probe succeeded in a `detected_by` field
carried alongside the tier. The tier claimed in a `NodeRecord` is therefore auditable rather
than merely asserted. Tier 3 is defined by *capacity* rather than by bus type, because the
economically relevant property is whether the node can hold a large model resident; on this
reasoning Apple Silicon unified memory legitimately counts towards the Tier 3 threshold even
though it is not discrete VRAM.

The development machine used throughout this work classifies as Tier 1: sixteen logical cores,
ten physical cores, 30.94 GB of RAM, no accelerator, `detected_by = "no accelerator probe
matched"`. This is recorded verbatim in every benchmark run's `hardware_profile.json`. The
tier is consumed by the market as a hard eligibility constraint: a `JobRequest` may specify
`min_tier`, and a bid from a node below that tier is not a cheap bid but an ineligible one.

---

## 7.3 Module 2 — Hybrid Market Protocol

Module 2 replaces the load balancer of a centralised inference service with a market. A
requester does not select a provider; it publishes a specification of the work and a ceiling
on what it will pay, and the providers that can meet the specification compete to supply it.
The auction logic is implemented as a pure, deterministic module in `edgegrid/market.py` — no
sockets, no clock, no configuration mutation — and the networking that feeds it lives in
`discovery/node.py`.

### 7.3.1 The GossipSub Task Mempool

Four GossipSub topics constitute the task mempool:

- `edgegrid/jobs/v1` — signed `JobRequest` messages,
- `edgegrid/bids/v1` — signed `Bid` messages,
- `edgegrid/awards/v1` — signed `JobAward` messages,
- `edgegrid/commitments/v1` — `Commitment` messages.

Publication is genuine GossipSub over py-libp2p, not a broadcast simulation. Three
implementation properties are necessary for this to actually work and are handled explicitly
in `EdgeGridNode.serve()`. First, `GossipSub.run()` is an independent anyio service which
`Pubsub` does not start; running only `Pubsub` produces a mesh that never grafts and publishes
that are silently discarded, so both services are started. Second, subscription happens before
peers are dialled, so a remote learns of our interest in a topic during the first exchange
rather than one heartbeat later. Third, `_wait_for_mesh()` blocks until the mesh for the jobs
topic actually contains a peer, because publishing into an empty mesh is a silent no-op — a
failure mode that would otherwise present itself as an auction that mysteriously received no
bids.

Pubsub-level strict signing is disabled deliberately, because authenticity in this system is
carried by the application's own secp256k1 signature inside each payload, checked against the
wallet address that settlement will actually pay. Every inbound message is verified on that
basis, and every rejection is counted in a per-node statistics dictionary and emitted as a
structured `dropped` event rather than discarded quietly.

### 7.3.2 Sealed Bids and Hard Constraints

A `JobRequest` carries the prompt, the model identifier, a token budget, the requester's PeerID
and wallet, a price ceiling `max_price`, a latency budget `max_latency_ms` and a minimum tier.
A provider that receives it and serves the requested model responds with a `Bid` carrying its
price, its estimated time-to-first-token, whether the model is currently warm on that node, its
tier and its posted stake. Bids are collected for a fixed window, `BID_WINDOW_S`, configured at
2.0 seconds, after which the window is closed; bids arriving afterwards are counted as late
rather than silently accepted.

The eligibility rules are enforced in `exclusion_reason()` and are treated as *hard
constraints*, not as soft penalties. A bid is excluded, with a recorded reason, if it names a
different job, if its signature does not verify against the wallet it claims, if its price is
non-positive or non-finite, if its tier is below the job's minimum, if its estimated TTFT
exceeds the latency budget, or if its price exceeds the ceiling. This distinction matters. An
earlier and naive formulation of the rule ranked all bids by price and selected the minimum,
which meant a bid that could not meet the latency budget was treated merely as a very cheap
bid. Under the present rule such a bid scores infinity and cannot win at any price. The latency
budget is the requester's statement of what the answer is worth; a provider that cannot meet it
is not offering a discount, it is offering a different product.

Duplicate bids from the same peer collapse to that peer's last submission. This permits a
provider to revise its bid inside the window while preventing it from occupying two positions
in the ranking and thereby manufacturing its own runner-up — which, under a second-price rule,
would be a direct attack on the clearing price.

### 7.3.3 The Second-Price Rule and Why Truthful Bidding Is Dominant

It is important to be precise about the direction of this auction. This is a *procurement* or
reverse auction: providers bid the price at which they are willing to be paid, and the
requester wishes to buy cheaply. The winner is therefore the *lowest* eligible bid, and under
the Vickrey rule the winner is paid the *second-lowest* price, which is by construction at or
above its own bid.

The strategic consequence is the standard one, and it is the reason the rule was chosen. A
provider's bid determines only *whether* it wins; it does not determine *what* it is paid, since
the payment is fixed by the runner-up. Shading a bid upwards, above one's true reserve, can
only cause one to lose auctions one would have been happy to win, and never increases the
payment received in an auction one still wins. Shading downwards, below one's true reserve, can
only cause one to win auctions at a price below one's own cost. Bidding one's true reserve
therefore weakly dominates every alternative strategy, for every provider, regardless of what
the others do. The practical importance of this in a permissionless network is that a provider
does not need to model its competitors, and a requester does not need to defend against
strategic manipulation of the price: truthfulness is not enforced, it is simply the best
available strategy.

Two boundary cases required an explicit decision. Where exactly one bid is eligible there is no
runner-up, and the clearing price is set to the requester's own declared ceiling,
`job.max_price`. This is the standard reserve-price convention: the requester has already
declared that the ceiling is acceptable, so paying it is truthful, and a monopolist provider
cannot extract more than the ceiling by bidding very low. The alternative — paying the winner
its own bid — is a first-price rule, and it destroys truthful bidding in precisely the
single-bidder case where the temptation to shade is largest. Where no bid is eligible, the
auction returns no award at all; it never falls back to "the cheapest ineligible bid".

The ordering of ranked bids is made a total order — effective price, then estimated TTFT, then
PeerID as a stable final arbiter — and the sort key is quantised to twelve decimal places so
that floating-point representation noise cannot break a genuine tie. The auction is therefore
deterministic given the same set of bids, and a recorded run can be replayed from its CSV and
produce the same award.

### 7.3.4 The Warm-Start Bonus and Its Empirical Justification

A node that already has the requested model resident in memory can begin generating almost
immediately; a node that must load the weights first cannot. To the requester these are simply
not the same service, and the market must be able to express that. Module 2 does so through a
warm-start bonus, `WARM_START_BONUS`, configured at 0.15, applied as a fifteen percent
handicap on a warm bid's *score*.

The empirical justification for paying for warmth is direct and was measured on the project's
own hardware. In the recorded benchmark run
`docs/results/inference-benchmark-20260902T101341Z`, three matched cold/warm pairs on the model
`qwen3-vl:2b-instruct` produced a mean cold TTFT of 8,912.84 ms against a mean warm TTFT of
677.68 ms — a ratio of 13.15, and a mean penalty of 8,235.16 ms attributable almost entirely to
model loading (mean cold load 8,363.96 ms against 584.56 ms warm). A second run recorded a
single cold observation of 10,576.41 ms against a warm 711.80 ms. In other words, on this
hardware a cold start costs roughly thirteen times the latency of a warm one, and the entire
difference is loading rather than generation. A requester whose latency budget is measured in
hundreds of milliseconds is not choosing between a cheap node and an expensive one; it is
choosing between a node that can serve it at all and one that cannot. A fifteen percent price
handicap is a modest premium against a thirteen-fold latency difference, and it gives providers
a standing incentive to keep in-demand models resident — which is precisely the behaviour the
network wants to elicit, since a network of warm nodes is what makes sub-second time-to-first-token
achievable at all.

The bonus is applied to the *score* and never to the money. This is essential, and the reason
is instructive. If the fifteen percent were applied to the payment, a warm winner could be paid
less than its own reserve price, the auction would cease to be individually rational, and no
provider would bid truthfully again. Instead the clearing price is computed as the winner's
*threshold price* — the highest it could have bid and still have won:

```
clearing = min( runner_up_effective / winner_discount_factor , job.max_price )
```

When the winner is not warm the discount factor is 1.0 and this reduces exactly to the plain
Vickrey rule, "pay the runner-up's price". When the winner is warm, the clearing price sits
above the runner-up's sticker price by exactly the handicap: the requester pays more in GRID
for a node whose service it valued fifteen percent more highly, while in effective terms it
still pays the runner-up's score. This is the standard result for a scoring auction, and it is
what preserves the dominance of truthful bidding for warm and cold nodes alike. The invariant
`winning_bid_price <= clearing_price <= max_price` holds on every path and is asserted in the
market test-suite.

---

## 7.4 Module 3 — Edge Inference Engine

Module 3 is the subsystem that actually produces tokens, and it is implemented in
`inference/engine.py` as a streaming client for a local Ollama runtime, with the hardware
benchmark and tier classifier in `inference/benchmark.py` and the content-addressed weight
distribution path of Section 7.4.6 in `edgegrid/weights.py`.

### 7.4.1 Runtime Selection, and Why vLLM and CUDA Are Out of Scope

The Phase-1 design proposed two backend paths: vLLM with PagedAttention on CUDA-capable NVIDIA
hardware, and Ollama on Apple Silicon and CPU-only nodes. The hardware available for this
project has no NVIDIA GPU. `detect_accelerator()` on the development machine returns
`{"kind": "none", "detected_by": "no accelerator probe matched", "vram_gb": 0.0}`, and the
machine classifies as Tier 1. vLLM's central contribution — PagedAttention over GPU KV-cache
memory — has no meaning on a machine with no GPU memory to page, and a vLLM path implemented
but never executed would be an untested claim in a report that is otherwise built on measured
evidence.

The project therefore declares the vLLM/CUDA path explicitly out of scope for Phase 1 and
implements the Ollama path only. This is recorded in the scope table of Section 7.8 rather than
left for a reader to discover. The architectural provision for the second backend is retained:
the inference engine is reached through a single `run()` entry point returning a signed
`InferenceResult`, so a second runtime is an alternative implementation of that method and
nothing above it changes.

### 7.4.2 How Time-to-First-Token Is Actually Measured

The headline quantitative claim of this project is a latency claim, and it is specifically a
claim about the time to the *first* token, not about the total time to complete a response.
This distinction determines how the engine must be written.

An earlier implementation requested `stream: false` from Ollama. Under that setting the HTTP
response does not exist until generation has finished, which makes time-to-first-token not
merely unmeasured but *structurally unmeasurable*: there is no event in the client's timeline
corresponding to the arrival of the first token. The present engine consumes Ollama's NDJSON
stream chunk by chunk. A monotonic clock, `time.perf_counter()`, is started immediately before
the HTTP request is issued, and `ttft_ms` is stamped at the arrival of **the first chunk whose
`response` field is non-empty**.

The qualification is not pedantry. Ollama terminates every stream with a final chunk that
carries `done: true` and an *empty* `response` string, and it may emit other chunks that carry
no text. Stamping TTFT at the first chunk of any kind would silently under-report latency, and
on a generation that produced no tokens at all it would report a TTFT for a response that never
existed. In the implementation, `stats.ttft_ms` remains `None` until non-empty text arrives, and
a generation that ends with `ttft_ms is None` raises `EmptyOutputError` rather than returning a
result. Reporting such a case as `0.0`, or as the total elapsed time, would be a fabricated
measurement injected directly into the project's headline metric.

The warmth check is performed *before* the clock is started, for the same reason: a round trip
to `/api/ps` must not be allowed to land inside the measured interval.

### 7.4.3 Real Token Counts

Token counts are read from the runtime's own counters on the final chunk: `eval_count` for
generated tokens and `prompt_eval_count` for prompt tokens, together with `eval_duration`,
`prompt_eval_duration`, `load_duration` and `total_duration`, all reported by Ollama in
nanoseconds. These come from the model's own tokenizer.

The alternative that is frequently seen — `len(output.split())` — is a word count, not a token
count, and is wrong by roughly thirty to forty percent for English prose and considerably more
for code or for text containing numbers and punctuation. Since token counts feed the cost
comparison against centralised providers, which price per thousand tokens, using a word count
would propagate a systematic error straight into the economic argument.

Throughput is computed as `eval_count / eval_duration`, not as `eval_count / total_ms`. Total
duration includes model loading and prompt evaluation; charging those to generation would make
a cold run appear slow at generating when in fact it was only slow at starting. The two
quantities answer different questions and the engine reports them separately.

### 7.4.4 Warmth Is Observed, Not Remembered

Whether a model is resident is read from Ollama's `/api/ps` endpoint at the moment a request is
issued, rather than tracked in a local boolean. The reason is that the process which evicts a
model — an idle `keep_alive` expiry, or another client loading a different model — is not this
process. A locally remembered flag would drift out of agreement with reality, and because the
market prices warmth through the warm-start bonus, a wrong warm flag is a mispriced bid and, in
the limit, a bid the node cannot honour. For the same reason the benchmark's `unload()` helper
verifies that the model has actually left memory before a "cold" measurement is taken, and
raises if it has not, rather than allowing a measurement labelled cold to have been quietly
warm.

### 7.4.5 Failure Is Named, Never Simulated

The engine defines a small exception hierarchy — `OllamaUnavailableError`,
`ModelNotFoundError`, `InferenceTimeoutError`, `OllamaProtocolError` and `EmptyOutputError`,
all deriving from `InferenceError` — and maps each distinguishable failure onto exactly one of
them. Ollama is capable of reporting a failure mid-stream while still returning HTTP 200, so
the engine inspects every chunk for an `error` key as well as checking the status code. A
stream that ends without a final `done` chunk raises `OllamaProtocolError`, because the token
counts for such a generation are unknown and reporting them as zero would be a fabrication.

The invariant the caller may rely on is that **an `InferenceResult` is never returned unless a
model actually produced it**. There is no placeholder result, no default output and no
silently substituted model. A `InferenceResult` carries the output text, the model identifier,
the real token count, the measured TTFT, the total duration, the throughput, the warm flag and
the SHA-256 of the output, and it is signed by the provider's identity before it leaves the
node.

### 7.4.6 Content-Addressed Weight Distribution

Objective 3 asks for an edge client that benchmarks its hardware, streams tokens, *and manages
model weights*. The first two clauses are the subject of the preceding subsections. The third is
the subject of this one, and it is implemented in `edgegrid/weights.py` against a real kubo IPFS
daemon brought up by `deploy/ipfs/docker-compose.yml`. The design is described here rather than
in Chapter 8 because what matters architecturally is the order in which the checks are performed
and what each of them establishes; how long a fetch took is reported in Chapter 8, in Tables 8.8
and 8.9.

The path a model identifier travels is short and has exactly three stages: **identifier to
content identifier, content identifier to local bytes, local bytes to a verified path**. The
first stage is `WeightResolver.lookup()`. It reads `ModelRegistry` (Section 7.6.1), keyed by the
keccak-256 hash of the model name, and takes the CID from the `ipfs://` URI the registry records
alongside the content hash. If no chain deployment is reachable, or the identifier is not
registered, or the registration has been revoked, it falls back to a local manifest — but the
fallback is never silent. The returned `ResolvedWeights` carries a `source` field naming which
authority was used and a `chain_note` field stating why the chain was not, and both are written
into the experiment's result rows, so a run can never appear to have consulted the chain when it
did not.

The second stage is `LocalWeightCache`, the bounded cache the module specifies. It is an LRU
cache governed by a byte budget rather than an entry count, because model weights differ in size
by three orders of magnitude and a count-based bound would say nothing useful about disk
occupancy. A hit is served entirely from local disk and does not touch the network at all. An
insertion that would overrun the budget evicts strictly least-recently-used entries until the
incoming artefact fits, and names every eviction it performed in the result it returns. An
artefact larger than the entire budget raises `CacheTooSmall` *before* anything is evicted,
rather than discarding the whole cache and then failing anyway. The index is guarded by a file
lock so that several node processes on one machine share the cache safely, and the download
itself is performed outside that lock, since holding a cross-process lock across a multi-gigabyte
transfer would serialise every node on the host behind one fetch.

The third stage is the one the whole mechanism exists for. **After the bytes have been received,
the client recomputes the content identifier from those bytes itself, and a mismatch raises
rather than returning the weights.** A content identifier is not a label the daemon assigns; it
is a function of the bytes, and the only way to use it as a commitment is to evaluate that
function locally. Asking the daemon what it just served verifies nothing whatever, because the
daemon is precisely the component an adversary would have to compromise in order to serve
tampered weights. The module therefore carries its own implementation of the UnixFS and dag-pb
layout — a 262,144-byte chunker, sha2-256 leaves, a balanced DAG of at most 174 links per node —
so that the CID of the received artefact is derived from first principles and compared against
the CID that was requested. A disagreement raises `CIDMismatch`, which is never downgraded to a
warning and never recovered from. Layouts the implementation cannot reproduce, among them
HAMT-sharded directories, non-default chunkers, hash functions other than sha2-256 and trickle
DAGs, raise `UnsupportedDAG` rather than being waved through, because each of them yields a
different identifier for the same bytes and guessing would mean returning weights the module
cannot vouch for. There is no code path in the module that returns a path to bytes which did not
verify.

This is the property that permits a node to accept weights from a peer it does not trust, and it
is worth stating why in full. Under an ordinary HTTP download the client's confidence in what it
received is confidence in the *server*: it trusts a hostname, a certificate and whoever
administers the machine behind them, and if that machine is dishonest the client has no means of
noticing. Under content addressing the client's confidence is confidence in *arithmetic*. The
identifier was fixed before the transfer began; the check is performed on the bytes that actually
arrived; and a server that substitutes different bytes produces a different identifier and is
detected with certainty. The identity of the party that served the artefact therefore drops out
of the trust argument entirely, which is what makes it safe to fetch weights from an arbitrary
peer, a public gateway, or a cache of unknown provenance. This is the same shape of reasoning as
the signed `NodeRecord` of Section 7.2.2 and the Merkle commitment of Section 7.5.1, and it is
the architectural principle stated in Section 7.1: authority in this system derives from
cryptography, never from a network position.

A second, independent check closes the loop back to the chain. Where the CID was obtained from
`ModelRegistry`, the SHA-256 of the fetched artefact is compared against the `contentHash` the
registry records, and a disagreement raises `ContentHashMismatch`. The two checks answer
different questions: the CID check establishes that the bytes received are the bytes the CID
denotes, while the content-hash check establishes that those bytes are the ones the model
identifier was bound to on chain by its publisher. Together they turn `JobRequest.model` from an
unenforced string into an enforceable commitment — the concern raised in Section 7.6.1, where
`ModelRegistry` was introduced as the on-chain half of a mechanism whose off-chain half did not
then exist. The registry says which bytes; IPFS supplies them; the client proves that the two
agree before the weights are used. Where a caller elects to skip the SHA-256 re-read on a cache
*hit*, which for a multi-gigabyte artefact is minutes of disk, the resolved record sets
`content_hash_checked` to `False` so that the omission is visible in the results; the CID check
on download is never skippable.

One distinction must not be blurred. The data availability layer of Section 7.5.2 is a **local
stand-in**: `edgegrid/da.py` is this project's own code, reimplementing Celestia's commitment
property without Celestia, and it therefore delivers the binding guarantee but not the
availability guarantee. The weight distribution path is **not** a stand-in of that kind. It
speaks to an unmodified third-party kubo daemon over the real IPFS HTTP API, the identifiers it
verifies are real IPFS CIDs, and the bytes make a genuine round trip through software this
project did not write. The two are different in kind, and Section 7.8 records them as separate
entries for that reason. What the weight path shares with the DA layer is only that the daemon
in question runs locally rather than as a member of the public IPFS swarm, which bears on
reachability and on retrieval time but not on the verification property, since that property is
established by the receiving client and holds against any source whatsoever.

---

## 7.5 Module 4 — Agentic Verification

Module 4 answers the question on which the entire economic model rests: given that a provider
is an anonymous machine in somebody's house, why should a requester believe that the text it
received was produced by the model that was paid for, and that it is not nonsense? The answer
is an optimistic verification scheme — outputs are accepted by default and audited on a
statistical sample, with a challenge window during which a proof of misbehaviour can be
submitted. The module is implemented in `edgegrid/da.py`, `verification/validator.py` and
`verification/evaluator.py`.

### 7.5.1 The Commitment Chain

Immediately after generating an output, the provider constructs a chain of four linked
artefacts, each binding the next:

1. **The output hash.** `sha256(output)`, computed over the exact bytes returned to the client
 and carried on the signed `InferenceResult`.
2. **A data-availability blob.** The raw output bytes are submitted to a namespaced blob store
 under the namespace `edgegrid.inference.v1`. In the gateway pipeline a provenance record —
 job identifier, provider PeerID, model, prompt hash, output hash, token count, TTFT — is
 submitted as a companion blob in the same block, so that the block contains at least two
 leaves. This is not cosmetic: a Merkle block with a single leaf yields an empty inclusion
 proof, which proves nothing about a tree. The output blob is submitted last and unpadded, so
 that `sha256(blob) == commitment.output_hash` exactly.
3. **A Merkle inclusion proof.** Blobs accumulate into a pending batch; sealing a block
 computes a binary Merkle root over the blob payloads and fixes every inclusion proof in the
 block. Leaves are hashed as `sha256(0x00 || data)` and internal nodes as
 `sha256(0x01 || left || right)`, with an odd tail duplicated. The domain separation between
 leaf and node prefixes is what prevents a leaf from being reinterpreted as an internal node,
 which is the classical second-preimage attack on naive Merkle constructions. Verification,
 `verify_proof()`, is a pure function: a verifier can check a proof knowing only the data, the
 sibling path and the root, and needs no access to the store that produced it.
4. **An on-chain reference.** The provider calls `recordCommitment(jobId, outputHash,
 merkleRoot, leafIndex, blobRef)` on `VerificationContract`, which binds the provider to
 exactly one output for that job and starts the challenge window.

The security property this chain delivers is that a provider **cannot show one output to the
requester and a different one to a verifier**. The on-chain record pins a specific hash under a
specific Merkle root at a specific leaf index; any blob a verifier is later shown must both
hash to the committed value and prove its inclusion under the committed root, and no
substitution satisfies both conditions.

### 7.5.2 Scope Note: This Is a Local Stand-in for Celestia, Not Celestia

The Phase-1 design specified that these blobs be posted to Celestia as namespaced data
availability blobs. **The implemented system does not integrate Celestia.** `edgegrid/da.py`
implements the same *interface* and the same *cryptographic commitment property* against a
local append-only store: namespaced blobs, batched into blocks, each block committing to its
blobs through a binary Merkle tree, with an inclusion proof any party can verify
independently. This is stated plainly here, in the module description, rather than in a
footnote, and is repeated in the scope table of Section 7.8.

It is worth being exact about what is and is not obtained by this substitution. What is
genuinely obtained is the binding property described above, which is the property the fraud
proof actually consumes: the on-chain reference pins a Merkle root, the proof is checkable by
arithmetic, and `VerificationContract.proveDataMismatch` recomputes that same root inside the
EVM using a byte-compatible reimplementation of the same hashing scheme. What is *not*
obtained is Celestia's actual guarantee — that the data is *available* to any party who asks
for it, enforced by a decentralised validator set performing data-availability sampling. In
the present implementation a provider that refuses to serve its blob can withhold data;
Celestia exists precisely to make that impossible. Migration is nevertheless narrow in scope:
`submit_blob()` and `get_blob()` are the only two methods that would be reimplemented against
a Celestia light node, and the Merkle logic, the contract, and every consumer above them are
unchanged.

### 7.5.3 Statistical Sampling at Five Percent

Auditing every job with a judge model would cost as much as producing the answer, which would
defeat the purpose of the network. Module 4 therefore audits a five percent sample,
`SAMPLE_RATE = 0.05`.

The sampling decision is not a bare random draw. `should_audit()` computes a keyed hash,
`sha256(seed || job_id)`, and interprets its leading eight bytes as a uniform value in `[0, 1)`,
auditing the job when that value falls below the rate. This construction has two properties that
a naive `random.random()` does not. It is **deterministic**: any party holding the epoch seed
can recompute the audit set and check that a validator sampled honestly, rather than selecting
its targets — which closes the obvious attack in which a validator audits only its competitors.
And it is **unpredictable to the provider**: a provider that does not yet hold the seed cannot
determine which of its jobs will be examined, and therefore cannot behave honestly on the
audited fraction and dishonestly on the remainder.

Jobs that are not sampled are returned as outcomes with `audited=False` rather than being
omitted from the results. A consumer of the audit results can therefore never mistake "was not
looked at" for "was looked at and passed".

### 7.5.4 Blob Verification Before Judging

Within an audit, the checks are ordered so that the cheap, objective one runs first.
`ValidatorPool.audit()` begins by calling `verify_commitment()`, which fetches the committed
blob, recomputes its SHA-256, compares it with the hash the provider placed on chain, and
verifies the Merkle inclusion proof against the block root. Only if all three succeed does the
pool spend a single judge call.

This ordering is a substantive design decision rather than an optimisation. A hash mismatch is
a **fraud proof**: it is a mathematical fact, not an opinion. It requires no language model, no
subjective rubric, no quorum and no appeal to anyone's judgement, and it costs one hash
computation. Detection of this class of fraud is therefore essentially free, is certain, and
cannot be disputed. Accordingly, an outcome produced by this path is marked `fraud_proof=True`
and settlement may act on it without waiting out a challenge window, whereas everything
downstream of it is explicitly an opinion. It is also worth noting that the answer subsequently
judged is read back *out of the DA layer* — it is the bytes actually committed, never a copy
the provider hands over separately, which would defeat the purpose of committing at all.

The same check exists on chain in a trustless, permissionless form.
`VerificationContract.proveDataMismatch(jobId, blobData, siblings)` takes the raw blob and a
sibling path, recomputes the leaf hash and folds the path into a root *inside the EVM*, and
confirms fraud only if the revealed data genuinely lies under the committed root and genuinely
fails to hash to the committed output hash. Crucially, the direction of each hashing step is
derived from the committed `leafIndex` rather than taken from the challenger, so a challenger
cannot substitute some other job's blob from the same DA block. If the revealed blob does hash
correctly, the call reverts with `NoMismatch`, so an honest provider cannot be slashed by a
well-formed but truthful challenge. Recorded gas for this call on the local chain is 118,753.

### 7.5.5 LLM-as-a-Judge

Where the blob checks out, the content of the answer is assessed by a judge model applying a
fixed five-point rubric for factual accuracy, defined as a system prompt in
`verification/evaluator.py`. The rubric scores 5 for a completely correct answer down to 1 for
a fabricated or entirely off-topic one, and the judge is instructed explicitly to assess factual
accuracy alone and not to reward style, length or hedging. The judge is required to reply with a
single JSON object carrying a score, a verdict label and a one-sentence reason. A configurable
threshold, `PASS_THRESHOLD = 3`, converts the score to a verdict.

Three properties of this component are worth recording, because each corrects a specific defect
that a straightforward implementation exhibits.

First, **the backend is always explicit**. `Judge` accepts `groq`, `ollama` or `mock` and
refuses anything else; there is no `auto`. Selecting the `groq` backend without an API key
raises `JudgeConfigError` rather than silently substituting a local or mock judge. The mock
backend is reachable only by naming it, tags every verdict it produces with
`judge_backend="mock"`, and is excluded from any reported figure.

Second, **the model recorded on a verdict is the model the server reports**, read back from the
response, rather than the string that was requested. The two differ whenever a name is aliased
or a provider silently substitutes a model, and a verdict that names the wrong judge is not
evidence.

Third, **the verdict is derived from the numeric score alone**, never from the label the model
wrote. Where a model's self-declared label contradicts its own score, the contradiction is
recorded in the reason field for analysis but is not obeyed. One threshold therefore governs
every backend and every model.

A related instrument, `verification/paraphrase_check.py`, measures the judge's self-consistency
by generating truth-preserving paraphrases of an answer and testing whether the verdict moves.
The motivation is recorded directly from an earlier run in which the same claim expressed two
ways received scores of 2 (fail) and 5 (pass) under an identical rubric. Since a single verdict
slashes real collateral, a detection rate is only meaningful alongside a measurement of how
often the judge disagrees with itself.

### 7.5.6 The Three-Valued Verdict: Why `error` Is a Correctness Requirement

`VerdictKind` has three values: `pass`, `fail` and `error`. The third is not a convenience or a
diagnostic nicety; it is a correctness requirement, and its absence is a specific and serious
bug.

A verdict in this system is the input to a slashing decision. Consider a two-valued verdict
under which the judge is unreachable — the API is down, the local model has been evicted, the
request timed out. The implementation must return something. If it returns `fail`, then an
outage of the verification infrastructure is recorded as unanimous detection of fraud, and
honest providers are slashed *en masse* for a failure that occurred on the validator's side of
the network. If instead it returns `pass`, then an attacker acquires a trivially exploitable
strategy: induce judge failures, and every fraudulent output is automatically approved. Both
branches are unacceptable, and no third option exists within a two-valued type. The same
argument applies to an unparseable response: an earlier version of this component defaulted an
unparseable reply to score 3, which is exactly the pass threshold, so malformed output silently
became an approval.

The three-valued type resolves this by making "the system does not know" representable.
`Judge.score()` exhausts a bounded retry budget with exponential backoff and then returns
`VerdictKind.ERROR` carrying the underlying exception text; `_parse()` raises rather than
guessing when no score can be recovered, and the caller converts that to `ERROR`. In the
validator pool, `ERROR` votes are counted towards neither side, and a pool that cannot reach
quorum returns `ERROR` as its collective outcome. Settlement is required to interpret `ERROR`
as *do not settle yet* — neither as innocence nor as guilt.

The three-valued outcome is mirrored on chain. `VerificationContract.VerdictKind` declares
`NONE`, `PASS`, `FAIL` and `ERROR`, and only `FAIL` triggers slashing; `PASS` and `ERROR` are
recorded and leave the escrow to settle normally when the challenge window closes.

### 7.5.7 The Validator Pool

`ValidatorPool` composes several independent judges into a voting body. Each validator scores
the same answer independently — in parallel, via a thread pool — and the pool applies a quorum
rule. The implementation is candid about the limits of what it provides. A pool constructed
from several distinct `Judge` objects with different backends or models offers genuine
independence; a pool that reuses a single `Judge` is cheaper but produces correlated votes, and
the pool records which case obtains in an `independent` attribute so that a results table can
never imply an independence it did not possess.

The tally checks `fail` before `pass`, from which it follows that with a quorum at or below half
the pool a single dishonest validator can force a slash. This is stated explicitly in the
implementation's own documentation, together with the corresponding operational requirement
that quorum be set above `n/2` in any deployment where validators are not trusted. Cases in
which both sides independently reach quorum are flagged as `split` rather than silently
resolved.

Until recently the case for composing judges in this way was an argument rather than a
measurement. It is now the latter. Chapter 8 reports a panel of distinct judge models evaluated
against the two corruption strategies on which a single small judge failed, and finds that
applying a majority rule across the panel lowers the false-positive rate below that of either
individually reliable member while leaving recall essentially unchanged. The quorum is therefore
not only the defence against a dishonest validator for which it was designed; it also measurably
improves accuracy against validators that are honest but fallible. The figures, together with the
caveat that the measured panel included members whose completion rate was too low for their votes
to carry weight, belong to Chapter 8 and are not repeated here.

---

## 7.6 Module 5 — Blockchain Settlement

Module 5 is the financial layer. It holds the requester's payment while the work is performed,
releases it when the work has survived scrutiny, returns it when the work is proven fraudulent,
and confiscates the provider's collateral in the latter case. It is implemented as four
Solidity 0.8.24 contracts under `contracts/contracts/`, compiled and deployed to a local
Hardhat chain (chain id 31337), with a Python bridge in `edgegrid/chain.py` and a byte-for-byte
equivalent off-chain implementation of the same state machine in `edgegrid/ledger.py`.

### 7.6.1 The Four Contracts

**`NodeRegistry`** holds provider collateral and is the only place in the system where stake is
created or destroyed. Two properties distinguish it from a naive registry. Slashing is callable
by exactly one address — the `VerificationContract`, configured through `setSlasher` — and any
other caller reverts with `NotSlasher`; an earlier sketch had no access control at all, so any
account could confiscate any provider's collateral. And stake remains slashable while it is
unbonding: `requestUnstake` moves collateral into a timelocked bucket that `slash` still
reaches, so a provider that sees a fraud proof coming cannot escape it by beginning a
withdrawal. All value leaves through a pull-payment `withdraw()` guarded by a reentrancy mutex,
never by a push in the middle of another state transition. The deployed minimum stake is 10
GRID and the unbonding period is 3,600 seconds.

**`Marketplace`** holds one escrow per job and enforces the state machine described in
Section 7.6.2. A provider must be an active staked node at the moment an escrow is opened
against it, since otherwise a subsequent slash would have nothing to bite.

**`VerificationContract`** records what a provider claims to have produced and is the only
address permitted to slash that provider or refund its requester. It exposes the two resolution
paths described in Section 7.6.3. Only the provider the marketplace actually awarded may record
a commitment, and only while the escrow is `OPEN`.

**`ModelRegistry`** binds a model identifier to the content hash of the weights that identifier
refers to, with monotonic versioning and a first-come publisher claim. Without it,
`JobRequest.model` is an unenforced string and a provider could serve a two-billion-parameter
model against a request for a seven-billion-parameter one leaving no on-chain trace. This
contract was named in the Phase-1 design and had no implementation; it now has one.

Shared ownership and reentrancy primitives live in `Auth.sol` and are kept in-repository rather
than pulled from OpenZeppelin, so that the contract set compiles with no npm dependency beyond
Hardhat itself. Total deployment gas for the four contracts, recorded in
`contracts/deployment.json`, is 4,831,798.

### 7.6.2 The Escrow State Machine

The escrow lifecycle is shown in the settlement_states figure and comprises five states:

**Figure 7.2** — `docs/figures/settlement_states.png` — Escrow state machine and the 80/20 distribution of slashed stake.


```
OPEN ──(provider records commitment)──▶ AWAITING_VERIFICATION
AWAITING_VERIFICATION ──(window elapses, no valid challenge)──▶ SETTLED
AWAITING_VERIFICATION ──(fraud confirmed)──▶ SLASHED
OPEN ──(no commitment before the award timeout)──▶ REFUNDED
```

`openEscrow` locks the clearing price against a named provider before any work begins.
`beginVerification`, callable only by the `VerificationContract`, advances the escrow to
`AWAITING_VERIFICATION` and stamps the challenge deadline. `release` moves it to `SETTLED` and
credits the provider; it is deliberately **permissionless**, so that the honest outcome does
not depend on any privileged party remaining online — anyone may trigger it once the deadline
has genuinely passed, and it reverts with `ChallengeWindowOpen` if it has not.
`refundOnFraud`, callable only by the `VerificationContract`, moves the escrow to `SLASHED` and
returns the funds to the requester. `cancel` allows a requester to reclaim an escrow whose
provider never delivered, after an award timeout of 600 seconds.

The contrast with the earlier sketch is instructive: that version had a single `settled` boolean
and a world-callable `settle(jobId, slashed)`, which meant any account could declare any job
fraudulent and redirect the escrow. In the present design the only transition an arbitrary
caller can trigger is the honest one, and only after the window has actually expired. As in
`NodeRegistry`, value never leaves inside a state transition: payouts are credited to a
`withdrawable` balance and pulled by their owner, so a malicious requester or provider contract
cannot re-enter the settlement path.

The identical state machine is implemented off-chain in `edgegrid/ledger.py` with integer
accounting in wei and an injectable clock, so that an experiment can run against either backend
and produce the same `SettlementRecord` objects. Integer accounting is what allows
`check_invariants()` to assert exact equality rather than approximate closeness when checking
that value is conserved: every unit that leaves a requester must land in exactly one of
provider, validator or treasury. `get_backend()` is the single place where the choice between
simulation and chain is made, and it never falls back silently — a missing deployment file, an
unreachable RPC endpoint, a chain-id mismatch or an address with no code each raise a distinct
error naming the remedy.

### 7.6.3 The Challenge Window and the Two Resolution Paths

Recording a commitment starts a challenge window, deployed at 3,600 seconds. Within the window
a commitment may be resolved by either of two mechanisms, and the contract records which one
fired, as a `ResolutionKind` of `DATA_MISMATCH_PROOF` or `VALIDATOR_VERDICT`. The distinction is
retained because the two carry very different trust assumptions and a settlement record must
always be able to state whether it rests on a proof or on an oracle.

`proveDataMismatch` is trustless and permissionless, as described in Section 7.5.4: correctness
is computed by the EVM rather than asserted by a caller. `submitVerdict` is an oracle: it
carries an off-chain judge's ruling, and it is therefore restricted by the `onlyValidator`
modifier to addresses that are both on an allow-list *and* currently hold active stake. A
verdict is an assertion rather than a proof, so the party making it must have something at risk.
The earlier sketch permitted any address to write any verdict; that is the hole this closes.

Once the window closes without a confirmed fraud, the commitment can no longer be challenged —
`_challengeable()` reverts with `ChallengeWindowClosed` — and `release` becomes callable by
anyone. The window is declared `immutable` in the contract so that it cannot be shortened out
from underneath a challenge that is already in flight.

Measured gas on the local chain for the settlement path, recorded in
`docs/results/settlement-onchain-20260902T102520Z/gas_used.json`, is 126,933 for `openEscrow`,
184,028 for `recordCommitment`, 61,119 for `release`, 141,621 for `submitVerdict` and 118,753
for `proveDataMismatch`.

### 7.6.4 The 80/20 Slash Split and Why Auditing Is Individually Rational

On confirmed fraud, the provider's stake is slashed up to the escrowed amount and the proceeds
are divided in a fixed ratio: **eighty percent to the account that reported the fraud, twenty
percent to the DAO treasury**. This is enforced on chain as
`VALIDATOR_SLASH_BPS = 8_000` out of `BPS = 10_000` in `NodeRegistry.slash`, and the two shares
are computed so that they always sum exactly to the slashed amount with no dust remaining. The
same constants appear as `VALIDATOR_SLASH_SHARE = 0.80` and `TREASURY_SLASH_SHARE = 0.20` in the
Python configuration and are carried on every `SettlementRecord` as `validator_reward` and
`treasury_amount`.

The incentive argument for this split is the heart of the verification economics, and it runs as
follows.

Auditing is not free. A validator that audits a job must fetch a blob, verify a proof and,
usually, pay for a judge model's inference. If detection carried no reward, a rational validator
would perform no audits at all, since the benefit of detection accrues entirely to the requester
and to the network while the cost falls entirely on the validator. Verification would then be a
pure public good, and it would be under-supplied in exactly the way public goods always are. The
80 percent share converts detection from a cost into a revenue opportunity: an audit has a
positive expected value whenever the probability of detection multiplied by eighty percent of
the escrow-capped slash exceeds the cost of the audit. **Auditing becomes individually rational,
so it happens without anyone being obliged to do it.** This is the property the network needs,
because a permissionless network cannot compel anyone to perform work.

The residual twenty percent to the treasury is not merely a protocol fee. It exists to make
self-reporting unprofitable. Were the reporter to receive the entire slash, a provider could
submit fraudulent work and then report itself, recovering its own confiscated stake in full and
converting the slashing mechanism into a costless formality; with an intact 100 percent rebate,
the expected cost of being caught would be zero. Withholding twenty percent guarantees that
being slashed is strictly loss-making regardless of who reports it, which preserves the
deterrent while keeping the reward large enough to fund genuine auditing. The twenty percent
share simultaneously funds the protocol's own operation.

Two further honesty properties attach to the split. The slash is capped at the provider's
remaining collateral, active plus unbonding; where the collateral does not cover the escrowed
amount the shortfall is reported as `fully_covered = false` rather than silently rounded away.
And the requester is made whole from *the escrow*, not from the slashed stake — the two flows
are independent, so the requester's refund does not compete with the validator's reward.

---

## 7.7 End-to-End Data Flow

The complete lifecycle of a single job, from client request to settlement, is shown in
the sequence figure. The eight steps are as follows.

**Figure 7.3** — `docs/figures/sequence.png` — End-to-end job lifecycle across the eight protocol steps.


**Step 1 — Request and escrow.** The client submits a request through the Python SDK or the
FastAPI gateway, and the payment for the job is locked in `Marketplace` by
`openEscrow(jobId, provider)` before the provider performs any work. One ordering detail should
be stated precisely, because it differs from the informal description in the synopsis:
`openEscrow` requires a *named* provider and reverts with `ProviderNotActive` unless that
provider currently holds active stake, since otherwise a later slash would have nothing to bite.
The escrow is therefore funded once the auction of Step 4 has named a winner and fixed the
clearing price, and always before the inference of Step 5 is commissioned. Funds are committed
in advance of the work, which is what makes the provider's effort worth undertaking, but the
amount cannot be fixed before the market has determined it.

**Step 2 — Mempool broadcast.** The requester constructs a `JobRequest` carrying the prompt,
the model, the token budget, the price ceiling, the latency budget and the minimum tier, signs
it with its secp256k1 key and publishes it to the GossipSub topic `edgegrid/jobs/v1`. Every
subscribed node receives it; each verifies the signature against the requester's claimed wallet
before considering it, and declines silently if it does not serve the requested model.

**Step 3 — Bidding.** Eligible providers publish signed `Bid` messages to `edgegrid/bids/v1`,
each carrying a price, an estimated time-to-first-token, a warm flag read from the node's own
runtime, a tier and a stake. The requester collects bids for the two-second window, verifying
each signature on arrival and counting every rejection by reason.

**Step 4 — Second-price auction and award.** The window closes and `market.evaluate()` ranks
the bids. Ineligible bids are excluded with a recorded reason; eligible bids are ordered by
effective price with the warm-start handicap applied to the score. The winner is the lowest
effective bid and the clearing price is its threshold price — the runner-up's price adjusted
for the handicap, capped at the requester's ceiling. A signed `JobAward` is published to
`edgegrid/awards/v1`, and the winning node recognises itself in it and prepares to serve.

**Step 5 — Edge inference.** The winning node runs the job on its local Ollama runtime,
streaming tokens to the client as they are produced. Time-to-first-token is stamped at the
arrival of the first chunk carrying non-empty text, and the real token counts are read from the
runtime's own counters on the final chunk. The node returns a signed `InferenceResult` carrying
the output, the measurements and the SHA-256 of the output.

**Step 6 — Commitment.** The provider submits the output as a namespaced blob to the data
availability layer, alongside a provenance blob, and seals the block, which fixes the Merkle
root and the inclusion proof. It then calls `recordCommitment` on `VerificationContract` with
the output hash, the Merkle root, the leaf index and the blob reference. This transaction binds
the provider to exactly one output and moves the escrow to `AWAITING_VERIFICATION` with a
challenge deadline one hour ahead.

**Step 7 — Sampled verification.** The keyed sampling function decides, deterministically and
unpredictably to the provider, whether this job falls in the five percent audited sample. Where
it does, a validator fetches the committed blob, recomputes its hash and checks the Merkle
inclusion proof against the block root. A mismatch is a fraud proof and terminates the audit
immediately, without a judge call. Otherwise the committed bytes — never a copy supplied
separately by the provider — are passed to the LLM-as-a-Judge, which returns a rubric score
that maps to `pass`, `fail` or `error`.

**Step 8 — Settlement or slashing.** On a pass, or on an unsampled job, the escrow is released
to the provider by `release()` once the challenge window has elapsed; the call is
permissionless. On a confirmed fail, `_confirmFraud` slashes the provider through
`NodeRegistry.slash`, credits eighty percent of the proceeds to the reporting validator and
twenty percent to the treasury, and returns the escrow to the requester through
`refundOnFraud`. On an `error`, nothing is settled and the job waits: an outage is neither a
payment nor a slashing. Every path produces a `SettlementRecord` in which value is conserved
exactly.

---

## 7.8 Scope and Limitations

The design set out in the Phase-1 synopsis names several specific external systems. The
implemented prototype substitutes locally runnable equivalents for six of them. These six
substitutions are set out here explicitly, with the reason for each and the migration path back
to the designed component, because a declared limitation is a scope decision whereas an
undeclared one is a defect discovered by the examiner.

In every case the substitution was chosen to preserve the *interface and the security property*
being relied upon, so that the surrounding system is exercised for real and the substituted
component is the only thing that changes.

| # | Design intent (synopsis) | Implemented reality | Reason | Migration path |
|---|---|---|---|---|
| 1 | Arbitrum Stylus contracts in Rust compiled to WASM | Four Solidity 0.8.24 contracts on a local Hardhat EVM chain (chain id 31337) | Stylus requires an Arbitrum testnet deployment and a Rust/WASM toolchain; a local EVM chain permits deterministic, repeatable, gas-instrumented runs with no external dependency and no testnet faucet | The contract logic — access control, escrow state machine, 80/20 slash split — is EVM-semantic and is portable to Stylus, or deployable unchanged to Arbitrum's EVM-compatible layer |
| 2 | Celestia namespaced data-availability blobs | Local Merkle-committed DA layer (`edgegrid/da.py`): namespaced blobs, Merkle-rooted blocks, verifiable inclusion proofs | Celestia integration requires a running light node and testnet tokens; the binding property the fraud proof consumes is delivered by the Merkle commitment alone | Only `submit_blob()` and `get_blob()` are reimplemented against a Celestia light node. What is *not* delivered today is Celestia's availability guarantee enforced by data-availability sampling |
| 3 | vLLM with PagedAttention on CUDA GPUs | Ollama streaming runtime on CPU | The development hardware has no NVIDIA GPU (`detected_by = "no accelerator probe matched"`, Tier 1); an implemented but never-executed CUDA path would be an unverified claim | The engine is reached through one `run()` entry point returning a signed `InferenceResult`; a vLLM backend is an alternative implementation of that method |
| 4 | Real economic stake in a live token | Testnet value on a local chain; 1 GRID == 1 ether == 10^18 wei, minimum stake 10 GRID | No token has been issued and none should be; the mechanism under test is the accounting and the incentive structure, not the market price of a coin | Unit accounting is already integer wei and identical to a production deployment; only the chain and the token contract change |
| 5 | Validator agents fine-tuned on TruthfulQA and Chatbot Arena preference data | Off-the-shelf judge models applying a fixed five-point rubric, with the backend always explicitly named and recorded | Fine-tuning a judge was not feasible in Phase 1; an off-the-shelf judge whose self-consistency is *measured* is more defensible than a fine-tuned one whose behaviour is assumed | The `Judge` abstraction takes a backend and a model identifier; a fine-tuned model substitutes at that boundary. `verification/paraphrase_check.py` already provides the instrument for evaluating whether the substitution improves matters |
| 6 | "Production-deployed" network of real third-party operators | A prototype running on one development host, as multiple processes and, for the network experiments, as containers each holding its own network namespace on a private bridge, with all results recorded under `docs/results/` | Phase 1 is a design-and-prototype phase; no external operator has run a node | Explicitly deferred to future work. **The system is not deployed, and no claim of deployment is made anywhere in this report** |

One component named in the synopsis is deliberately absent from this table, and the omission is
the point. Content-addressed distribution of model weights over IPFS, described in
Section 7.4.6, is implemented against a real kubo daemon rather than against a locally written
equivalent, and is therefore not a substitution at all. Together with the EVM of row 1, it is one
of the two places where the designed external system is genuinely present; it should not be read
as being of the same kind as the data availability stand-in of row 2.

Five further limitations are recorded for completeness.

**Sample sizes are small.** The cold/warm latency comparison in
`docs/results/inference-benchmark-20260902T101341Z` rests on three matched pairs, and the
steady-state warm characterisation on eight trials. The trial count is reported alongside every
statistic precisely so that a reader can see how much weight a percentile can bear; at n = 8 a
95th percentile is barely distinguishable from the maximum, which is a property of the sample
size and not of the estimator.

**Judge independence is bounded.** Where a validator pool reuses one `Judge` instance the votes
are correlated rather than independent, and the pool records which case obtained. The default
quorum of one is appropriate only for a single-validator prototype; any deployment in which
validators are not mutually trusted requires a quorum above half the pool, since the tally
checks `fail` before `pass`.

**Model-weight distribution is implemented; two things about it are not.** The design specifies
IPFS-based retrieval of weights by content hash, with an LRU cache and verification of what was
received. That is implemented in `edgegrid/weights.py` against a real kubo daemon, as described
in Section 7.4.6, and the earlier statement in this report that it was unimplemented is
superseded. Two boundaries remain. The artefacts exercised in the recorded runs are synthetic
byte sequences of realistic sizes rather than real GGUF files, since the verification property
under test is a property of bytes and is indifferent to what those bytes encode; and the verified
local path the resolver returns is not yet handed to the Ollama runtime as its model source, so
models are still pulled by Ollama in the ordinary way at inference time. Joining the two is a
matter of configuring the runtime's model directory and is future work, not a design gap.

**The network topology is containers on one host, not machines on a network.** Every timing in
the earlier network experiments was taken with all peers as processes on a single host sharing
one loopback interface, which made the path between peers a kernel memory copy with no bridge,
no interface queue and no address distinction. The present deployment model, implemented in
`discovery/run_swarm.py` and `deploy/grid/`, gives each node its own container with its own
network namespace and a distinct address on a private bridge (subnet 10.77.0.0/24), so that
packets cross veth pairs and the bridge's forwarding path, every node binds the same port — which
is impossible on shared loopback and is the cleanest evidence that the namespaces are genuinely
separate — and `tc netem` can shape the link per node. **This is not a LAN deployment and must
not be read as one.** It remains one kernel, one host and one clock, with no physical network
interface, no switch, no MTU negotiation and no wide-area path. What it removes is the loopback
shortcut; what it adds is a link whose delay is controllable. A deployment across genuinely
separate machines remains future work.

**Geographic proximity is not exercised.** The latency argument for edge inference rests in part
on routing a request to a physically nearby node. All nodes in the present experiments run on one
host, whether as processes or as containers, so the measured latencies isolate model and runtime
behaviour rather than network distance, and no claim about geographic routing gains is made. The
injected delays described above establish how the protocol responds to latency; they do not
establish what latency a real deployment would encounter.

Taken together, these six substitutions and five limitations define the boundary of Phase 1. The
network protocol, the auction, the measurement methodology, the commitment chain, the
verification logic, the weight distribution path and the settlement contracts are all implemented
and exercised against real cryptography, a real peer-to-peer stack, a real language-model
runtime, a real IPFS daemon and a real EVM. What has been substituted is, in every case, a hosted
external dependency, and in every case the substitution preserves the property the rest of the
system depends upon.
