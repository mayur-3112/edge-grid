# Chapter 4

# LITERATURE REVIEW

## 4.1 Introduction to the Survey

The Edge Grid draws on five distinct bodies of research that have, until now, been pursued largely
in isolation from one another: structured peer-to-peer overlay networking, blockchain settlement and
optimistic fraud proving, efficient serving of large language models on commodity hardware,
automated evaluation of language-model outputs, and the economic design of decentralised physical
infrastructure networks. A system that proposes to route an inference request through all five in a
single pipeline must be able to show that each stage rests on established work, that the seams
between the stages are the genuinely novel part, and that the claim being made is proportionate to
what the literature already contains.

This chapter surveys twenty-nine sources in support of that argument. Twenty of them constitute the
core survey carried forward from the Phase-1 literature submission; nine further sources have been
added during the preparation of this report to close two specific deficiencies in that submission,
namely the absence of any definition of the term "DePIN" itself and the absence of any of the
decentralised compute networks that are actually running in production today. The survey is
organised into the four thematic clusters used in the Phase-1 submission, so that the mapping
between the literature and the system architecture remains traceable:

1. Peer-to-peer networking and decentralised scheduling (Section 4.3);
2. Blockchain settlement, trust and fraud prevention (Section 4.4);
3. Decentralised LLM inference engines and comparable running systems (Section 4.5);
4. Agentic verification and LLM output quality (Section 4.6).

For every source the discussion follows a fixed structure: the contribution the work makes, the
specific module of The Edge Grid to which that contribution is relevant, and the limitation that
prevents the work from being adopted wholesale. The limitation is not an afterthought. In several
cases — Petals, Parallax, Navigator, Bittensor — the limitation of the prior work is precisely the
place where this project's contribution lies, and stating it accurately is what makes the research
gap in Chapter 6 defensible rather than rhetorical.

The mapping from literature to implemented module is shown in the system architecture diagram,
the architecture figure, reproduced in this report
as **Figure 4.1**. The reader is asked to keep that figure in view through Sections 4.3 to 4.6: each
box in the diagram corresponds to one or two of the works discussed below, and the arrows between
boxes correspond to the integration problem that no single cited work addresses.

**Figure 4.1** — `docs/figures/architecture.png` — The Edge Grid system architecture: five modules
and the literature that grounds each of them.

## 4.2 Method and Citation Verification

Before the survey proper, a note on method is necessary, because it materially affects what appears
below.

The reference list carried in the Phase-1 literature survey and in the accompanying presentation was
subjected to an independent verification pass during the preparation of this report. Each of the
twenty entries was checked against the Crossref DOI registry, the publisher's own record, the arXiv
abstract page, the ACL Anthology, the DBLP bibliography and, where relevant, the primary document
itself. The outcome of that pass was as follows. Six entries carried the correct paper but the wrong
venue or the wrong year. Two entries carried the wrong author list. Two entries could not be located
in any bibliographic index whatsoever and are, on the balance of evidence, not real publications. Six
of the twenty rows in the Phase-1 survey table carried paper titles that do not match the titles of
the works they cite, which is consistent with the rows having been composed from memory rather than
from the documents.

Every citation used in this chapter is the corrected form. The two entries that could not be verified
have been removed and the claims that rested on them have been re-grounded on genuine sources or, in
one case, on this project's own measurements. Specifically:

- The Phase-1 reference [5], attributed to "S. Balaji et al." and to a proof-of-useful-work paper
 said to have appeared at an IEEE blockchain conference under the auspices of the MIT Digital
 Currency Initiative, does not exist. This is the most consequential defect in the Phase-1 package,
 because that single reference was the sole citation offered for the project's entire economic
 premise: that a node should earn tokens by performing productive inference rather than by
 performing wasteful cryptographic hashing. That premise is sound and is supported by a substantial
 real literature; it is simply not supported by the reference that was given. It has been
 re-grounded here on Ball, Rosen, Sabin and Vasudevan [5a], on Fitzi, Kiayias, Panagiotakos and
 Russell [5b], and — most directly — on Jia et al.'s formalisation of proof-of-learning [5c].
- The Phase-1 reference [7], attributed to "T. Eloundou et al." and said to have appeared in
 *AI & Society*, does not exist either. Ollama has never been described in a peer-reviewed
 publication; its repository carries no citation file, and an open issue in that repository is a
 request for a recommended citation format. Every performance claim that the Phase-1 submission hung
 on this reference — quantised serving throughput, time-to-first-token, Apple Silicon behaviour —
 is therefore re-grounded in this report on the project's own instrumented measurements, recorded
 under `docs/results/`. This is the stronger position, because the project possesses measured
 numbers on identified hardware and the cited paper never did. Ollama and its underlying
 `llama.cpp` substrate are cited here as software artefacts [7a], [7b], which is what they are.

Two further sources are real but were mischaracterised in the Phase-1 submission and are labelled
correctly throughout this chapter. Parallax [14] is an unrefereed arXiv preprint, not a peer-reviewed
publication; DGrid [12] is a corporate litepaper, not a research paper. Both are the right systems to
compare against and their inclusion is retained. An argument that rests part of its weight on the
refereed standing of a comparator must state that standing accurately, or the argument fails the
first time an examiner opens the link.

PolyLink [16] is the exception, and the correction runs the other way. An earlier draft of this
chapter recorded it as an unrefereed preprint on the strength of its arXiv posting and of DBLP,
which indexes only the CoRR version. It is in fact a peer-reviewed conference paper — *Proc. 2025
IEEE International Conference on Blockchain*, pp. 101-108, doi 10.1109/Blockchain67634.2025.00023,
IEEE Xplore document 11264675 — and the arXiv posting is its preprint. The Phase-1 description of it
as the closest peer-reviewed academic parallel therefore stands. The original citation's only defect
is that "IEEE" names a publisher rather than a venue, which understates the work rather than
overstating it. The lesson is recorded here deliberately: an absence from one index is not evidence
of absence, and a verification pass can err in the direction of accusing a source as easily as in
the direction of accepting one.

Finally, nine sources have been added. The most important of these is the Morpheus network [21] and
its reference implementation, the Morpheus-Lumerin-Node [22]. This addition is not optional. The
project's own repository documentation — `README.md` and `docs/PAPER_DRAFT.md` — already names
Morpheus-Lumerin-Node as the reference implementation from which the escrow, bid, settle and slash
structure of the `contracts/` directory was patterned, and `contracts/README.md` states this
verbatim. A survey that omits the system the code was patterned on, while asserting that no such
system exists, is internally inconsistent in a way that no examiner would overlook. Sections 4.5 and
6.3 treat Morpheus as the closest prior art in existence.

## 4.3 Theme 1 — Peer-to-Peer Networking and Decentralised Scheduling

This theme supplies the substrate on which Modules 1 and 2 of The Edge Grid are built: how a node
finds other nodes without a directory server, how a job announcement reaches every eligible node
without a broker, and what is already known about scheduling machine-learning work across a
heterogeneous, geographically dispersed pool of machines.

### 4.3.1 Kademlia: XOR-Metric Distributed Hash Tables

Maymounkov and Mazières [1] introduced the Kademlia distributed hash table at the First International
Workshop on Peer-to-Peer Systems in 2002. The paper's contribution is a routing metric — the bitwise
exclusive-or of two node identifiers, interpreted as an integer — which is symmetric, unidirectional
and consistent with a binary-tree view of the identifier space. From that single choice follow the
properties that made Kademlia the dominant DHT design: a node learns useful routing information from
every query it receives, `k`-bucket routing tables refresh themselves as a side effect of ordinary
traffic rather than requiring dedicated maintenance messages, and lookup completes in O(log n) hops
with a configurable parallelism parameter that trades bandwidth against latency.

*Relevance to this project.* Kademlia is the foundation of Module 1, the Decentralised Discovery
Module. In the implementation, `discovery/node.py` instantiates a `KadDHT` in server mode over
py-libp2p 0.7.0 and stores each node's signed `NodeRecord` — endpoint, settlement address, hardware
tier — under a key derived from that node's identity. No central registry is consulted at any point
in the job pipeline. The XOR-metric lookup is what allows a requester joining the network with a
single bootstrap peer to reach an arbitrary provider record.

*Limitation.* Kademlia is a storage and routing structure, not a trust structure. The 2002 paper
assumes that node identifiers are drawn honestly and does not defend against an adversary who
manufactures identifiers to position itself adjacent to a target key; the eclipse and Sybil attacks
against Kademlia-family DHTs are well documented in the subsequent literature. Kademlia is also
poorly matched to state that changes on a timescale shorter than the republish interval — a node's
current memory headroom, thermal state, or whether a given model is resident. The Edge Grid addresses
the first limitation with cryptographic identity and economic staking (Sections 4.4.4 and 4.4.5) and
the second with a dedicated UDP heartbeat channel at a five-second interval, implemented in
`discovery/heartbeat.py`, which deliberately decouples fast-moving health signals from the slower DHT
record.

### 4.3.2 GossipSub: Attack-Resilient Publish-Subscribe

Vyzovitis, Napora, McCormick, Dias and Psaras [2] specify GossipSub, the publish-subscribe protocol
that carries block and transaction propagation in the Filecoin and Ethereum 2.0 networks. The design
combines an eagerly maintained mesh of full-message peers with a lazily gossiped index of message
identifiers, so that a node receives each message once over the mesh and can repair omissions on
demand from the gossip. The v1.1 revision, which is the substance of the report, adds a peer-scoring
function combining time-in-mesh, message delivery rate, mesh-message delivery failures and invalid
message counts, together with adaptive mesh grafting and pruning driven by that score. The stated
purpose is resistance to specific adversarial behaviours: cold-boot attacks, covert flash attacks,
sybil-amplified censorship and eclipse attacks.

This document is a Protocol Labs technical report, released as arXiv:2007.02754 in July 2020. It has
not been published at a refereed venue; the Phase-1 attribution to a 2022 IEEE Peer-to-Peer Computing
conference is incorrect, and that conference series in fact concluded in 2015.

*Relevance to this project.* GossipSub is Module 2, the Hybrid Market Protocol. In `discovery/node.py`
the node joins a GossipSub mesh over the market topics and publishes signed job announcements and
sealed bids onto it. There is no auctioneer process and no message broker. The peer-scoring machinery
described in [2] is directly relevant to the threat that most obviously afflicts an open bid mempool,
namely an adversary flooding it with bids in order to displace honest ones.

*Limitation.* GossipSub's guarantees are about message propagation, not about message meaning. It
will faithfully and resiliently deliver a bid that a provider has no intention of honouring. Nothing
in the protocol relates a peer's score to the economic truthfulness of its payload, and the peer
scoring parameters must be calibrated per deployment — mis-calibration penalises honest
low-bandwidth peers, which for a network explicitly targeting consumer hardware in
bandwidth-constrained regions is a real cost. The Edge Grid therefore treats GossipSub as a transport
and places the incentive-compatibility burden on the auction rule itself (`edgegrid/market.py`) and
the settlement contract, not on the pub-sub layer.

### 4.3.3 Petals: Collaborative Inference over Consumer Hardware

Borzunov, Baranchuk, Dettmers, Ryabinin, Belkada, Chumachenko, Samygin and Raffel [10] present
Petals, a system in which volunteers each host a contiguous block of transformer layers and a client
stitches a pipeline across them to run inference and parameter-efficient fine-tuning on models too
large for any single participating device. The system demonstrated interactive-rate generation for a
176-billion-parameter model over ordinary consumer internet connections. The correct citation is the
ACL 2023 System Demonstrations track; a distinct and more detailed paper by substantially the same
authors appeared at NeurIPS 2023 under a different title, and the Phase-1 reference appears to have
merged the two.

*Relevance to this project.* Petals is the strongest existence proof available that distributed LLM
inference over volunteer consumer hardware is technically feasible at useful speed. It is the prior
work that removes feasibility from the list of open questions and allows this project to concentrate
on the economic and verification questions instead.

*Limitation.* Petals is a cooperative system by design. It has no economic incentive, no notion of
payment, no stake, no Sybil resistance and no settlement layer; participation is altruistic, and the
authors are explicit that the swarm's composition is therefore outside the system's control. A
participant that returns plausible but incorrect activations is not detected and not punished. These
three absences — incentives, Sybil resistance, settlement — are exactly the three additions that
Modules 2, 4 and 5 of The Edge Grid make, and the accuracy of the comparison in Chapter 5 rests on
stating them precisely.

### 4.3.4 Parallax: Scheduling for Heterogeneous Decentralised Serving

Tong, Jiang, Chen, Zhao, Lu, Qu, Yang, Ai and Yuan [14] present Parallax, a two-phase scheduler for
serving large language models across a decentralised pool of heterogeneous GPUs. The first phase
allocates model layers to devices under memory and bandwidth constraints; the second selects, at
request time, a pipeline through the available replicas. The reported gains over decentralised
baselines are a 3.1x reduction in latency and a 5.3x increase in throughput.

*Relevance to this project.* Parallax establishes the quantitative bar that a decentralised
scheduling mechanism should be measured against, and its two-phase separation of a slow allocation
decision from a fast request-time selection decision is structurally the same separation that The
Edge Grid makes between the DHT record (slow, static capability) and the GossipSub bid (fast,
per-request availability and price).

*Limitation.* Parallax is a preprint (arXiv:2509.26182, September 2025) and has not been refereed;
this report cites it as such. More substantively, Parallax optimises over a pool of cooperative
volunteer nodes. It contains no incentive layer, no payment, and no adversary model: a node that
under-reports its latency in order to attract work, or that returns garbage in order to be paid
without computing, is outside the scope of the design. Its performance figures are therefore an upper
reference point achieved under assumptions The Edge Grid explicitly refuses to make.

### 4.3.5 Navigator: Decentralised Scheduling for Latency-Sensitive Workflows

Yang, Merlina, Song, Yuan, Birman and Vitenberg [15], of Cornell University and the University of
Oslo, present Navigator at IEEE EDGE 2024. Navigator models an AI workflow as a data-flow graph and
schedules its tasks across edge nodes without a central coordinator, combining GPU cache-awareness
with dependency-aware placement; the reported end-to-end latency improvement over centralised
baselines is a factor of two to six.

*Relevance to this project.* Navigator is the formal justification for the single most
counter-intuitive design decision in The Edge Grid: that removing the central load balancer need not
cost latency. If decentralised placement were inherently slower, the entire premise of a
market-scheduled inference network would be unsound. Navigator shows it is not.

*Limitation.* Navigator assumes a cooperative, trusted, rack-scale deployment. It addresses neither
adversarial nodes, nor economic incentives, nor open permissionless registration. Its result
therefore transfers to The Edge Grid as a statement about achievable latency, not as a statement
about achievable latency in an open market — a distinction Chapter 5 preserves.

## 4.4 Theme 2 — Blockchain Settlement, Trust and Fraud Prevention

This theme supplies Modules 4 and 5: how a payment is held and released, how a claim about an
off-chain computation is committed so that it cannot later be repudiated, how a fraudulent claim is
challenged, and what it costs an adversary to manufacture identities.

### 4.4.1 Arbitrum Nitro: Second-Generation Optimistic Rollup

The Arbitrum Nitro whitepaper [3] — authored by sixteen contributors at Offchain Labs and published
in August 2022, not, as the Phase-1 reference stated, by Kalodner et al. at an IEEE conference —
describes the architecture that has run Arbitrum One since 31 August 2022. Its contributions are the
compilation of the reference execution environment to WebAssembly so that the same Geth core serves
both ordinary execution and dispute resolution, a multi-round interactive fraud proof that bisects a
disputed execution down to a single WASM instruction adjudicated on Ethereum, and calldata
compression that substantially reduces the dominant cost component of a rollup transaction.

*Relevance to this project.* Nitro is the design rationale for placing settlement on an optimistic
rollup rather than on Ethereum mainnet. A network that settles one payment per inference request
cannot afford mainnet calldata costs; the order-of-magnitude reduction Nitro provides is what makes
per-job micro-settlement arguable at all. The interactive fraud proof is also the conceptual
ancestor of the challenge window in `contracts/VerificationContract.sol`.

*Limitation, and a scope declaration.* Two limitations must be stated. First, in the protocol itself:
optimistic rollups impose a multi-day dispute window on withdrawals to the base layer, which is
tolerable for a node operator's accumulated earnings but not for instant liquidity. Second, and more
important for the honesty of this report: **The Edge Grid does not use Arbitrum Stylus.** The Phase-1
design specified settlement contracts written in Rust and compiled to WebAssembly for the Stylus
environment. The implementation contains no Stylus code. The contracts in `contracts/contracts/` are
plain Solidity 0.8.24 — `NodeRegistry.sol`, `Marketplace.sol`, `VerificationContract.sol` and
`ModelRegistry.sol` — compiled with Hardhat and deployed to a local EVM chain with chain ID 31337, as
recorded in `contracts/deployment.json`. Reference [3] therefore appears in this report as design
rationale for a settlement architecture, and must not be read as a citation for a deployed
dependency. Section 5.5 states this divergence in tabular form.

### 4.4.2 Fraud and Data Availability Proofs

Al-Bassam, Sonnino and Buterin [4] address the problem that gives optimistic systems their name: a
light client can only rely on a fraud proof if the data needed to construct that proof is actually
available. The paper introduces erasure-coded block commitments with probabilistic data-availability
sampling, so that a light client making a small number of random sample requests obtains a high
probability guarantee that the whole block is recoverable, and it formalises the resulting security
model for light clients under a dishonest majority. It is a preprint (arXiv:1809.09044, September
2018, revised May 2019) from University College London and the Ethereum Foundation; the Phase-1
attribution to ACM CCS 2023 and to Protocol Labs is incorrect on both counts.

*Relevance to this project.* This is the formal foundation for the data-availability layer in Module
4. The property the layer must supply is binding: a provider must not be able to show a verifier one
output while the chain records a commitment to a different one.

*Limitation, and a second scope declaration.* **Celestia is not integrated into The Edge Grid.** The
data-availability layer is `edgegrid/da.py`, a local namespaced append-only blob store that batches
blobs into blocks, commits each block through a binary Merkle tree with domain-separated leaf and
internal-node hashing for second-preimage safety, and issues inclusion proofs that any verifier can
check without trusting the store. Those Merkle proofs are real and are covered by the test suite. The
binding property described above is therefore genuinely obtained. What is *not* obtained is
Celestia's actual guarantee — availability of the data to any party, enforced by a decentralised
validator set performing data-availability sampling — because there is no validator set. The module's
own docstring states this, and the report states it here rather than allowing a reader to discover
it. The interface is deliberately narrow, so substituting a Celestia light client means reimplementing
`submit_blob` and `get_blob` and nothing else.

### 4.4.3 Optimistic Fraud Proofs and the Single-Honest-Verifier Result

Teutsch and Reitwießner [20] formalise, in the TrueBit design, an interactive verification game
between a solver who claims a result for an off-chain computation and a challenger who disputes it.
Successive bisection of the disputed execution trace localises the disagreement to a single
computational step, which the chain can adjudicate cheaply. The central result is that a single
honest and attentive verifier is sufficient: the protocol's security does not require an honest
majority of verifiers, only one. The document was released as a whitepaper in November 2017 and later
published by World Scientific in the Lecture Notes Series of the Institute for Mathematical Sciences,
NUS, in 2023; the Phase-1 attribution to "Springer Cryptography, 2022" is incorrect, and the
subtitle it carries is not part of the title.

*Relevance to this project.* This is the theoretical warrant for the most economically important
design choice in Module 4. Auditing every inference output with a judge model would cost more than
the inference itself and would destroy the network's cost advantage. The single-honest-verifier
result is what permits a small validator pool sampling a small fraction of jobs to be sound, provided
the economic incentives keep at least one honest validator attentive. `verification/validator.py`
implements exactly this: `should_audit` admits a job into the audited sample with probability
`C.SAMPLE_RATE`, which is set to 0.05 in `edgegrid/config.py`, per the Phase-1 design.

*Limitation.* The guarantee is conditional on validator liveness. If no honest validator is watching
during a challenge window, the window closes and the fraudulent claim finalises. The result converts
a security problem into an incentive-design problem; it does not eliminate it. The Edge Grid's
response — an eighty per cent share of the slashed stake paid to the detecting validator, encoded as
`VALIDATOR_SLASH_BPS = 8_000` in `contracts/contracts/NodeRegistry.sol` — is a direct attempt to
price validator attention, and its adequacy is an empirical question this project does not claim to
have settled.

### 4.4.4 FLock: Blockchain-Enforced Peer Review and Slashing

Cheng, Sun, Sun and Guo [18] present FLock, a decentralised framework for collaborative fine-tuning
of large language models in which the central aggregator of conventional federated learning is
replaced by an on-chain trust layer: participants submit updates, peers review them, and smart
contracts automatically slash malicious contributors while rewarding honest reviewers. The paper's
headline empirical result is a reduction of more than sixty-eight per cent in adversarial attack
success rate attributable to that blockchain-enforced review-and-slash mechanism. That figure is
genuine and appears in the paper's abstract and body. The Phase-1 attribution to "Y. Chen et al." is
incorrect — there is no author of that name on the paper — and the subtitle given in the Phase-1
reference is not part of the real title.

*Relevance to this project.* FLock is the strongest quantitative evidence available that coupling
peer review to on-chain economic penalty measurably suppresses adversarial behaviour, as opposed to
merely being expected to. It is the empirical support for the reward-and-slash structure in
`contracts/contracts/VerificationContract.sol` and `NodeRegistry.sol`.

*Limitation.* FLock's threat model is gradient poisoning during fine-tuning, not fraudulent output
submission during inference. The economics differ: a poisoning attack aims to corrupt a shared
artefact, whereas an inference free-rider aims to collect payment without spending compute, and the
two have different payoff structures and different detection signatures. The sixty-eight per cent
figure therefore transfers as an indication of the mechanism's efficacy in kind, not as a predicted
value for this system, and this report does not present it as one.

### 4.4.5 The Sybil Attack

Douceur [19] proves, in the same 2002 IPTPS volume as Kademlia, the result that constrains every
permissionless network: without a trusted certifying authority, and given that computational,
storage and bandwidth resources are not uniformly bounded across entities, a single faulty entity can
present an arbitrarily large number of distinct identities, and no purely local validation by honest
peers can prevent this except under implausibly strong resource assumptions.

*Relevance to this project.* Douceur's result is why identity in The Edge Grid is cryptographic and
why registration is staked. `edgegrid/identity.py` gives each node a single secp256k1 keypair that
simultaneously yields its libp2p peer identity, its Ethereum settlement address and its message
signing capability, so that a node's network presence and its economic exposure cannot be separated.
`contracts/contracts/NodeRegistry.sol` then requires a minimum stake at registration — set to ten
units in the deployed configuration — which converts identity manufacture from a free operation into
a capital-bounded one.

*Limitation, and a correction.* Two points must be recorded. First, the honest limitation: staking
does not defeat a Sybil adversary, it only prices the attack, and pure stake-weighting concentrates
influence among wealthy operators. Second, a correction to the Phase-1 submission: its survey table
claims that this paper "evaluates proof-of-work, economic staking, and hardware attestation" as
resistance mechanisms. It does not. The paper predates blockchain economic staking by six years and
discusses none of it. The anachronism is a larger exposure than the wrong date, because an examiner
who opens the paper looking for an analysis of staking will find nothing. The correct statement, used
throughout this report, is that Douceur establishes the impossibility result that motivates economic
staking; the staking mechanism itself is grounded on later work.

### 4.4.6 Useful Work and Proof-of-Learning

The Phase-1 submission grounded the entire economic premise of the project on a single reference that
does not exist. The premise itself — that a decentralised network should reward computation that
produces value to someone rather than computation whose only product is a hash below a threshold — is
supported by three genuine lines of work, which this report cites in its place.

Ball, Rosen, Sabin and Vasudevan [5a] construct proofs of useful work from problems of independent
computational interest, establishing that the moderately-hard function at the heart of a proof-of-work
scheme need not be arbitrary and can be tied to a delegated computation whose result the verifier
actually wants. Fitzi, Kiayias, Panagiotakos and Russell [5b] extend this from a primitive to a
protocol: Ofelimos, presented at CRYPTO 2022, is a provably secure blockchain protocol whose
consensus mechanism performs a combinatorial optimisation search, with the security argument carried
through in the standard model rather than asserted.

Most directly relevant is Jia, Yaghini, Choquette-Choo, Dullerud, Thudi, Chandrasekaran and Papernot
[5c], presented at IEEE Symposium on Security and Privacy 2021, which formalises proof-of-learning:
a mechanism by which a party can prove that it actually performed a particular machine-learning
computation, using intermediate training artefacts as a certificate that a verifier can spot-check at
a small fraction of the original cost.

*Relevance to this project.* [5a] and [5b] supply the theoretical warrant for the token model: reward
follows useful computation. [5c] is the closest genuine academic antecedent to Module 4, because it
states the problem The Edge Grid's Agentic Verification Module solves in a different way — proving
that a claimed machine-learning computation was in fact carried out — and it establishes the
verification-cost asymmetry (cheap spot-checks against expensive original work) on which the five per
cent sampling design depends.

*Limitation.* All three works are about computations with a checkable structure: a search whose
solution can be validated, or a training run whose intermediate states can be replayed. Autoregressive
LLM inference has no such structure. Sampling temperature, kernel non-determinism and hardware
variation mean that re-executing the same prompt on the same weights need not reproduce the same
tokens, so bitwise replay is not a usable check. This is precisely why The Edge Grid falls back on
semantic judgement of the output (Section 4.6) rather than on replay, and it is a limitation of the
whole approach, not merely of the cited works.

## 4.5 Theme 3 — Decentralised LLM Inference Engines and Comparable Systems

This theme covers Module 3 and the systems against which the project must be compared. It is the
theme most substantially revised relative to the Phase-1 submission, because that submission compared
the project only against research prototypes and omitted every decentralised compute network in
production.

### 4.5.1 Defining DePIN

The Phase-1 submission uses "DePIN" as its central organising term without ever citing a definition
of it. Two additions close that gap. Lin, Wang, Shi, Zhang and Cao [28] survey decentralised physical
infrastructure networks, giving a five-layer reference architecture, the design principles that
distinguish a DePIN from a conventional cloud marketplace, and a market analysis covering the
compute, storage, wireless and sensor categories — including the Render and io.net class of GPU
aggregation networks that the Phase-1 survey did not mention. Andrew and Ballandies [29] supply a
decision tree with explicit classification criteria: a three-sided market, token incentives directed
at the supply side, and the placement of physical assets by independent participants.

*Relevance to this project.* [29] in particular allows this report to *argue* that The Edge Grid
qualifies as a DePIN — independent operators contribute physical machines, are compensated in a
network token, and serve a distinct demand side — rather than simply asserting the label in the
title. Both are preprints and are cited as such.

### 4.5.2 vLLM and PagedAttention

Kwon, Li, Zhuang, Sheng, Zheng, Yu, Gonzalez, Zhang and Stoica [6] introduce PagedAttention at ACM
SOSP 2023. The insight is to manage the key-value cache of a serving LLM with the techniques
operating systems use for virtual memory: non-contiguous fixed-size blocks, an indirection table, and
copy-on-write sharing between sequences that share a prefix. Internal and external fragmentation of
the KV cache, which had been the dominant waste in LLM serving, falls to near zero, and the resulting
system, vLLM, achieves a large throughput improvement over prior serving stacks at equal latency.

*Relevance to this project.* PagedAttention is the reason a discrete-GPU tier is economically
meaningful in the node classifier: it is what makes a single consumer card serving a quantised
seven-to-thirteen-billion-parameter model competitive on throughput.

*Limitation, and a third scope declaration.* vLLM requires a CUDA-capable NVIDIA GPU. **The
development hardware for this project has no NVIDIA GPU.** The hardware profile recorded with every
benchmark run — for example
`docs/results/inference-benchmark-20260902T103043Z/hardware_profile.json` — reports tier 1, CPU,
`vram_gb: 0.0`, `accelerator: none`, on an x86-64 Linux host with sixteen logical cores and
approximately 31 GB of RAM. vLLM is therefore explicitly out of scope for the implementation, and
reference [6] supports design rationale for a future CUDA node tier only. It is not a citation for a
built component, and Chapter 5 does not credit the project with one.

### 4.5.3 Ollama and llama.cpp as the Implemented Runtime

Ollama [7a] and the `llama.cpp` inference substrate beneath it [7b] are cited in this report as
software artefacts, because that is what they are: neither has been described in a peer-reviewed
publication, and the Phase-1 reference asserting otherwise has been withdrawn (Section 4.2).
`llama.cpp` provides quantised CPU and mixed CPU-GPU execution of transformer models in the GGUF
format; Ollama provides model management, a streaming HTTP API and cross-platform packaging above it.

*Relevance to this project.* Ollama is the runtime The Edge Grid actually uses. `inference/engine.py`
consumes Ollama's newline-delimited JSON stream and timestamps the arrival of the first token, so
that the reported time-to-first-token is a measurement of the first token and not, as in an earlier
non-streaming implementation, a measurement of the entire generation. Token counts are read from the
server's own `eval_count` and `prompt_eval_count` fields rather than estimated. Every performance
claim in this report is therefore supported by the project's own instrumented runs rather than by a
citation.

Those runs are recorded under `docs/results/`. On the CPU-only development host, with
`qwen3-vl:2b-instruct`, warm-path time-to-first-token means across recorded runs lie between
approximately 678 ms and 992 ms, with medians in the 700–1,050 ms band and individual warm minima as
low as 532 ms. Paired cold-versus-warm runs give cold-start means of approximately 8,913 ms and
10,576 ms against warm means of approximately 678 ms and 712 ms in the same runs, a cold-over-warm
ratio of 13.2 and 14.9 respectively. The magnitude of that ratio is itself an architectural finding
and is what justifies the warm-start bid bonus in the market protocol (Section 4.3.2 and
`edgegrid/market.py`, `WARM_START_BONUS = 0.15`).

*Limitation.* A CPU-only host is not the hardware class the network targets, and sustained
throughput on it is modest — under ten tokens per second in the recorded runs. The measurements
establish that the pipeline works end to end and that the cold-start penalty dominates, but they do
not establish competitive serving economics, and this report does not claim they do.

### 4.5.4 IPFS: Content-Addressed Storage

Benet [13] describes IPFS, a content-addressed peer-to-peer file system combining a Merkle DAG object
model, DHT-based content routing and a block exchange protocol. The relevant property for this
project is that an object's name is the hash of its content, so a retrieval either yields exactly the
bytes named or fails.

*Relevance to this project.* The Phase-1 design specifies IPFS as the store for quantised model
weights, with content hashes registered in the `ModelRegistry` contract. The property being bought is
not storage but integrity: a provider cannot silently substitute a smaller or differently quantised
variant of a model for the one the job specified, because the requester names the artefact by hash.
`contracts/contracts/ModelRegistry.sol` implements the on-chain half of this.

*Limitation, and a scope note.* IPFS availability depends on active pinning; without a storage
incentive layer, an unpopular object with no pinning peer becomes unretrievable. The Phase-1 citation
also claims publication as "arXiv + Springer, 2021"; it is a July 2014 draft-3 preprint and Springer
never published it. Finally, the implementation does not use IPFS: model artefacts and output blobs
are held by the local namespaced store in `edgegrid/da.py`. The content-addressing property is
preserved — blob identifiers are SHA-256 commitments — but the distribution network is not.

### 4.5.5 PolyLink

Liu, Cao, Yang, Bai, Cao, Shen, Zhang, Liang, Jiang and Zhang [16] present PolyLink, a
blockchain-based decentralised platform for LLM inference on edge devices. Its verification component,
TIQE, combines a lightweight cross-encoder with an LLM-as-a-Judge stage, and it couples this to a
token-based dynamic pricing and reward mechanism, evaluated across a geographically distributed
deployment of heterogeneous edge devices.

*Relevance to this project.* PolyLink is the closest architectural parallel to The Edge Grid in the
academic literature, sharing three of the five pillars: blockchain settlement, judge-based
verification and token incentives for operators. Its geo-distributed evaluation is the most relevant
empirical precedent for the pipeline this project builds.

*Standing, and limitations.* PolyLink is a peer-reviewed conference paper — *Proc. 2025 IEEE
International Conference on Blockchain*, pp. 101-108 — of which arXiv:2510.02395 (October 2025) is
the preprint. The Phase-1 description of it as "the closest peer-reviewed academic parallel" is
accurate and is retained; the research-gap argument may rest on its refereed standing.
Substantively, PolyLink does not employ a dedicated data-availability layer, does not optimise
settlement for a Layer-2 micro-payment regime, and does not use a gossip-based sealed-bid auction for
matching.

### 4.5.6 DGrid AI

The DGrid litepaper [12] describes a three-tier decentralised AI network — a routing and verification
network, a free market for models and agents, and a DAO governance layer — with a Proof-of-Quality
mechanism intended to make inference trustworthiness verifiable on chain.

*Relevance to this project.* DGrid is the most architecturally similar commercial system, and its
Proof-of-Quality mechanism is concrete prior art for the Agentic Verification Module.

*Limitation.* This is a corporate litepaper of June 2025, not a refereed publication. The
Proof-of-Quality algorithm is described but not specified in a way that would permit reproduction,
carries no security proof, and has not been independently audited. It is cited here as evidence that
a commercial market for verified decentralised inference exists, not as a technical result.

### 4.5.7 Running Networks the Phase-1 Survey Omitted

The Phase-1 survey compared The Edge Grid against seven research prototypes and zero production
networks. This is the single largest weakness in its research-gap argument, and this section repairs
it.

**Morpheus and the Morpheus-Lumerin-Node [21], [22].** The Morpheus whitepaper describes a network
for powering smart agents in which compute providers post bids that are matched by smart contracts,
with settlement on Arbitrum Layer 2. The Morpheus-Lumerin-Node is its reference implementation,
comprising a proxy-router and a contract suite structured around escrow, bid, settle and slash. This
is the closest prior art in existence to The Edge Grid's Module 5, because it is not merely similar
in spirit — it is the specific combination of Arbitrum L2 settlement plus provider bidding for LLM
inference that the Phase-1 submission claimed as novel. Its inclusion here is mandatory for a further
reason: `contracts/README.md` in this project's own repository names Morpheus-Lumerin-Node as the
pattern from which the escrow-to-settle-to-slash contract structure was drawn, and `README.md` and
`docs/PAPER_DRAFT.md` repeat that attribution. *Limitation:* the Morpheus whitepaper is pseudonymous
and unrefereed, and neither document reports a controlled evaluation of verification accuracy or
settlement cost; it establishes the mechanism's existence in production, not its measured properties.

**Bittensor [23], [24].** The Opentensor Foundation whitepaper [24] describes Bittensor as a
peer-to-peer intelligence market in which participants are rewarded according to peer-assessed
informational value; it is the largest live decentralised-AI network by market participation. The
arXiv version of that whitepaper has been withdrawn by its authors, so this report cites the official
document. More important is the peer-reviewed empirical analysis by Lui and Sun [23], presented at
MARBLE 2025 and published by Springer, which examines the network's reward distribution and documents
that emissions are driven overwhelmingly by stake rather than by the quality of the output produced.
*Relevance to this project.* This is the most strategically valuable single reference added to the
survey. It converts the argument for verification-linked payment from a speculative improvement into
a response to a measured failure mode in the largest deployed system of this kind. Chapter 6 uses it
in exactly that way. *Limitation:* Bittensor's subnet mechanism and emission schedule have changed
substantially since the whitepaper, so [24] should be read as the design intent and [23] as the
empirical record.

**Akash [25].** Osuri and Bozanich describe the Akash network's token and mining economics. Akash is
a live decentralised compute marketplace that settles real workloads and matches demand to supply
through a reverse auction in which providers bid down. *Relevance:* this is direct prior art for the
market structure in `edgegrid/market.py`, which is likewise a procurement auction in which the
lowest acceptable bid wins. *Limitation:* Akash is a general container-hosting marketplace. It
performs no verification of workload output whatsoever — the tenant simply receives the container's
result — so it supplies no precedent for Module 4.

**Golem [26].** The Golem crowdfunding whitepaper of 2016 is the earliest sustained attempt at a
peer-to-peer market for renting idle consumer compute, and the origin of the requestor-provider
vocabulary that this class of system still uses. *Relevance:* it establishes that the core idea long
predates the current DePIN wave, which is a useful corrective to any claim of conceptual novelty.
*Limitation:* it predates the LLM era entirely and its verification proposals were not realised at
scale.

**Gensyn [27].** The Gensyn litepaper describes a protocol for verifiable machine-learning compute in
which the worker emits a certificate of work assembled from training metadata, allowing a verifier to
replicate only a small number of key stages rather than the whole task. *Relevance:* it is direct
prior art for the verification-cost asymmetry that Module 4 depends on, approached from the
probabilistic-replication side rather than the semantic-judgement side. *Limitation:* the cited
litepaper is explicitly labelled out of date by its own publisher, Gensyn having since replaced its
Substrate-based Layer 1 with a custom Ethereum rollup; and the certificate-of-work approach applies
to training, where intermediate state is checkable, rather than to autoregressive inference.

**The Render and io.net class [28].** GPU aggregation marketplaces that pool consumer and data-centre
accelerators and settle in a network token are surveyed in [28]. No peer-reviewed characterisation of
io.net specifically was located during the verification pass; the row for it in Chapter 5 therefore
rests on [28] and on public project documentation, and is labelled accordingly. *Relevance:* these
networks demonstrate that supply-side aggregation at scale is achievable. *Limitation:* they are
capacity marketplaces rather than verified-inference marketplaces, and none couples payment to an
assessment of output quality.

## 4.6 Theme 4 — Agentic Verification and LLM Output Quality

This theme supplies the mechanism of Module 4: how the correctness of a generated answer can be
assessed automatically, at low cost, without a ground-truth reference.

### 4.6.1 LLM-as-a-Judge

Zheng, Chiang, Sheng, Zhuang, Wu, Zhuang, Lin, Li, Li, Xing, Zhang, Gonzalez and Stoica [8], in the
NeurIPS 2023 Datasets and Benchmarks track, study the use of a strong language model as an automated
judge of other models' outputs. Their contribution is threefold: the MT-Bench multi-turn benchmark,
the demonstration that a capable judge agrees with human preference at a rate comparable to the
agreement between two human annotators, and a careful catalogue of the failure modes of the approach
— position bias, verbosity bias, self-enhancement bias, and weakness on tasks requiring reasoning the
judge itself cannot perform.

*Relevance to this project.* This is the direct academic source for the Agentic Verification Module.
`verification/evaluator.py` implements a judge that scores an answer on a bounded rubric and maps the
score to a verdict through a configurable threshold (`PASS_THRESHOLD = 3`). One implementation detail
is worth recording because it is a correction of an earlier design: the evaluator returns three
outcomes, not two. `VerdictKind.PASS`, `VerdictKind.FAIL` and `VerdictKind.ERROR` are distinct, so
that a backend failure or an unparseable judge response is recorded as an error rather than silently
defaulting to a passing score. The earlier implementation fell back to a score of three on an
unparseable response, which is exactly the pass threshold, and therefore converted every judge
malfunction into an undeserved acquittal. The contract mirrors this: in
`contracts/contracts/VerificationContract.sol`, only a FAIL verdict slashes, while PASS and ERROR are
recorded and leave the escrow to settle normally.

*Limitation.* The biases catalogued in [8] are consequential when a judge's verdict is attached to
money. A false positive slashes an honest operator's stake, which is a far more damaging error than a
false negative that merely lets one bad answer through. The published mitigations — multiple
independent judges, position swapping, reference-guided grading — all increase verification cost, and
the trade-off between verification cost and false-positive rate is an open design question that this
project has measured but not resolved. The harness in `verification/run_harness.py` records
precision, recall and F1 against injected fraud so that the trade-off is at least visible.

### 4.6.2 TruthfulQA

Lin, Hilton and Evans [9], at ACL 2022, introduce TruthfulQA, a benchmark of 817 questions
constructed adversarially so that a model reproducing common human misconceptions will answer them
falsely. The benchmark ships both truthful reference answers and known false answers, and the paper
proposes an automated judge fine-tuned to score truthfulness at scale.

*Relevance to this project.* TruthfulQA is the evaluation substrate for Module 4.
`verification/truthfulqa_loader.py` samples questions together with their correct and known-incorrect
answers, and `verification/fraud_injector.py` uses those pairs to construct adversarial cases under
four named strategies — `swap_incorrect`, `negate`, `hallucinate_entity` and `random_topic` — so that
judge precision and recall can be measured against a known label rather than estimated. The recorded
run at `docs/results/verification-20260902T102134Z/` gives, over eighty injected-fraud items and
twenty honest items with a single validator and a non-fine-tuned local judge, an overall recall of
0.975 and an overall precision of 0.839.

*Limitation.* TruthfulQA targets factual falsehood arising from imitative learning. It does not cover
the failure mode that matters most economically in a paid inference market: an answer that is truthful
but truncated, low-effort, or produced by a smaller model than the one the requester paid for. The
per-strategy precision figures in the same run — approximately 0.57 against a shared honest control
set — show that the judge over-flags at this scale, and this report reports that number rather than
only the favourable aggregate.

### 4.6.3 Chatbot Arena

Chiang, Zheng, Sheng, Angelopoulos, Li, Li, Zhu, Zhang, Jordan, Gonzalez and Stoica [11] present
Chatbot Arena at ICML 2024 — not, as the Phase-1 reference states, at ICLR — an open platform
collecting crowdsourced pairwise human preferences between anonymised models, with a statistical
rating model derived from the Bradley-Terry framework and an accompanying dataset of large-scale
real-world preference comparisons.

*Relevance to this project.* Two contributions transfer. The preference data is the natural training
substrate for a judge that must assess response quality rather than mere factuality; and the
Elo-style rating methodology is the model for the node reputation design, in which an operator's
standing is a statistic accumulated over many pairwise-comparable outcomes rather than a single
score.

*Limitation.* Human preference and factual correctness are not the same quantity, and a judge trained
purely on preference will reward fluency and length. A verification module that slashes on the basis
of such a judge would penalise terse correct answers. The design consequence adopted here is that
truthfulness scoring and quality scoring must remain separable signals, and that only the former is
permitted to trigger a slash.

### 4.6.4 Nesa: Two-Tier Verification for Decentralised Inference

Zhang, Zhao, Angione, Yang, Buban, Farhan, Johnston and Colangelo [17], in the NeurIPS 2024 RBFM
workshop, propose a layered security framework for decentralised inference combining zero-knowledge
proofs of inference for critical workloads, consensus-based verification by sampled nodes for general
workloads, split learning for model confidentiality, and trusted execution environments as an
orthogonal hardware layer.

*Relevance to this project.* Nesa supplies the two-tier philosophy that The Edge Grid adopts:
optimistic, sampled, semantic verification as the default path, with cryptographic verification
reserved for a future high-value tier. The correspondence to this project's architecture is direct —
Nesa's consensus-based verification maps onto the validator pool in `verification/validator.py`, and
its zero-knowledge path maps onto the zkML item on the future-work roadmap rather than onto anything
implemented.

*Limitation.* The authors themselves record that zero-knowledge verification of inference carries
prohibitive computational overhead for real-time serving, which is why they reserve it. That
limitation is inherited wholesale by this project and is the reason Chapter 6 does not claim
cryptographic verification as a contribution.

## 4.7 Consolidated Summary of the Survey

Table 4.1 consolidates the survey. Sources are grouped by theme; the module column refers to the five
modules of Figure 4.1; the standing column is recorded because Chapter 5's comparison depends on it.

**Table 4.1** — Surveyed literature, module mapping and bibliographic standing

| # | Source | Theme | Module | Standing |
|---|---|---|---|---|
| [1] | Kademlia (Maymounkov & Mazières, IPTPS 2002) | 1 | M1 Discovery | Refereed workshop, LNCS |
| [2] | GossipSub (Vyzovitis et al., 2020) | 1 | M2 Market | Technical report / preprint |
| [10] | Petals (Borzunov et al., ACL 2023 Demos) | 1 | Prior art | Refereed (demo track) |
| [14] | Parallax (Tong et al., 2025) | 1 | M2 Market | Preprint, not refereed |
| [15] | Navigator (Yang et al., IEEE EDGE 2024) | 1 | M2 Market | Refereed conference |
| [3] | Arbitrum Nitro (Offchain Labs, 2022) | 2 | M5 Settlement (rationale) | Whitepaper |
| [4] | Fraud & DA Proofs (Al-Bassam et al., 2018) | 2 | DA layer (rationale) | Preprint |
| [20] | Scalable Verification / TrueBit (Teutsch & Reitwießner, 2017/2023) | 2 | M4 Verification | Whitepaper; later World Scientific volume |
| [18] | FLock (Cheng et al., 2025) | 2 | M4/M5 slashing | Preprint |
| [19] | The Sybil Attack (Douceur, IPTPS 2002) | 2 | M1 identity | Refereed workshop, LNCS |
| [5a] | Proofs of Useful Work (Ball et al., 2017) | 2 | Economic model | IACR ePrint |
| [5b] | Ofelimos (Fitzi et al., CRYPTO 2022) | 2 | Economic model | Refereed conference |
| [5c] | Proof-of-Learning (Jia et al., IEEE S&P 2021) | 2 | M4 Verification | Refereed conference |
| [6] | vLLM / PagedAttention (Kwon et al., SOSP 2023) | 3 | M3 (rationale only) | Refereed conference |
| [7a] | Ollama | 3 | M3 Inference | Software artefact |
| [7b] | llama.cpp | 3 | M3 Inference | Software artefact |
| [13] | IPFS (Benet, 2014) | 3 | Model storage (rationale) | Preprint, draft 3 |
| [16] | PolyLink (Liu et al., 2025) | 3 | Comparator | Peer-reviewed, IEEE Blockchain 2025 |
| [12] | DGrid AI (2025) | 3 | Comparator | Corporate litepaper |
| [21] | Morpheus whitepaper (2023) | 3 | Comparator, M5 pattern | Pseudonymous whitepaper |
| [22] | Morpheus-Lumerin-Node | 3 | M5 reference implementation | Software artefact |
| [23] | Bittensor empirical analysis (Lui & Sun, MARBLE 2025) | 3 | Comparator; failure-mode evidence | Refereed, Springer |
| [24] | BitTensor whitepaper (Rao et al., 2020) | 3 | Comparator | Whitepaper (arXiv version withdrawn) |
| [25] | Akash economics (Osuri & Bozanich, 2020) | 3 | Comparator, M2 pattern | Economic whitepaper |
| [26] | Golem (2016) | 3 | Historical prior art | Whitepaper |
| [27] | Gensyn litepaper (2022) | 3 | Comparator, M4 | Litepaper, superseded |
| [28] | DePIN: challenges and opportunities (Lin et al., 2024) | 3 | Definition, taxonomy | Preprint |
| [29] | Are you a DePIN? (Andrew & Ballandies, 2025) | 3 | Classification criteria | Preprint |
| [8] | LLM-as-a-Judge (Zheng et al., NeurIPS 2023 D&B) | 4 | M4 Verification | Refereed track |
| [9] | TruthfulQA (Lin et al., ACL 2022) | 4 | M4 evaluation | Refereed conference |
| [11] | Chatbot Arena (Chiang et al., ICML 2024) | 4 | M4 / reputation | Refereed conference |
| [17] | Nesa (Zhang et al., NeurIPS RBFM 2024) | 4 | M4 two-tier design | Refereed workshop |

Read as a whole, the survey supports a narrow and specific conclusion. Every individual mechanism The
Edge Grid uses is established: DHT discovery is twenty-three years old, gossip-based propagation runs
two of the largest blockchain networks in production, optimistic fraud proving has a formal
single-honest-verifier guarantee, judge-based evaluation has been validated against human agreement,
and staked settlement for AI inference is already live on Arbitrum in the Morpheus network. What the
survey does not contain is a single open system in which all five are wired together and the
composite pipeline is measured end to end. That is the observation Chapters 5 and 6 develop.

---

# Chapter 5

# COMPARATIVE ANALYSIS

## 5.1 Purpose and Basis of the Comparison

Chapter 4 examined each source in isolation. This chapter places the systems side by side along a
fixed set of dimensions, in order to establish two things: what coverage the existing landscape
already provides, and — equally important for the credibility of this report — where The Edge Grid
does *not* provide coverage.

The comparison in the Phase-1 presentation compared five systems along seven dimensions and scored
The Edge Grid as achieving all seven. That table is not reproduced here, for three reasons. First, it
included a dimension on which the project scored itself as production-deployed; the system is not
deployed, and that is the most easily falsified claim in the entire Phase-1 package. Second, it
compared the project only against research prototypes, omitting every decentralised compute network
running in production — which is where the strongest counter-evidence to a novelty claim actually
lives. Third, it awarded ticks for components that the design specifies but the implementation does
not contain. This chapter corrects all three.

The comparison below therefore differs from the Phase-1 version in four ways: the dimension set is
defined precisely enough that each entry is falsifiable; five running networks are added; The Edge
Grid is scored against what is implemented rather than against what is designed; and the divergences
between design and implementation are stated in their own table (Section 5.5) rather than left for a
reader to discover.

The pipeline being compared is shown in the sequence figure, reproduced as **Figure 5.1**. The seven dimensions correspond to
distinct stages of that sequence, which is why coverage of all seven is a meaningful property rather
than an arbitrary scorecard.

**Figure 5.1** — `docs/figures/sequence.png` — Job lifecycle from request through auction, execution,
data-availability commitment and sampled verification to settlement or slashing.

## 5.2 Definition of the Seven Dimensions

Each dimension is defined here as a testable predicate. A system satisfies a dimension only if the
predicate can be checked against its published artefacts or its running network.

**D1 — Economic incentives.** Providers receive compensation, denominated in a network token or in
fiat, that is a function of work performed. Altruistic or reputation-only participation does not
satisfy D1.

**D2 — Sybil resistance.** Registering an additional provider identity carries a cost that scales
with the number of identities — a stake, a bond, a hardware attestation or an equivalent — and that
cost is enforced by the protocol rather than by an operator's discretion.

**D3 — Blockchain settlement.** Payment for a unit of work is executed by a smart contract on a
public or permissionless ledger, rather than by an off-chain accounting system.

**D4 — Layer-2 gas optimisation.** Settlement is placed on a rollup or equivalent Layer-2 execution
environment specifically so that per-job micro-payments are economically viable. A system settling
directly on a Layer-1 chain, or on a local development chain, does not satisfy D4.

**D5 — Dedicated data-availability layer.** Output commitments are published to a layer whose
specific purpose is to guarantee that the committed data can be retrieved by any verifier, separate
from the settlement chain's own storage.

**D6 — Open-source inference runtime.** The component that executes the model is open source and can
be inspected, rebuilt and substituted by an operator.

**D7 — LLM-as-a-Judge verification.** Output correctness is assessed by a language model acting as an
evaluator, and that assessment is coupled to the payment or penalty decision.

The Phase-1 table carried an eighth, implicit dimension — production deployment — on which The Edge
Grid scored a tick. **That dimension is removed.** The Edge Grid is a Phase-1 reference
implementation running on a single development host and a local Hardhat chain. It has no mainnet
deployment, no external users and no operating node fleet. Retaining that dimension in any form would
mean either recording a falsehood or recording a cross against the project's own column in a table
whose stated purpose is to demonstrate its superiority, and neither is defensible. Production
deployment is stated in Chapter 6 as future work.

## 5.3 The Comparative Table

Legend: **Y** — satisfied; **N** — not satisfied; **P** — partially satisfied; **D** — specified in
the system's published design but not present in the artefact evaluated here.

**Table 5.1** — Comparison across seven dimensions

| System | D1 Economic incentives | D2 Sybil resistance | D3 Blockchain settlement | D4 L2 gas optimisation | D5 Dedicated DA layer | D6 Open-source runtime | D7 LLM-as-a-Judge |
|---|---|---|---|---|---|---|---|
| Centralised cloud (AWS / GCP / Azure) | N | N | N | N | N | N | N |
| Monolithic API providers (OpenAI, Anthropic) | N | N | N | N | N | N | N |
| Petals [10] | N | N | N | N | N | Y | N |
| Parallax [14] | N | N | N | N | N | Y | N |
| Navigator [15] | N | N | N | N | N | Y | N |
| Nesa [17] | Y | P | Y | N | N | P | P |
| PolyLink [16] | Y | P | Y | N | N | Y | Y |
| DGrid AI [12] | Y | P | Y | N | N | N | P |
| Bittensor [23], [24] | Y | Y | Y | N | N | Y | P |
| Akash [25] | Y | Y | Y | N | N | Y | N |
| io.net (class of [28]) | Y | P | Y | N | N | Y | N |
| Gensyn [27] | Y | Y | Y | P | N | Y | N |
| Morpheus / Lumerin [21], [22] | Y | Y | Y | Y | N | Y | N |
| **DePIN-Edge — The Edge Grid (this work)** | **Y** | **Y** | **Y** | **D** | **P** | **Y** | **Y** |

Notes on individual entries, so that each is checkable:

- **Petals, Parallax, Navigator** are cooperative research systems. Each satisfies D6 and nothing
 else; their authors do not claim otherwise.
- **Nesa** is scored P on D2 because its published framework describes staking and node selection but
 the workshop paper does not specify an enforced registration bond; P on D6 because the framework is
 described at architecture level rather than as a single substitutable runtime; and P on D7 because
 its consensus-based verification samples validators for agreement on output correctness without
 necessarily using a judge model in the sense of [8].
- **PolyLink** satisfies D7 unambiguously through its TIQE protocol and D1 and D3 through its token
 pricing and on-chain reward mechanism. It is scored P on D2 because the preprint describes device
 registration without an enforced economic bond.
- **DGrid** is scored N on D6 because its runtime is not open, and P on D7 because Proof-of-Quality is
 described as a quality-verification mechanism but not specified in reproducible detail.
- **Bittensor** is scored Y on D1, D2 and D3 — it is a live, staked, on-chain incentive network — and
 P on D7 because subnet validators evaluate miner outputs, in several subnets using model-based
 scoring, but the mechanism is subnet-specific rather than a protocol-level judge. The empirical
 finding of [23], that emissions track stake rather than output quality, is discussed in Section
 5.4.7 and is the reason this row is the most important in the table.
- **Akash** settles container leases through a reverse auction on its own chain and enforces provider
 bonds, hence Y on D1, D2 and D3, but performs no output verification at all, hence N on D7.
- **io.net** is characterised from [28] and public project documentation; no peer-reviewed
 characterisation was located, and the row is marked accordingly. It is scored P on D2 for the same
 reason as PolyLink.
- **Gensyn** is scored P on D4 because the cited litepaper's Substrate Layer 1 has since been replaced
 by a custom Ethereum rollup, a change the publisher acknowledges, so the L2 property holds for the
 current system but not for the cited document. Its verification is certificate-and-replication
 based rather than judge based, hence N on D7.
- **Morpheus / Lumerin** is the only prior system in the table satisfying D1 through D4 and D6
 simultaneously. It is scored N on D7: providers are selected and paid through bid-matching
 contracts, and output quality is not assessed by a judge model as a condition of payment.
- **The Edge Grid** is scored D on D4 and P on D5, and Section 5.5 explains both. It is scored Y on
 D2 because ECDSA secp256k1 identity in `edgegrid/identity.py` is bound to an enforced minimum stake
 in `contracts/contracts/NodeRegistry.sol`, and Y on D7 because
 `contracts/contracts/VerificationContract.sol` slashes only on a FAIL verdict produced by the judge
 pipeline in `verification/evaluator.py`.

## 5.4 Analysis

### 5.4.1 The centralised baselines

The two centralised rows score zero across the table, but this must be read correctly. They score
zero because the dimensions describe properties of a decentralised market, and a hyperscaler has no
need of any of them: it does not need Sybil resistance because it owns every machine, and it does not
need on-chain settlement because it bills its customers directly. Their inclusion establishes the
contrast the project exists to address — cost, geographic latency, data custody and single points of
failure — but a table of decentralisation properties is not an argument that centralised inference is
technically inferior, and this report does not present it as one.

### 5.4.2 The cooperative research systems

Petals, Parallax and Navigator form a coherent group: each is an excellent piece of systems research
that assumes away the economic problem. Their uniform pattern — Y on D6, N on everything else — is
not a deficiency of those works but a statement of their scope. What they collectively establish is
that the technical substrate is sound: distributed inference over consumer hardware works (Petals),
heterogeneous scheduling can be made efficient (Parallax), and decentralised placement does not cost
latency (Navigator). Every one of those results is a precondition for The Edge Grid, and none of them
is a competitor to it.

### 5.4.3 The verification-oriented academic systems

Nesa, PolyLink and DGrid occupy the middle band. All three combine an incentive layer with on-chain
settlement and some form of output verification, and PolyLink in particular is architecturally very
close to this project. Their common shortfall along D4 and D5 is real but should not be overstated:
neither an L2 nor a dedicated DA layer is a research contribution in its own right, and their absence
reflects a different engineering priority rather than an oversight. The more useful observation is
what the three of them share with The Edge Grid — the coupling of a semantic quality assessment to a
payment decision — because that shared property is what distinguishes this whole band from the
production networks in the rows below.

### 5.4.4 The production networks

The five production rows are the ones the Phase-1 comparison omitted, and they are the ones that most
constrain what this project may claim. Bittensor, Akash, Gensyn and Morpheus all satisfy D1, D2 and
D3 today, with real value at stake and real workloads settled. Morpheus additionally satisfies D4 and
D6, which means that the specific combination the Phase-1 submission advanced as its central novelty
— Arbitrum Layer-2 settlement plus provider bidding for LLM inference — is already running in
production and, moreover, is the pattern from which this project's own contract structure was drawn.
No honest reading of Table 5.1 permits a claim of novelty for that combination, and Chapter 6 does not
make one.

### 5.4.5 Where coverage genuinely thins

Two columns are sparse across the entire table. D5, a dedicated data-availability layer, is satisfied
by nothing in the comparison, including this project, which achieves it only partially. D7,
judge-based verification coupled to payment, is satisfied by PolyLink, partially by Nesa, DGrid and
Bittensor's subnet validators, and by nothing among the four largest production networks. Akash,
Gensyn and Morpheus each pay providers without any language-model assessment of what the provider
returned: Akash because it leases containers rather than answers; Gensyn because its verification is
replication-based and aimed at training; Morpheus because bid-matching and settlement, not output
adjudication, are its concern.

The intersection of D7 with D1 through D3 in a running, open, reproducible system is therefore where
the coverage of the existing landscape genuinely thins. That intersection, not any individual
mechanism, is where this project sits.

### 5.4.6 What the table does not show

Table 5.1 is a coverage table, and coverage is a weak property. A tick records that a system contains
a mechanism; it records nothing about how well that mechanism works. Morpheus's settlement is running
in production with real value at stake, while The Edge Grid's is running on a local Hardhat chain
with test value; both score Y on D3, and the two facts are not equivalent. Similarly, a Y on D7 says
nothing about judge precision — this project's own measured precision of 0.839 against injected
fraud, with a non-fine-tuned local judge and a single validator, is a modest number that the tick
conceals entirely.

Coverage tables of this form are therefore best read as a map of the design space rather than as a
ranking, and this report treats Table 5.1 accordingly. The substantive comparison — measured latency,
measured judge accuracy, measured settlement cost — belongs to the evaluation phase of the project
and not to a literature-derived matrix.

### 5.4.7 The most useful row: Bittensor and the stake-versus-quality finding

The single most valuable entry in the table is the P against Bittensor under D7, because of what Lui
and Sun's peer-reviewed analysis [23] found. Bittensor is the largest live decentralised-AI network,
and it is explicitly designed as a market that rewards useful intelligence; yet the empirical record
is that emissions are driven overwhelmingly by stake rather than by the quality of the outputs
participants produce. In other words, a network that intends to pay for quality can, in practice,
end up paying for capital.

That is a documented failure mode in a deployed system, and it reframes the argument for this project
entirely. Verification-linked payment is not proposed here as an improvement nobody has thought of;
it is proposed as a direct structural response to a measured misalignment in the largest existing
network of this kind. Coupling the release of escrow to a judge verdict, and coupling the slash to
that same verdict, is an attempt to make the compensation signal track output rather than balance.
Whether it succeeds is an empirical question. But "we are addressing a documented failure mode in a
production network" is an argument that survives examination, and "no prior art exists" is not.

## 5.5 Scope: Design Versus Implementation

The following divergences between the Phase-1 design and the Phase-1 implementation are stated here
in full. They are the reason two of this project's entries in Table 5.1 are D and P rather than Y.
Every one of them is declared rather than discovered, and each is verifiable against the file named.

**Table 5.2** — Design specified in Phase-1 against implementation delivered

| # | Phase-1 design | What is implemented | Evidence | Consequence for Table 5.1 |
|---|---|---|---|---|
| 1 | Arbitrum Stylus contracts in Rust compiled to WASM | Plain Solidity 0.8.24 on a local Hardhat EVM chain, chain ID 31337 | `contracts/contracts/*.sol`; `contracts/deployment.json` | D4 scored **D** — the L2 gas-optimisation property is designed, not obtained |
| 2 | Celestia as the data-availability layer | Local namespaced blob store with real binary Merkle inclusion proofs, domain-separated leaves and nodes | `edgegrid/da.py` | D5 scored **P** — binding is real and tested; availability under a decentralised validator set is not reproduced |
| 3 | vLLM with PagedAttention on CUDA | Ollama streaming runtime on CPU only | `inference/engine.py`; `docs/results/inference-benchmark-*/hardware_profile.json` reporting `vram_gb: 0.0`, `accelerator: none` | D6 unaffected; the CUDA node tier is future work |
| 4 | IPFS for model weight distribution | Content-addressed local store; on-chain hash registry retained | `edgegrid/da.py`; `contracts/contracts/ModelRegistry.sol` | Not a scored dimension; declared for completeness |
| 5 | Validator agents running fine-tuned judges | Off-the-shelf local model as judge, no fine-tuning | `verification/evaluator.py`; run config in `docs/results/verification-*/manifest.json` | D7 satisfied, but measured accuracy is a lower bound |
| 6 | Real economic stake | Test-value stake on a local chain, minimum stake of 10 units | `contracts/deployment.json` (`minStakeWei`) | D2 satisfied mechanically; not economically load-bearing |
| 7 | Production deployment | Single-host reference implementation | — | Dimension removed from the comparison entirely |

Three points about this table deserve emphasis.

First, every stand-in is implemented behind the same interface as the component it replaces. The
data-availability module exposes `submit_blob` and `get_blob`; substituting a Celestia light client
means reimplementing those two functions and nothing else. The settlement layer's semantics — escrow
state machine, challenge window, access control, the 80/20 split of a slash between the reporting
validator and the treasury — are implemented and tested in Solidity; porting them to Stylus is a
language change, not an architectural one. Replacing a stand-in means reimplementing one module, not
rewiring the system.

Second, what the stand-ins do deliver is not nothing. The Merkle inclusion proofs in `edgegrid/da.py`
are real: leaves and internal nodes are domain-separated to prevent second-preimage substitution, the
proof path is checkable by any party without trusting the store, and `verify_blob` performs the full
chain of checks a verifier would perform — blob present, its SHA-256 matching the provider's
commitment, and its proof landing on the recorded block root. The settlement contracts are real: they
compile, they deploy, they are covered by a Hardhat test suite, and their deployment record includes
the measured gas cost of each deployment — 1,121,822 for `NodeRegistry`, 1,198,543 for `Marketplace`,
1,641,201 for `VerificationContract` and 870,232 for `ModelRegistry`.

Third, the divergences are declared here precisely because they would otherwise be found. An examiner
who reads that the system uses Celestia, opens `edgegrid/da.py`, and finds a local blob store will
reasonably discount everything else the report asserts. An examiner who reads Table 5.2 first, and
then finds exactly the local blob store it describes, has been given a reason to trust the rest.

The settlement state machine that these contracts implement — the escrow lifecycle from opening
through the challenge window to settlement or slashing — is shown in
the settlement_states figure, reproduced as
**Figure 5.2**.

**Figure 5.2** — `docs/figures/settlement_states.png` — Escrow state machine: open, awaiting
verification, settled, slashed or refunded, with the challenge window governing the transition.

## 5.6 Summary of the Comparative Analysis

The comparison supports three conclusions and refutes one.

It supports, first, that the technical substrate of decentralised LLM inference is settled: Petals,
Parallax and Navigator between them remove feasibility, scheduling efficiency and latency from the
list of open questions. It supports, second, that staked on-chain settlement for AI compute is not
merely feasible but operating at scale, with Bittensor, Akash, Gensyn and Morpheus all satisfying D1
through D3 in production. It supports, third, that the coupling of judge-based semantic verification
to the payment decision is thinly covered — present in PolyLink and partially in Nesa, DGrid and
Bittensor's subnet validators, and absent from every one of the largest production networks.

It refutes the Phase-1 claim that The Edge Grid achieves full coverage across all seven dimensions.
It does not. Layer-2 gas optimisation is designed and not obtained, and the dedicated
data-availability layer is a documented local stand-in that delivers binding but not availability.
Recording those two entries honestly costs the project two ticks and buys it the standing to be
believed about the other five.

---

# Chapter 6

# RESEARCH GAP IDENTIFIED

## 6.1 Restating the Gap Correctly

The Phase-1 submission stated the research gap as follows: that while each component has been studied
independently, no existing system integrates all five into a single, open, economically
self-sustaining, blockchain-verified, latency-optimised and production-deployed decentralised AI
inference network.

That formulation cannot be sustained, and this report replaces it. It fails on two counts. The word
"production-deployed" describes a property this project does not have, so a gap defined by it is a
gap this project cannot claim to close. And the claim of no existing integration was tested against
seven research prototypes and zero production networks; as Chapter 5 established, Morpheus already
ships the specific combination of Arbitrum Layer-2 settlement and provider bidding for LLM inference
that the Phase-1 submission advanced as its central novelty, and it is the system this project's own
contract structure was patterned on.

The corrected statement of the gap is narrower, and it is a gap of **integration and empirical
evaluation** rather than of mechanism:

> Every mechanism required for a verifiable decentralised inference market has been established
> independently in the literature or is running in production, but no open, reproducible system
> combines peer-to-peer discovery, an incentive-compatible auction market, an open inference runtime,
> judge-based output verification and staked on-chain settlement into a single pipeline whose
> end-to-end behaviour — latency, verification accuracy, settlement cost and value conservation — has
> been measured and reported under a stated protocol.

The distinction between the two formulations is the difference between an indefensible claim and a
defensible one. The first asserts that nobody has built the parts; that is false and easily shown to
be false. The second asserts that nobody has assembled the parts into one artefact that can be run
and measured by a third party; that is true, and it is a contribution proportionate to a
final-year engineering project.

## 6.2 Component-by-Component Evidence for the Gap

The gap is established component by component below. In each case the pattern is the same: the
mechanism exists, and it exists inside a system that omits the neighbouring mechanisms.

**Peer-to-peer discovery exists without a market.** Kademlia [1] is twenty-three years old and
GossipSub [2] carries production traffic on Filecoin and Ethereum. Neither carries an auction.
Petals [10] runs a discovery mesh for inference specifically, and has no notion of price at all.

**Decentralised scheduling exists without incentives.** Parallax [14] and Navigator [15] both
demonstrate efficient placement of inference work across distributed nodes; both assume cooperative
participants. Neither contains a payment, a stake or an adversary model. Their results are ceilings
achieved under assumptions an open market cannot make.

**Staked settlement for compute exists without output verification.** Akash [25] settles container
leases through a reverse auction with provider bonds and verifies nothing about what the container
produced. Morpheus [21], [22] matches provider bids and settles on Arbitrum without assessing the
quality of the tokens the provider returned. Gensyn [27] verifies, but by certificate-and-replication
over training metadata, which does not transfer to autoregressive inference.

**Judge-based verification exists without a market or settlement around it.** Zheng et al. [8]
establish that a capable judge agrees with human preference at approximately human-to-human rates,
and TruthfulQA [9] and Chatbot Arena [11] supply the evaluation substrates. All three are offline
benchmarks. None is embedded in a system where the verdict moves money.

**Data-availability commitment exists without an inference pipeline attached.** Al-Bassam et al. [4]
formalise the light-client guarantee; the Celestia line of work productionises it. Nothing in that
literature concerns inference outputs.

**The nearest integrations each stop short.** PolyLink [16] comes closest in the academic literature,
combining settlement, judge verification and token incentives, and it is a preprint without a
data-availability layer or a gossip-based auction. DGrid [12] comes closest commercially and is a
litepaper with a proprietary, unspecified quality mechanism and a closed runtime. Nesa [17] supplies
the two-tier verification philosophy without a market. Morpheus comes closest in production and does
not judge outputs.

The composite this project builds — DHT discovery feeding a sealed-bid second-price auction, feeding
a streaming open runtime, feeding a Merkle-committed data-availability record, feeding a sampled
judge pool, feeding a staked settlement contract that slashes on a fail verdict — does not exist as
an open, runnable, measured artefact anywhere in the surveyed landscape.

## 6.3 A Second Gap: The Absence of Reported End-to-End Measurement

The integration gap has a corollary that is arguably the more useful half of this project's
contribution.

The systems surveyed report measurements of their own layer and of nothing else. Parallax reports
scheduling latency and throughput. Zheng et al. report judge-human agreement. FLock reports attack
success rate reduction. Morpheus and Akash report network activity. No surveyed system reports the
quantity a prospective operator or requester would actually need: the end-to-end cost and latency of
a verified inference, including the overhead the verification itself imposes, together with a check
that value is conserved across the settlement path.

That absence is not an oversight in any one paper; it is a consequence of the layers having been
studied separately. It becomes measurable only once the layers are composed, which is precisely what
this project does. The four experiments defined in `docs/EXPERIMENTS.md` — latency, auction
convergence, verification accuracy and cost-and-settlement — are designed to produce exactly those
composite numbers, with every run writing a full configuration snapshot and the git commit hash into
its own timestamped directory under `docs/results/` so that no run overwrites another and every
reported figure is traceable to the code state that produced it.

Two composite findings already visible in the recorded runs illustrate the point, and neither could
have been obtained from any single surveyed work:

- The cold-start penalty dominates the latency budget on commodity hardware. Paired cold-versus-warm
 measurements give cold-over-warm ratios of approximately 13.2 and 14.9, with cold-start
 time-to-first-token means of roughly 8.9 and 10.6 seconds against warm means below 750 ms. This is
 an economic result, not merely a performance one: it means that model residency, not raw compute
 capability, is the dominant term in a provider's ability to win a latency-constrained auction, and
 it is the empirical justification for the warm-start bonus in the bid-scoring rule.
- Judge-based verification is asymmetric in its errors in a way that matters when money is attached.
 Against injected fraud from four strategies over TruthfulQA-derived items, with a single validator
 and a non-fine-tuned local judge, the recorded run gives overall recall of 0.975 against overall
 precision of 0.839. A verification module that slashes stake on a judge verdict is far more damaged
 by its false positives than by its false negatives, and this measurement locates the problem
 concretely rather than leaving it as a caveat.

## 6.4 What This Project Does Not Claim to Invent

The following is stated explicitly, because a claim of novelty that is not carefully bounded is the
easiest thing in a report to falsify, and because an examiner is entitled to know exactly where the
line is drawn.

This project does **not** claim to have invented:

1. **Distributed hash tables or XOR-metric routing.** Kademlia is due to Maymounkov and Mazières [1],
 2002. This project uses py-libp2p's implementation of it.
2. **Gossip-based publish-subscribe or its attack resistance.** GossipSub is due to Vyzovitis et al.
 [2]. This project uses py-libp2p's implementation of it.
3. **The second-price sealed-bid auction.** The Vickrey mechanism and its incentive-compatibility
 property long predate this work. The project's contribution here is confined to a specific and
 modest engineering detail — applying the warm-start bonus as a handicap on the bid *score* rather
 than on the payment, so that the clearing price remains at or above the winner's own bid and
 truthful bidding stays dominant for warm nodes — which is a correct application of standard
 scoring-auction theory, not a new mechanism.
4. **Distributed LLM inference over consumer hardware.** Petals [10] demonstrated this.
5. **Efficient serving of quantised models.** PagedAttention [6] and the `llama.cpp` and Ollama
 projects [7a], [7b] are the state of the art; this project consumes Ollama's API and contributes
 nothing to the runtime.
6. **The LLM-as-a-Judge evaluation paradigm.** It is due to Zheng et al. [8], and the datasets used
 to exercise it here are TruthfulQA [9] and Chatbot Arena [11].
7. **Optimistic fraud proving or the single-honest-verifier result.** Both are due to Teutsch and
 Reitwießner [20], with the rollup realisation described in [3].
8. **Data-availability sampling or erasure-coded commitments.** Due to Al-Bassam, Sonnino and
 Buterin [4]; and this project does not implement them at all, using a local Merkle-committed
 stand-in instead.
9. **Blockchain-enforced peer review with slashing.** Demonstrated with measured effect by FLock
 [18], and running in production in Bittensor [24].
10. **Layer-2-settled AI inference marketplaces with provider bidding.** This exists in production as
 Morpheus [21] and its reference implementation [22], from which this project's contract structure
 was patterned. This is the claim the Phase-1 submission advanced as its novelty, and it is
 withdrawn.
11. **The concept of a market for idle consumer compute.** Golem [26] proposed it in 2016.
12. **Any cryptographic verification of inference.** No zero-knowledge machine learning is
 implemented; the two-tier philosophy is adopted from Nesa [17] with only the optimistic tier
 built.

What this project does claim is set out in Section 6.5, and it is deliberately narrow.

## 6.5 The Claim

The Edge Grid claims the following, and nothing beyond it.

**C1 — Integration.** An open-source reference implementation in which Kademlia-based discovery over
py-libp2p, a GossipSub sealed-bid second-price auction, a streaming open inference runtime, a
namespaced data-availability store with checkable binary Merkle inclusion proofs, a sampled
LLM-as-a-Judge validator pool with three-valued verdicts, and staked Solidity settlement with an
escrow state machine and an 80/20 slash split are wired into one job pipeline that runs end to end.
Each component is prior art; the composition, as a runnable artefact, is not.

**C2 — Empirical characterisation of the composite.** Measurement of the assembled pipeline under a
declared protocol, with per-run configuration snapshots and commit hashes, reporting quantities that
are properties of the composition rather than of any layer — the cold-start penalty's dominance of
the latency budget and its consequence for auction design; judge precision and recall against
systematically injected fraud; the overhead verification adds to the cost of a served token; and
conservation of value across escrow, settlement and slashing.

**C3 — A response to a documented failure mode.** Verification-linked payment, in which escrow
release and stake slashing are both conditioned on the judge verdict, positioned as a structural
response to the finding of Lui and Sun [23] that rewards in the largest live decentralised-AI network
track stake rather than output quality. This is a design response to measured evidence, offered for
evaluation rather than asserted as a solution.

**C4 — A declared-scope methodology.** The explicit separation, in Table 5.2, of what was designed
from what was built, with each stand-in implemented behind the interface of the component it
replaces. This is a small contribution, but it is a real one: it makes the artefact's limitations
checkable rather than requiring a reader to take the report's word for its capabilities.

## 6.6 Research Questions Arising

The gap and the claim above yield four questions that the remainder of the project addresses:

**RQ1.** What time-to-first-token can a market-scheduled pipeline achieve on commodity hardware, and
how is the latency budget distributed between auction clearing, model residency and generation?

**RQ2.** With what precision and recall does a non-fine-tuned judge model distinguish fraudulent from
honest inference outputs, and how does that trade-off move with validator pool size and sampling
rate?

**RQ3.** What is the total cost of a verified inference, including the compute consumed by
verification itself and the gas consumed by settlement, relative to a centralised baseline?

**RQ4.** Is value conserved across the settlement path — does the sum of provider payments, validator
rewards and treasury receipts equal the sum of requester escrows and slashed stakes, over an
adversarial run in which fraud is deliberately injected?

## 6.7 Limits on the Claim

Four limitations bound what this project's evaluation can establish, and they are stated here rather
than deferred to a concluding chapter.

**Scale.** The measurements are taken on a single development host running a small number of node
processes, and the auction-convergence runs recorded under `docs/results/` cover three, four and five
nodes. Nothing in this work establishes behaviour at the scale of hundreds or thousands of
geographically dispersed nodes, and no extrapolation from these figures to such a network is
warranted.

**Hardware.** The host has no NVIDIA GPU. All inference measurements are CPU-only, on a machine
reporting tier 1 with zero VRAM. The figures therefore characterise the pipeline, not the serving
economics of the discrete-GPU tier the network is ultimately intended to recruit.

**Verification quality.** The judge is an off-the-shelf local model, not a fine-tuned validator, and
the recorded runs use a single validator rather than a quorum. Measured accuracy is consequently a
lower bound on what the design can achieve, and the false-positive rate at this configuration is high
enough that it would be irresponsible to attach real economic penalties to it without further work.

**Economic realism.** Stakes are test values on a local chain. No conclusion about the adequacy of
any particular stake level, slashing share or reward split as a deterrent against a rational
adversary follows from these runs, because no participant in them had anything to lose.

These limits do not undermine the claim in Section 6.5, because that claim is about integration and
characterisation rather than about performance or economic security. They do bound it, and stating
them is part of making it defensible.

---

## References Cited in Chapters 4–6

The following list supersedes the reference list carried in the Phase-1 literature survey and
presentation. Entries [1]–[20] retain the Phase-1 numbering where the underlying work is unchanged,
with venue, year and authorship corrected. Entries [5a]–[5c] and [7a]–[7b] replace two Phase-1
entries that could not be verified as genuine publications. Entries [21]–[29] are additions.

[1] P. Maymounkov and D. Mazières, "Kademlia: A peer-to-peer information system based on the XOR
metric," in *Peer-to-Peer Systems: First International Workshop (IPTPS 2002)*, Cambridge, MA, USA,
Mar. 2002, Lecture Notes in Computer Science, vol. 2429, Berlin, Germany: Springer, 2002, pp. 53–65.
doi: 10.1007/3-540-45748-8_5.

[2] D. Vyzovitis, Y. Napora, D. McCormick, D. Dias, and Y. Psaras, "GossipSub: Attack-resilient
message propagation in the Filecoin and ETH2.0 networks," Protocol Labs, Technical Report, Jul. 2020.
[Online]. Available: https://arxiv.org/abs/2007.02754. arXiv:2007.02754.

[3] L. Bousfield, R. Bousfield, C. Buckland, B. Burgess, J. Colvin, E. W. Felten, S. Goldfeder,
D. Goldman, B. Huddleston, H. Kalodner, F. A. Lacs, H. Ng, A. Sanghi, T. Wilson, V. Yermakova, and
T. Zidenberg, "Arbitrum Nitro: A second-generation optimistic rollup," Offchain Labs, Inc.,
Whitepaper, Aug. 2022. [Online]. Available: https://docs.arbitrum.io/nitro-whitepaper.pdf

[4] M. Al-Bassam, A. Sonnino, and V. Buterin, "Fraud and data availability proofs: Maximising light
client security and scaling blockchains with dishonest majorities," University College London and
Ethereum Foundation, Preprint, Sep. 2018 (rev. May 2019). [Online]. Available:
https://arxiv.org/abs/1809.09044. arXiv:1809.09044.

[5a] M. Ball, A. Rosen, M. Sabin, and P. N. Vasudevan, "Proofs of useful work," IACR Cryptology
ePrint Archive, Report 2017/203, 2017. [Online]. Available: https://eprint.iacr.org/2017/203

[5b] M. Fitzi, A. Kiayias, G. Panagiotakos, and A. Russell, "Ofelimos: Combinatorial optimization via
proof-of-useful-work — A provably secure blockchain protocol," in *Advances in Cryptology — CRYPTO
2022*, Lecture Notes in Computer Science, vol. 13508, Cham, Switzerland: Springer, 2022, pp. 339–369.
doi: 10.1007/978-3-031-15979-4_12.

[5c] H. Jia, M. Yaghini, C. A. Choquette-Choo, N. Dullerud, A. Thudi, V. Chandrasekaran, and
N. Papernot, "Proof-of-Learning: Definitions and practice," in *Proc. 2021 IEEE Symposium on Security
and Privacy (SP)*, May 2021, pp. 1039–1056. doi: 10.1109/SP40001.2021.00106.

[6] W. Kwon, Z. Li, S. Zhuang, Y. Sheng, L. Zheng, C. H. Yu, J. E. Gonzalez, H. Zhang, and I. Stoica,
"Efficient memory management for large language model serving with PagedAttention," in *Proc. 29th
ACM Symposium on Operating Systems Principles (SOSP '23)*, Koblenz, Germany, Oct. 2023, pp. 611–626.
doi: 10.1145/3600006.3613165.

[7a] Ollama Contributors, *Ollama* (version 0.x) [Computer software]. Ollama Inc., 2023–. [Online].
Available: https://github.com/ollama/ollama

[7b] G. Gerganov and llama.cpp Contributors, *llama.cpp: LLM inference in C/C++* [Computer software].
2023–. [Online]. Available: https://github.com/ggml-org/llama.cpp

[8] L. Zheng, W.-L. Chiang, Y. Sheng, S. Zhuang, Z. Wu, Y. Zhuang, Z. Lin, Z. Li, D. Li, E. P. Xing,
H. Zhang, J. E. Gonzalez, and I. Stoica, "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena," in
*Advances in Neural Information Processing Systems 36 (NeurIPS 2023), Datasets and Benchmarks Track*,
New Orleans, LA, USA, Dec. 2023. Preprint: arXiv:2306.05685.

[9] S. Lin, J. Hilton, and O. Evans, "TruthfulQA: Measuring how models mimic human falsehoods," in
*Proc. 60th Annual Meeting of the Association for Computational Linguistics (Volume 1: Long Papers)*,
Dublin, Ireland, May 2022, pp. 3214–3252. doi: 10.18653/v1/2022.acl-long.229.

[10] A. Borzunov, D. Baranchuk, T. Dettmers, M. Ryabinin, Y. Belkada, A. Chumachenko, P. Samygin, and
C. Raffel, "Petals: Collaborative inference and fine-tuning of large models," in *Proc. 61st Annual
Meeting of the Association for Computational Linguistics (Volume 3: System Demonstrations)*, Toronto,
Canada, Jul. 2023, pp. 558–568. doi: 10.18653/v1/2023.acl-demo.54.

[11] W.-L. Chiang, L. Zheng, Y. Sheng, A. N. Angelopoulos, T. Li, D. Li, B. Zhu, H. Zhang,
M. I. Jordan, J. E. Gonzalez, and I. Stoica, "Chatbot Arena: An open platform for evaluating LLMs by
human preference," in *Proc. 41st International Conference on Machine Learning (ICML 2024)*, Vienna,
Austria, Jul. 2024, PMLR, vol. 235, pp. 8359–8388. Preprint: arXiv:2403.04132.

[12] DGrid.AI, "DGrid AI: The decentralized AI inference network for open, low-cost &
community-powered AI," Litepaper, Jun. 2025. [Online]. Available:
https://static.dgrid.ai/dgrid_litepaper.pdf [Accessed: Sep. 3, 2026].

[13] J. Benet, "IPFS — Content addressed, versioned, P2P file system (draft 3)," Protocol Labs,
Technical Report, Jul. 2014. [Online]. Available: https://arxiv.org/abs/1407.3561. arXiv:1407.3561.

[14] C. Tong, Y. Jiang, G. Chen, T. Zhao, S. Lu, W. Qu, E. Yang, L. Ai, and B. Yuan, "Parallax:
Efficient LLM inference service over decentralized environment," Preprint, Sep. 2025. [Online].
Available: https://arxiv.org/abs/2509.26182. arXiv:2509.26182.

[15] Y. Yang, A. Merlina, W. Song, T. Yuan, K. Birman, and R. Vitenberg, "Navigator: A decentralized
scheduler for latency-sensitive AI workflows," in *Proc. 2024 IEEE International Conference on Edge
Computing and Communications (EDGE)*, Shenzhen, China, Jul. 2024, pp. 35–47.
doi: 10.1109/EDGE62653.2024.00015.

[16] H. Liu, J. Cao, B. Yang, D. Bai, Y. Cao, X. Shen, Y. Zhang, J. Liang, S. Jiang, and M. Zhang,
"PolyLink: A blockchain based decentralized edge AI platform for LLM inference," in Proc. 2025 IEEE
Int. Conf. Blockchain (Blockchain), 2025, pp. 101-108, doi: 10.1109/Blockchain67634.2025.00023.
Preprint: arXiv:2510.02395, Oct. 2025.

[17] H. Zhang, Y. Zhao, C. Angione, H. Yang, J. Buban, A. Farhan, F. Johnston, and P. Colangelo,
"Towards secure and private AI: A framework for decentralized inference," in *Proc. NeurIPS 2024
Workshop on Responsibly Building the Next Generation of Multimodal Foundational Models (RBFM)*,
Vancouver, Canada, Dec. 2024. Preprint: arXiv:2407.19401.

[18] Z. Cheng, R. Sun, J. Sun, and Y. Guo, "Scaling decentralized learning with FLock," Preprint,
Jul. 2025 (rev. Aug. 2025). [Online]. Available: https://arxiv.org/abs/2507.15349. arXiv:2507.15349.

[19] J. R. Douceur, "The Sybil attack," in *Peer-to-Peer Systems: First International Workshop (IPTPS
2002)*, Cambridge, MA, USA, Mar. 2002, Lecture Notes in Computer Science, vol. 2429, Berlin, Germany:
Springer, 2002, pp. 251–260. doi: 10.1007/3-540-45748-8_24.

[20] J. Teutsch and C. Reitwießner, "A scalable verification solution for blockchains," TrueBit
Whitepaper, Nov. 2017; also published in *Aspects of Computation and Automata Theory with
Applications*, Lecture Notes Series, Institute for Mathematical Sciences, NUS, vol. 42, Singapore:
World Scientific, 2023, pp. 377–424. doi: 10.1142/9789811278631_0015. Preprint: arXiv:1908.04756.

[21] Morpheus, Trinity, and Neo (pseudonymous), "Morpheus: A network for powering smart agents,"
Morpheus Whitepaper, Sep. 2023. [Online]. Available:
https://github.com/MorpheusAIs/Docs/blob/main/!KEYDOCS%20README%20FIRST!/WhitePaper.md

[22] MorpheusAIs, *Morpheus-Lumerin-Node: Proxy-router and inference marketplace node* [Computer
software]. 2024–. [Online]. Available: https://github.com/MorpheusAIs/Morpheus-Lumerin-Node

[23] E. Lui and J. Sun, "Bittensor protocol: The Bitcoin in decentralized artificial intelligence? A
critical and empirical analysis," in *Mathematical Research for Blockchain Economy: 6th International
Conference (MARBLE 2025)*, Athens, Greece, Lecture Notes in Operations Research, Cham, Switzerland:
Springer, 2026, pp. 145–165. doi: 10.1007/978-3-032-13377-9_7. Preprint: arXiv:2507.02951.

[24] Y. Rao, J. Steeves, A. Shaabana, D. Attevelt, and M. McAteer, "BitTensor: A peer-to-peer
intelligence market," Opentensor Foundation, Whitepaper, Mar. 2020 (rev. Nov. 2021). [Online].
Available: https://bittensor.com/whitepaper (the arXiv version, arXiv:2003.03917, has been withdrawn
by its authors and should not be cited).

[25] G. Osuri and A. Bozanich, "AKT: Akash network token and mining economics," Overclock Labs,
Economic Whitepaper, Mar. 2020. [Online]. Available:
https://akash-web-prod.s3.amazonaws.com/uploads/2020/03/akash-econ.pdf

[26] Golem Factory GmbH, "The Golem project: Crowdfunding whitepaper," Nov. 2016. [Online].
Available:
https://assets.website-files.com/62446d07873fde065cbcb8d5/62446d07873fdeb626bcb927_Golemwhitepaper.pdf

[27] Gensyn AI Ltd., "Gensyn litepaper: A protocol for verifiable machine learning compute,"
Technical Report, 2022 (legacy edition; superseded — the publisher has since replaced the Substrate
Layer 1 described therein with a custom Ethereum rollup). [Online]. Available:
https://docs.gensyn.ai/litepaper

[28] Z. Lin, T. Wang, L. Shi, S. Zhang, and B. Cao, "Decentralized physical infrastructure network
(DePIN): Challenges and opportunities," Preprint, Jun. 2024. [Online]. Available:
https://arxiv.org/abs/2406.02239. arXiv:2406.02239.

[29] M. S. Andrew and M. C. Ballandies, "Are you a DePIN? A decision tree to classify decentralized
physical infrastructure networks," Preprint, Jan. 2025. [Online]. Available:
https://arxiv.org/abs/2501.17416. arXiv:2501.17416.
