# Chapter 1

# INTRODUCTION

## 1.1 Introduction

The last decade of progress in artificial intelligence has been driven less by any single
algorithmic breakthrough than by the sustained availability of computational capacity at a scale
that was previously the preserve of national laboratories. Large Language Models (LLMs) and the
family of generative systems built around them have moved, within a very short period, from
research artefacts to infrastructure on which commercial products, academic workflows and
consumer applications depend. That movement has produced a demand curve for inference —
the act of running a trained model against a user's input to obtain an output — that has grown
considerably faster than the physical infrastructure available to serve it.

The manner in which this demand is presently satisfied is the subject of this project. Almost the
entirety of the world's inference capacity is concentrated in a small number of hyperscale data
centres operated by a handful of organisations. Amazon Web Services, Microsoft Azure and
Google Cloud Platform supply the underlying accelerators and the virtualised environments in
which they run; a second and equally concentrated layer, comprising monolithic model API
providers, supplies the models themselves behind proprietary endpoints. A developer in
Bengaluru, a researcher in a university laboratory and a start-up building a consumer product
all issue their requests into the same few facilities, over the same few commercial interfaces,
under terms none of them negotiated.

This concentration is not merely a matter of market structure. It has direct and measurable
consequences for the cost, the responsiveness, the confidentiality and the availability of every
application that is built upon it. The physical distance between a user and the data centre that
answers the user's request is a lower bound on the latency of that request that no amount of
software optimisation can remove. The commercial margin embedded in a per-token price is a
lower bound on the cost of a workload that no amount of prompt engineering can remove. The
fact that a prompt containing a patient record, a legal draft or an unreleased design must leave
the user's administrative boundary in order to be answered is a confidentiality exposure that no
contractual clause can fully eliminate. And a service whose control plane resides in one region is
a service whose availability is bounded by the availability of that region.

While this concentration has been building, a second and quite separate development has been
under way at the other end of the hardware spectrum. The consumer devices that ordinary users
already own — gaming desktops carrying discrete graphics processors with sixteen gigabytes of
video memory or more, professional laptops built around Apple Silicon with unified memory and
on-package neural engines, and even well-provisioned multi-core workstations without any
discrete accelerator at all — have become genuinely capable of executing quantised language
models at interactive speeds. The quantisation and memory-management techniques that made
this possible are recent, well documented in the literature, and already available in mature
open-source runtimes. Yet the hardware in question sits idle for the overwhelming majority of its
operational life. It represents an installed base of computational capacity that has already been
manufactured, already been paid for, and is presently doing nothing.

The Edge Grid is a proposal to connect these two facts to one another. It is a Decentralized
Physical Infrastructure Network (DePIN) — a system in which independently owned physical
hardware is enrolled into a shared network, is paid for the useful work it performs, and is held
to account for that work by cryptographic and economic means rather than by the reputation of a
central operator. Its specific object is verifiable AI inference: a request enters an open market,
is auctioned to the peer that can serve it fastest within the requester's price ceiling, is executed
on that peer's own hardware, is committed to a data-availability layer so that the output cannot
subsequently be disputed, is sampled for correctness by an independent pool of validator agents,
and is settled on a blockchain — with the executing node's stake slashed if the output is shown to
have been fraudulent. There is no central directory, no central scheduler and no central payment
rail at any point in that sequence.

## 1.2 The Four Systemic Failures of Centralised Inference

The case for a decentralised alternative rests on four distinct failures of the present arrangement.
They are treated separately here because they have different causes and admit different remedies,
and because a system that addresses only one of them is of limited value.

### 1.2.1 Prohibitive Inference Cost

Inference is billed per token, and the price of a token includes the amortised capital cost of the
accelerator, the cost of the facility that houses and cools it, the operator's margin, and the margin
of the model provider layered above the operator. For a workload of any sustained volume this
compounds rapidly. The practical effect is exclusionary rather than merely expensive: an
independent developer, a student project or a research group without institutional funding is
priced out of experimentation at exactly the scale at which experimentation becomes informative.
The capital cost of the hardware itself, meanwhile, has already been borne — by the millions of
individuals who bought the consumer devices described in Section 1.3 — and is not being
recovered by anyone.

### 1.2.2 Latency Imposed by Geographic Distance

A request issued in southern India and answered in a facility in northern Virginia incurs a round
trip whose lower bound is set by the speed of light in fibre and by the number of intermediate
network hops, before the model has performed any computation at all. For batch workloads this
is immaterial. For the interactive workloads that now dominate — conversational assistants, code
completion, agentic tool use, non-player character dialogue in games — it is the dominant term in
the user's perception of responsiveness. The metric that captures this is time-to-first-token
(TTFT), the interval between the submission of a request and the arrival of the first token of the
response, and it is the metric against which this project's performance objective is stated. No
amount of model optimisation reduces a propagation delay; only moving the computation closer
to the user does.

### 1.2.3 Data-Privacy Exposure

To obtain an answer from a centralised provider, the user must transmit the question. Where the
question contains personally identifiable information, protected health information, privileged
legal material or commercially sensitive design data, the act of asking is itself a disclosure to a
third party whose retention, logging and training policies the user does not control and generally
cannot audit. Regulatory regimes in healthcare and finance treat such disclosure as a compliance
event in its own right. The consequence is that entire classes of genuinely valuable application
are foreclosed, not because the models are incapable, but because the delivery model requires an
export of data that the user is unwilling or legally unable to perform.

### 1.2.4 Single Points of Failure

A centralised service concentrates not only capacity but also control. A regional outage, a control-
plane defect, a change of commercial terms, a change of content policy or a unilateral withdrawal
of model access propagates instantaneously to every application built on that service. The failure
mode is correlated by construction: every dependent system fails at the same moment and for the
same reason, and none of them has a fallback, because the alternatives are equally concentrated
and equally opaque. This is a structural fragility in what has become, in a very short period, a
piece of general-purpose infrastructure.

## 1.3 The Latent Compute Opportunity and the Proposed Work

### 1.3.1 The Idle Installed Base

The four failures above are usually discussed as though the only remedy were to build more data
centres. The premise of this project is that a substantial reservoir of suitable capacity already
exists and is simply not addressable. High-end gaming systems built around discrete GPUs with
sixteen gigabytes or more of video memory can hold a quantised seven-billion-parameter model
resident and serve it at interactive rates. Apple Silicon machines, whose unified memory
architecture allows the GPU and neural engine to address system RAM directly, can do the same
without any discrete accelerator. Even a CPU-only workstation of the kind used to develop this
project can serve a small quantised model at a first-token latency below one second once the
weights are resident in memory.

These devices are, in aggregate, an enormous quantity of hardware that has already been
manufactured and paid for and that is idle for the great majority of its lifetime. What prevents it
from being used is not capability. It is the absence of three things: a way for a requester to find a
suitable peer without a central directory, a way for the two of them to agree a price without a
central broker, and a way for the requester to trust the output of a machine owned by a stranger.
Those three absences are precisely what this project sets out to supply.

### 1.3.2 The Edge Grid

The Edge Grid is organised as five interdependent modules, shown in Figure 1.1.

**Figure 1.1** — `docs/figures/architecture.png` — The Edge Grid system architecture: five modules from peer discovery through to on-chain settlement.


**Module 1 — Decentralized Discovery.** Every node generates an ECDSA secp256k1 keypair
from which three things are derived simultaneously: a stable libp2p PeerID, an Ethereum address
that is the node's settlement identity on chain, and the ability to sign every message it places on
the wire. Slow-changing facts about the node — its endpoint, wallet address, hardware tier and
the models it is willing to serve — are published as a signed record into a Kademlia distributed
hash table, giving O(log n) lookup without any directory server. Fast-changing liveness — whether
the node is up, what it currently has warm, its available memory — travels instead over signed
UDP heartbeats at a five-second interval, because pushing state that changes every few seconds
through a replicated DHT would republish constantly and still return a stale answer.

**Module 2 — Hybrid Market Protocol.** Job matching is an auction rather than a load balancer.
A signed job request is published to a libp2p GossipSub topic; eligible nodes reply on a bid topic
with a sealed bid carrying an offered TTFT, a price, and a flag indicating whether the requested
model is already resident. After a fixed bid window the requester runs a sealed-bid second-price
(Vickrey) procurement auction: the lowest acceptable bid wins, and the winner is paid the price
the runner-up would have had to be paid, so that bidding one's true reserve is the dominant
strategy. Latency budget, hardware tier and price ceiling are hard constraints rather than soft
preferences, and a node that already holds the model in memory receives a bonus applied to its
score but never to its payment.

**Module 3 — Edge Inference Engine.** The winning node executes the request on its own
hardware through a streaming runtime and returns tokens to the requester as they are produced.
Streaming is not a convenience here but a measurement requirement: TTFT is meaningful only if
the arrival of the first token can be observed, which a non-streaming call structurally cannot
provide. Token counts are taken from the runtime's own tokeniser rather than estimated from
whitespace. A hardware classifier assigns each node to Tier 1 (CPU), Tier 2 (integrated or
low-end GPU) or Tier 3 (discrete GPU with at least sixteen gigabytes of addressable memory),
and the tier is carried in the node's signed record so that a requester can constrain the auction.

**Module 4 — Agentic Verification.** After execution, the node writes the full output as a
namespaced blob into a data-availability layer, which batches blobs into blocks and commits to
each block through a binary Merkle tree, and posts the resulting reference on chain. A pool of
validator agents audits a five per cent random sample of jobs. The sampling decision is a keyed
hash of the job identifier, which makes it deterministic — anyone holding the epoch seed can
recompute the audit set and confirm that a validator sampled honestly — while remaining
unpredictable to a provider that does not yet hold the seed. Auditing proceeds cheapest-first: the
validator recomputes the blob hash and checks the Merkle inclusion proof, which is a certainty
and costs one hash, and only if that succeeds does it spend a judge call. The judge is an
LLM-as-a-Judge pipeline returning one of three verdicts — pass, fail, or **error** — with error
treated as "do not settle yet" rather than as either innocence or guilt.

**Module 5 — Blockchain Settlement.** Funds are locked in escrow before any work begins. The
escrow follows an explicit state machine — OPEN, AWAITING_VERIFICATION, and then SETTLED,
SLASHED or REFUNDED — shown in Figure 1.3. A passing
job releases the escrow to the provider once the challenge window has elapsed; a confirmed fraud
proof slashes the provider's stake, of which eighty per cent goes to the detecting validator, so
that auditing is individually rational, and twenty per cent to the treasury, so that self-reporting is
not profitable. A node registry holds the stake that makes Sybil attack costly, and a model
registry binds a model identifier to the content hash of the weights it names, so that a provider
cannot serve a cheaper quantisation against a request for a larger model without leaving an
on-chain trace.

The end-to-end lifecycle of a single job across these five modules is shown in Figure 1.2.

### 1.3.3 Scope of the Present Implementation

A Phase-1 report is judged on the honesty of its scope statement as much as on the ambition of its
design. The design described above is the target architecture. The implementation submitted with
this report realises that architecture against the same interfaces but diverges from it in seven
places, in six of them by substituting a locally runnable component, for reasons that are recorded
here rather than left for the examiner to discover. Table 1.1 states each divergence. The seventh
row is of a different kind and is marked as such: content-addressed weight distribution is
implemented against the designed external system itself rather than against a local equivalent, and
what remains outstanding there is the realism of the artefacts and of the network path, not the
mechanism.

**Table 1.1: Design intent versus the present implementation**

| Element of the Phase-1 design | What is implemented | Reason |
|---|---|---|
| Arbitrum Stylus contracts in Rust compiled to WebAssembly | Four Solidity 0.8.24 contracts — `NodeRegistry`, `Marketplace`, `VerificationContract`, `ModelRegistry` — compiled and deployed to a local Hardhat EVM chain (chain id 31337) | No Stylus toolchain is available on the development hardware. The settlement *semantics* — the escrow state machine, the access control, the challenge window, the 80/20 slash split — and the gas measurements are real |
| Celestia as the data-availability layer | A local namespaced blob store implementing the same interface, batching blobs into blocks and producing genuine binary Merkle inclusion proofs that any verifier can check | The **binding** property — that a provider cannot show a verifier one output and the chain another — is implemented and tested. Celestia's *availability* guarantee, which rests on data-availability sampling by a decentralised validator set, is not reproduced. Substituting a Celestia light node means reimplementing two functions and nothing else |
| vLLM with PagedAttention on CUDA | Ollama on CPU | The development machine has no NVIDIA GPU. CUDA-specific throughput work is therefore explicitly outside the scope of this phase |
| IPFS-distributed model weights | Implemented, and the only row in this table that is not a substitution: `edgegrid/weights.py` publishes and fetches weights through a real kubo IPFS daemon, recomputing the content identifier from the received bytes before returning them, with a byte-budgeted LRU cache above it. The model identifier remains bound on chain to its content hash in `ModelRegistry` | Measured in Sections 8.2.4 and 8.2.5 over five artefacts of 64 KiB to 48 MiB. Two boundaries remain: the artefacts exercised are synthetic byte sequences rather than real GGUF files, and the daemon is on the same host, so the fetch timings are client-side retrieval and verification cost and not wide-area transfer |
| Validator agents fine-tuned on TruthfulQA and Chatbot Arena | An off-the-shelf model used as judge, with a curated TruthfulQA-derived question set used for evaluation | No fine-tuning budget or data pipeline. Reported judge accuracy is therefore a lower bound, not an upper one |
| Economic stake of real value | Test-denominated stake on a local chain | No mainnet deployment |
| Next.js dashboard and Grafana heatmap | A static operator dashboard served directly by the FastAPI gateway | Reduced client-layer scope for Phase 1; the gateway's OpenAI-compatible endpoint, which is the substantive migration claim, is fully implemented |

One further correction is recorded here. The Phase-1 presentation compared The Edge Grid
against five existing systems across seven dimensions and scored the proposed system as
achieving full coverage, including a dimension described as production deployment. The system
is not deployed in production, and no claim of production deployment is made anywhere in this
report. That dimension is withdrawn from the comparative analysis and is restated as future work
in Chapter 9.

## 1.4 Organization of the Report

This report is organised into nine chapters.

**Chapter 1 — Introduction** establishes the context of the work. It describes the concentration of
global AI inference capacity in a small number of centralised operators, sets out the four
systemic failures that follow from that concentration — prohibitive cost, latency imposed by
geographic distance, data-privacy exposure and single points of failure — and identifies the
reservoir of idle consumer hardware that the project seeks to bring into productive use. It then
introduces The Edge Grid and its five modules, and states plainly, in Table 1.1, where the present
implementation substitutes a local component for the one named in the original design.

**Chapter 2 — Problem Statement** analyses the four categories of existing system that presently
address, or fail to address, the delivery of AI inference: centralised cloud infrastructure
providers, monolithic model API providers, proof-of-work blockchain compute networks, and
multi-agent routing DePINs. Each is examined for its specific failure mode rather than dismissed
generically, and the chapter closes with a precise statement of the problem this project
undertakes to solve.

**Chapter 3 — Objectives** enumerates the seven objectives of the project. Each objective is
expanded to state what it means concretely at the level of an implemented subsystem, and, more
importantly, how its achievement is to be measured — the metric, the instrument that produces
the metric, and the artefact in which the measurement is recorded.

**Chapter 4 — Literature Review** surveys the twenty core research papers that underpin the
design, organised into four themes: peer-to-peer networking and decentralised scheduling;
blockchain settlement, trust and fraud prevention; decentralised LLM inference engines and
directly comparable systems; and agentic verification and LLM output quality. Each paper is
summarised for its contribution, the module of this project it informs, and the limitation it leaves
open.

**Chapter 5 — Comparative Analysis** places the surveyed systems side by side across the
architectural dimensions that matter for an open inference market — economic incentives, Sybil
resistance, blockchain settlement, layer-2 gas optimisation, a dedicated data-availability layer,
open-source runtimes and judge-based verification — and identifies which combinations of these
have and have not previously been demonstrated together.

**Chapter 6 — Research Gap Identified** draws the conclusion of the comparative analysis into an
explicit statement of what is missing from the existing literature. In summary: each of the five
components has been studied and demonstrated independently, but no published system
integrates peer-to-peer discovery, a market protocol, edge inference, verifiable outputs and
blockchain settlement into a single open and economically self-sustaining network. Petals
demonstrates the feasibility of consumer-GPU distributed inference but supplies no incentive
layer and no Sybil resistance; PolyLink supplies blockchain settlement and judge-based
verification but no data-availability layer; Parallax and Navigator supply state-of-the-art
scheduling but assume fully cooperative nodes.

**Chapter 7 — Proposed System** presents the design of The Edge Grid in full: the five-module
architecture, the wire contract that binds the modules together, the auction rule and its incentive
properties, the verification pipeline and its sampling discipline, the escrow state machine and
the slashing distribution, and the end-to-end data flow from client request through to settlement
or slashing. It also documents the experimental protocol by which each objective of Chapter 3 is
to be evaluated.

**Chapter 8 — Results and Discussion** reports what the implemented system was measured to do.
It sets out the experimental protocol and the parameters held constant, presents the four
experiments — latency, auction convergence, verification accuracy, and cost and settlement —
together with the settlement measurement taken against a live chain and three later measurements
reported alongside the experiments they extend: content-addressed weight distribution against a real
IPFS daemon, the auction re-run across containers holding separate network namespaces under
injected link delay, and the same corruption set re-judged by a panel of larger models under three
quorum rules. It then revisits each of the seven objectives of Chapter 3 against the measurement
that bears on it, and states the threats to the validity of every result reported.

**Chapter 9 — Conclusion and Future Work** summarises what has been implemented
and measured in Phase 1, states the limitations of the present implementation without
qualification, records that the panel measurement refuted a hypothesis this report itself had
advanced, and sets out the work that remains: a genuine multi-machine deployment, a paid or
self-hosted judge tier so that a verification experiment is not truncated by rate limits, a
human-adjudicated honest set against which judge precision can be measured cleanly, migration of the
data-availability layer to Celestia, migration of the settlement contracts to Arbitrum, a CUDA
inference path, weight distribution exercised over a public IPFS swarm with real model files, and
zero-knowledge machine learning as a replacement for optimistic verification.

---

# Chapter 2

# PROBLEM STATEMENT

## 2.1 The Core Problem

The delivery of AI inference is, at present, structurally centralised. A small number of
organisations own the accelerators, operate the facilities that house them, and control the
interfaces through which those accelerators are reached. Every developer who wishes to run a
foundational model must transact with one of them, on terms set by them, at prices set by them,
subject to content and retention policies set by them, and must accept the latency imposed by the
geographic position of their facilities. The consequence is a market in which cost is high,
responsiveness is bounded below by physical distance, sensitive data must be exported in order
to be processed, and the failure of a single operator is felt simultaneously by every application
that depends on it.

Simultaneously — and this is the part of the problem that makes it tractable rather than merely
regrettable — a very large quantity of hardware capable of performing the same work sits idle.
The problem is therefore not one of insufficient global capacity. It is one of *addressability*: there
exists no open mechanism by which a requester can locate an idle machine owned by a stranger,
agree a price with it, obtain a result from it, and be confident that the result is genuine. Each of
those four steps is presently performed by a trusted intermediary, and it is the intermediary, not
the hardware, that is the scarce and expensive resource.

## 2.2 Failures of Existing Systems

Four categories of system currently occupy this space. Each addresses some part of the problem
and fails at a different point, and it is the specificity of those failures — rather than any general
objection to centralisation — that motivates the present design.

### 2.2.1 Centralised Cloud Infrastructure Providers

Amazon Web Services, Google Cloud Platform and Microsoft Azure rent accelerator capacity by
the hour or by the token. They solve the problem of capacity provisioning genuinely well and at
enormous scale, and nothing in this report disputes their engineering.

Their failure mode is threefold. First, price: the rate charged must recover the capital cost of the
accelerator, the cost of the facility, and the operator's margin, and that floor is high enough to
exclude precisely the independent developers and unfunded researchers who would benefit most
from access. Second, lock-in: the surrounding services on which a deployed workload comes to
depend — identity, storage, queuing, observability — are provider-specific, so that the cost of
migration grows monotonically with the length of the deployment, and the tenant's negotiating
position weakens accordingly. Third, correlated failure: capacity is concentrated in regions, and
a regional control-plane failure removes service from every tenant in that region at the same
instant. Because the alternatives are equally concentrated, no tenant possesses a fallback that is
uncorrelated with the failure. Redundancy purchased within a single provider is not
independence.

### 2.2.2 Monolithic Model API Providers

The second layer, comprising providers that expose proprietary models behind a hosted API,
inherits every failure of the layer beneath it and adds three of its own.

The first is opacity. The weights are not published, the exact model version answering a given
request is not always disclosed, and the behaviour of the endpoint may change beneath a
deployed application without notice. A system built on such an endpoint cannot be reproduced,
which is a serious defect in any research context and a compliance defect in a regulated one.

The second is policy control. The provider determines unilaterally what may be asked and what
may be answered. Whatever one's view of the merits of any particular restriction, the structural
point stands: a single organisation exercises editorial control over a general-purpose
computational facility, and an application built upon it inherits that control without recourse.

The third, and for this project the most consequential, is privacy. Obtaining an answer requires
transmitting the question. For prompts containing patient records, financial positions, privileged
legal material or unreleased designs, the transmission is itself the disclosure. Contractual
assurances about retention and training do not change the fact that the data has crossed an
administrative boundary the user does not control and cannot audit. Entire classes of application
in healthcare, law and finance are therefore foreclosed by the delivery model rather than by any
limitation of the models themselves.

### 2.2.3 Proof-of-Work Blockchain Compute Networks

Blockchain networks secured by proof of work do possess the property that this project needs —
they coordinate large quantities of independently owned hardware, without a central operator,
through purely economic incentives, and they do so robustly. That coordination mechanism is
genuine prior art and is worth taking seriously.

Their failure is what the coordinated hardware is made to do. Proof of work requires participants
to compute cryptographic hashes whose only purpose is to be difficult. The computation has no
value outside the consensus mechanism itself; the moment a block is found, every hash computed
in the attempt is discarded. An enormous quantity of electrical energy and silicon is therefore
consumed to produce an artefact that is useful only as evidence that energy was consumed. The
"proof of useful work" literature exists precisely because this is recognised as waste, and its
central question — how to make the work that secures a network be work that someone
independently wanted done — is the question this project answers in the specific case of AI
inference. Matrix multiplication for a language model is useful work by construction: a paying
requester wanted the result, independently of any consensus consideration.

### 2.2.4 Multi-Agent Routing DePINs and Consensus Fatigue

The fourth category is the closest prior art: decentralised networks that route computational
tasks among independently owned nodes, coordinating that routing through on-chain agents that
negotiate placement, load balancing and pricing among themselves.

Their failure mode is latency, and it is architectural rather than incidental. If the routing decision
for a single inference request must itself be agreed by a set of agents whose agreement is
mediated by a blockchain, then the cost of the decision is bounded below by the time required to
reach agreement — block time, propagation, confirmation, and in many designs several rounds of
negotiation before any of that begins. This accumulated coordination overhead is what the
literature terms *consensus fatigue*. Where the workload is a long-running batch job, an
overhead of several seconds is immaterial. Where the workload is a streaming conversational
response whose entire quality target is a first token in under one second, an overhead of several
seconds is not an inefficiency but a disqualification: the coordination alone exceeds the latency
budget for the whole request, and sub-second streaming becomes structurally impossible
regardless of how fast the executing node is.

The correct inference from this failure is not that decentralised routing is unworkable. It is that
consensus must be removed from the request path. In the design proposed here, the matching
decision is taken off chain, in a single bid window over a gossip mesh, and is settled
deterministically by an auction rule that requires no agreement among the bidders at all. The
chain is used only where its properties are actually required — for custody of funds, for the
commitment that binds an output, and for the adjudication of fraud — all of which occur outside
the latency-sensitive window.

## 2.3 The Gap That Remains

Each of the five capabilities required by an open inference market has been demonstrated
independently in the literature. Kademlia provides directory-free peer lookup; GossipSub
provides attack-resilient message propagation; Petals demonstrates that distributed inference
over consumer GPUs is technically feasible; the LLM-as-a-Judge line of work establishes that a
capable model can evaluate the output of another with useful agreement against human
preference; and optimistic fraud proofs establish that a single honest challenger suffices to police
off-chain state transitions within a challenge window.

What no published system does is integrate all five. Petals has no incentive layer and no Sybil
resistance, so participation is altruistic and cannot be sustained in an open setting. PolyLink,
the most directly comparable peer-reviewed system, supplies blockchain settlement and judge-
based verification but no dedicated data-availability layer and no gas-optimised micro-settlement
path. DGrid, the closest commercial system, relies on a proprietary and unaudited quality
algorithm and does not publish its runtimes. Parallax and Navigator achieve substantial
scheduling improvements but assume a fully cooperative and trusted node population, which is
exactly the assumption an open permissionless network cannot make.

## 2.4 Statement of the Problem

The problem this project addresses may therefore be stated precisely as follows.

*Given a population of independently owned, heterogeneous and mutually untrusting edge devices,
and a population of requesters who wish to obtain language-model inference from them, construct
an open network that: (i) allows a requester to discover suitable providers without any central
directory; (ii) allocates each request to a provider by a mechanism whose coordination cost does
not consume the sub-second time-to-first-token budget, and whose pricing rule makes truthful
bidding the dominant strategy; (iii) executes the request on the provider's own hardware and
streams the result directly to the requester; (iv) commits the output to a data-availability layer in a
manner that binds the provider to exactly what it produced, so that a later dispute can be resolved
against evidence rather than assertion; (v) detects fraudulent or fabricated outputs by auditing a
statistical sample of jobs, at a cost that a rational validator is willing to bear; and (vi) settles
payment on chain such that an honest provider is paid, a fraudulent provider forfeits stake, and the
detecting validator is compensated — with no participant required to trust any other, and with
every divergence between this design and its present implementation declared rather than
concealed.*

---

# Chapter 3

# OBJECTIVES

## 3.1 Preamble

The seven objectives below are those stated in the approved project synopsis. Each is restated
here in the form in which it will actually be assessed: what the objective means at the level of an
implemented subsystem, and by what measurement its achievement will be judged. An objective
that cannot be measured is a statement of intent rather than an objective, and every objective in
this chapter is therefore paired with a metric, an instrument that produces that metric, and the
run record in which the measurement is preserved. All experimental runs write to their own
timestamped directory under `docs/results/`, each carrying a full configuration snapshot and the
git commit hash of the code that produced it, so that no result can be silently overwritten or
attributed to the wrong version of the system.

## 3.2 The Seven Objectives

**Objective 1 — To design a peer-to-peer inference network using a Kademlia Distributed
Hash Table for efficient node discovery and metadata storage.**

Concretely, this requires that a node be able to join the network knowing only a bootstrap
address, publish a signed record describing itself, and be found by a requester that holds no
prior knowledge of it. The record carries the facts that change slowly — endpoint multiaddresses,
wallet address, hardware tier, and the set of models the node is willing to serve — and is stored
under a key derived from the node's own identity, so that no directory service exists to be
attacked or shut down. Because DHT records are replicated and expensive to refresh, liveness is
deliberately excluded from them and carried instead over signed UDP heartbeats at a five-second
interval, so that a stale record can never be mistaken for an available node. Achievement is
measured by a multi-process network launched on a single host in which each node resolves the
records of every other node it did not directly bootstrap from, with lookup latencies recorded per
lookup; the discovery experiment writes these to `dht_lookups.csv` within its run directory.

**Objective 2 — To implement a Hybrid Market Protocol via GossipSub mempools that replaces
traditional load balancers with a real-time auction model.**

The load balancer of a centralised service is replaced here by a public task mempool. A signed
job request is published to a GossipSub topic; every eligible node receives it and may reply on a
separate bid topic with a sealed bid stating its offered time-to-first-token, its price, and whether
the model is already resident in memory. After a fixed bid window the requester resolves the
auction under a second-price (Vickrey) procurement rule: the lowest acceptable bid wins and is
paid the price at which the runner-up would have become competitive, so that a bidder's price
determines whether it wins but never what it is paid, and truthful bidding is the dominant
strategy. Latency budget, minimum tier and price ceiling are enforced as hard eligibility
constraints, and each excluded bid is recorded with the reason for its exclusion rather than
silently dropped. Achievement is measured by the auction-convergence experiment, which runs
real multi-process networks at three, four and five nodes and records, for every auction, the time
from job publication to award, the number of bids received, the winning bid and the clearing
price. The invariant that must hold on every row is `winning bid ≤ clearing price ≤ price ceiling`;
a violation would mean the auction was not individually rational.

**Objective 3 — To develop a lightweight Python-based edge client capable of benchmarking
local hardware, managing model weights, and streaming AI outputs.**

This objective covers the node software itself. The hardware benchmark probes the machine's
accelerator, memory and core count and classifies it into Tier 1 (CPU only), Tier 2 (integrated or
low-end GPU) or Tier 3 (discrete GPU with at least sixteen gigabytes of addressable memory),
recording alongside the tier the method by which it was detected so that a tier claimed in a signed
record can be audited rather than merely believed. The inference client consumes the runtime's
streaming interface and stamps the arrival of the first chunk carrying a non-empty response as
the time to first token; token counts are taken from the runtime's own tokeniser rather than
estimated from whitespace, which would be wrong by thirty to forty per cent for English prose
and considerably worse for code. Failure paths — connection refused, unknown model, timeout,
truncated stream — raise distinct named exceptions, so that no caller can ever receive a result
that appears successful but was not produced by a model. Weight management is the third clause of
this objective: weights are obtained by content address through an IPFS daemon and the identifier is
recomputed from the bytes that arrived before they are returned, so that a node can accept weights
from a peer it does not trust, and a byte-budgeted least-recently-used cache bounds how much disk
that consumes. Achievement is measured by the
inference benchmark, which records per-trial TTFT, total duration, model load time, prompt and
completion token counts, throughput and host load into `trials.csv` and a statistical summary
into `warm_summary.json`, and by the weight-distribution run, which records per-artefact fetch
times, cache statistics, the recomputed identifier for every fetch, and the outcome of three
tampering cases against an honest control.

**Objective 4 — To integrate a Layer-2 smart contract architecture to handle decentralized
identity, escrow and micro-settlements.**

Four contracts implement the financial and identity layer. `NodeRegistry` custodies provider
stake, enforces a minimum active stake as the cost of a Sybil identity, and imposes an unbonding
period so that a provider who anticipates a fraud proof cannot escape it by withdrawing.
`Marketplace` holds each job's escrow and enforces the state machine of Figure 1.3: OPEN on funding, AWAITING_VERIFICATION once a
commitment is recorded, and thereafter SETTLED, SLASHED or REFUNDED, with every illegal
transition reverting with a typed error. `VerificationContract` accepts commitments, adjudicates
fraud proofs within the challenge window, and distributes a confirmed slash eighty per cent to the
detecting validator and twenty per cent to the treasury. `ModelRegistry` binds a model identifier
to the content hash of its weights, so that serving a cheaper quantisation than the one paid for
leaves an on-chain trace. Achievement is measured by the settlement experiment, which runs
against a live chain and records the gas consumed by every transaction type into `gas_used.json`
and a value-conservation check into `invariants.json`; the conservation check is performed in
integer wei rather than in floating point, so that it asserts exact equality rather than
approximate closeness. As stated in Table 1.1, the contracts are Solidity 0.8.24 on a local EVM
chain rather than Arbitrum Stylus in Rust; the semantics, the access control and the gas
measurements are real, and the migration to Stylus is future work.

**Objective 5 — To establish a Data Availability integration ensuring verifiable state
commitments with minimal gas overhead.**

The purpose of this layer is to make an output *binding*. After execution, the provider writes the
complete output as a namespaced blob; blobs are batched into blocks, each block commits to its
blobs through a binary Merkle tree with domain-separated leaf and internal hashes so that a leaf
can never be reinterpreted as an internal node, and only the resulting root and a blob reference
are posted on chain. A verifier can then fetch the blob, recompute its hash, and check the
inclusion proof against the root the chain already holds, without trusting the store. This is what
prevents a provider from showing the verifier one output and the chain another. It also keeps the
on-chain footprint to a fixed-size commitment irrespective of output length, which is the gas
argument. Achievement is measured by tested round-trip proof verification — including negative
cases in which a tampered blob must fail — and by the recorded blob and block counts in
`da_stats.json` for each settlement run. As stated in Table 1.1, this is a local store implementing
the Celestia interface, not Celestia. The binding property is real and tested; Celestia's
availability guarantee under a decentralised validator set with data-availability sampling is not
reproduced, and replacing the local store with a Celestia light node requires reimplementing two
functions and nothing else.

**Objective 6 — To deploy an Agentic Verification system using an LLM-as-a-Judge mechanism
to optimistically verify outputs and slash malicious nodes.**

Verification is optimistic: every job is presumed honest and settles by default once its challenge
window elapses, and only a five per cent random sample is ever audited, because auditing every
job would cost as much as performing it. The sampling decision is a keyed hash of the job
identifier, which makes the audit set deterministic — recomputable by anyone holding the epoch
seed, so a validator that skips inconvenient jobs can be caught — and simultaneously
unpredictable to a provider that does not yet hold the seed, so that no provider can cheat
selectively on the unaudited remainder. Auditing runs cheapest-first: the validator first
recomputes the blob hash and checks the Merkle proof, which yields certainty at the cost of one
hash and constitutes a fraud proof on its own, and only if that check passes does it spend a judge
call. The judge returns pass, fail, or **error**, and the error verdict is first-class: a judge outage
must never be recorded as a detection of fraud, an unparseable response must never fall back to
the passing threshold, and a pool that cannot reach quorum returns error, which upstream treats
as "do not settle yet" rather than as innocence or guilt. Achievement is measured by the
verification experiment, which generates honest outputs, corrupts a controlled proportion of them
using four independent fraud strategies — substituting an incorrect answer, negating the
assertion, hallucinating entities and numbers, and substituting an answer to a different question
— and reports precision, recall and error rate of the judge against that known ground truth,
together with a separate paraphrase experiment measuring the judge's self-consistency when the
same output is restated. The judge is an off-the-shelf model rather than one fine-tuned on
TruthfulQA and Chatbot Arena as the synopsis proposed, so the reported accuracy is a lower
bound on what the design can achieve.

**Objective 7 — To validate the system's performance by achieving sub-second time-to-first-
token latency while maintaining lower inference cost than centralised alternatives.**

This objective requires the most careful statement, because it is the one most easily overclaimed.

Time to first token is defined here as the interval between the submission of a request and the
arrival of the first streamed chunk carrying a non-empty response fragment. It is measured at the
first token and at no other point; a final chunk carrying an empty response is explicitly not
counted, since counting it would under-report latency on short generations. The claim of
sub-second TTFT applies to the **warm** case, in which the requested model is already resident in
the provider's memory. On the CPU-only development machine — sixteen logical cores,
approximately thirty-one gigabytes of RAM, no NVIDIA GPU, classified by the system's own
detector as Tier 1 — warm TTFT was measured at a median of approximately 0.68 to 0.80 seconds
across benchmark runs, with individual warm trials ranging from roughly 0.53 to 1.36 seconds.

The **cold** case is an order of magnitude worse and is reported alongside rather than omitted. When
the model must first be loaded into memory, measured TTFT rises to approximately 8.9 to 10.6
seconds — a cold-to-warm ratio of roughly 13 to 15 — of which the model load itself accounts for
the great majority. The honest form of the performance claim is therefore this: *the system achieves
sub-second time-to-first-token on warm nodes, and the cold-start penalty is approximately an order
of magnitude, which is precisely why the market protocol pays a warm-start bonus and why the
paired cold and warm figures are reported together in every latency table in this report.* Any
statement of the warm figure without the cold figure beside it would misrepresent the system.

The cost half of the objective is measured as grid cost per thousand tokens including the
amortised overhead of verification and the gas cost of settlement, compared against the published
per-token price of a centralised baseline for a comparable model. The comparison is stated as a
cost model with its assumptions declared, not as a headline multiple, because the two systems
differ in the hardware they run on and a like-for-like claim would not survive scrutiny.

## 3.3 Objective-to-Module Mapping

Table 3.1 maps each objective to the module that realises it, the location of that module in the
source tree, and the primary artefact in which its measurement is recorded.

**Table 3.1: Objectives, implementing modules and measurement artefacts**

| # | Objective | Module | Implemented in | Measurement artefact |
|---|---|---|---|---|
| 1 | Kademlia DHT peer discovery | Module 1 — Discovery | `discovery/node.py`, `discovery/heartbeat.py`, `edgegrid/identity.py` | `dht_lookups.csv`, `nodes.csv` |
| 2 | GossipSub mempool and second-price auction | Module 2 — Market protocol | `discovery/node.py`, `edgegrid/market.py` | `auctions.csv`, `bids.csv` |
| 3 | Edge client, benchmark, streaming inference | Module 3 — Inference | `inference/engine.py`, `inference/benchmark.py` | `trials.csv`, `warm_summary.json`, `hardware_profile.json` |
| 4 | Layer-2 identity, escrow and settlement | Module 5 — Settlement | `contracts/contracts/*.sol`, `edgegrid/chain.py`, `edgegrid/ledger.py` | `gas_used.json`, `invariants.json`, `settlements.csv` |
| 5 | Data availability with verifiable commitments | Module 4 — Verification | `edgegrid/da.py` | `da_stats.json`, Merkle proof tests |
| 6 | Agentic verification and slashing | Module 4 — Verification | `verification/validator.py`, `verification/evaluator.py`, `verification/fraud_injector.py` | verification run directory, paraphrase run directory |
| 7 | Sub-second warm TTFT and cost comparison | Modules 3 and 5 | `inference/engine.py`, `edgegrid/ledger.py` | `warm_summary.json`, `cold_warm_summary.json` |

The wire contract that binds these modules to one another — the nine signed message types that
constitute the protocol — is defined once, in `edgegrid/schemas.py`, and every module validates
against it, so that a change to the protocol cannot be made in one module without breaking the
others visibly rather than silently.
