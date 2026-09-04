# The Edge Grid: A Prototype for Decentralized, Verifiable Peer-to-Peer AI Inference

**Harshit Raj, Chetan Raghuvanshi, Keshav Narayan, Mayur Agarwal**
*Department of Computer Science and Engineering (IoT, Cyber-Security and Blockchain Technology)*
*Sir M. Visvesvaraya Institute of Technology, VTU Belagavi*

*Guide: Dr. Savita Choudhary, Professor & Head*

---

## Abstract

Centralized inference services concentrate pricing power, availability, and trust in a single
operator, and decentralized physical infrastructure networks have already shown that peer-to-peer
markets can sell connectivity, storage, and general compute at scale. Selling verifiable AI
inference the same way raises a problem those networks do not solve: an anonymous edge provider,
operating under a hard latency budget, has both the means and the incentive to return a
plausible-looking wrong answer, and naive redundancy or centralized scheduling cannot detect that
cheaply. We design a network in which a job is discovered and auctioned under a sealed-bid,
warm-start-aware second-price rule; executed by a streaming edge inference runtime; committed to
a Merkle-backed data-availability layer; and verified by a two-tier mechanism that separates a
trustless cryptographic fraud proof from a sampled, quorum-voted semantic judge, with staked
collateral slashed 80/20 between reporter and treasury on confirmed fraud. We build and measure a
small-scale reference implementation of this design on commodity CPU hardware: warm time-to-first-
token averaged 610 ms with a 12× cold-start penalty; a sealed-bid auction cleared correctly across
57 trials and responded near-linearly to injected per-link network delay; content-addressed model
weights were fetched, independently re-verified, and correctly rejected under three tampering
scenarios; and on-chain settlement conserved value exactly across all three resolution paths. The
verification layer's measured behavior is the paper's central finding: the deployable, edge-
hostable judge configuration caught only 30–35% of two semantically subtle fraud strategies while
scoring them confidently rather than hesitantly, and a follow-up experiment shows this failure
tracks judge capability rather than the judge's relationship to the model it polices, refuting an
explanation this evaluation initially favored. We report the composed pipeline, the measured
numbers, and this refuted hypothesis as a bounded but reproducible characterization of what such
a system achieves today and what a production deployment still requires.

**Index Terms** — decentralized physical infrastructure networks, verifiable AI inference,
LLM-as-a-Judge, second-price auctions, optimistic fraud proofs, data availability, staked
settlement.

---

## 1. Introduction

### 1.1 Problem Background

Centralized inference services have made large-model access cheap and reliable, but that
reliability is purchased at the cost of concentrating pricing power, infrastructure control, and
data governance in a small number of operators. Decentralized physical infrastructure networks
(DePIN) have already demonstrated, at production scale, that a permissionless, token-incentivized
provider set can sell a commodity resource competitively: connectivity, storage, and
general-purpose compute all have working, economically self-sustaining markets today. What none
of the production DePIN networks surveyed in Section 2 solve is the specific problem this paper
addresses: **selling AI inference, rather than raw compute, from anonymous edge providers under a
latency budget the requester actually cares about, with the output's correctness verified cheaply
enough that verification does not eat the economics it is supposed to protect.**

### 1.2 The Technical Gap

The naive answers to "how do you know an untrusted provider's output was correct" do not survive
contact with this problem. Redundant execution — running every job on several providers and
comparing outputs — multiplies cost by the redundancy factor for every job, not just the
audited fraction, which is economically incompatible with competing against a centralized
provider's marginal cost. Centralized scheduling and verification reintroduce the single point of
control the network exists to remove. And a purely reputation-based system provides no recourse
for the specific job on which a provider chose to cheat; reputation punishes a *pattern*, not a
transaction. What is needed is a mechanism that (a) prices and enforces a latency budget at the
point a job is routed, not after the fact, (b) verifies a *sampled* fraction of jobs cheaply
enough that the sampling rate can be low without leaving the deterrent hollow, and (c) makes the
economic consequence of confirmed fraud automatic and proportionate, without requiring every
participant to trust a central adjudicator.

Two properties of AI inference specifically make this harder than verifying, say, that a storage
provider is still holding the bytes it was paid to hold. First, a wrong answer is not obviously
wrong: a fabricated fact, a plausible-but-incorrect substitution, or a subtly negated claim can be
fluent, well-formed, and indistinguishable from a correct answer to anyone who does not already
know the answer — which is a structurally different verification problem from a checksum
mismatch. Second, the party best positioned to catch a subtle semantic error is itself a language
model, which means the verification layer inherits every reliability question that attaches to
language models generally: it can be wrong, it can be wrong *confidently*, and its errors may not
be independent of the errors of the model it is checking. This paper's central empirical finding
(Section 5.4) is a direct measurement of exactly that last risk.

### 1.3 Threat Intuition

An adversary in this network is a rational economic actor, not an omnipotent one: it can operate
many identities, misreport its own latency or capability, collude with a validator it also
controls, or simply return a wrong answer and hope the sampled audit misses it. The system's
defenses are calibrated to that actor rather than to an unbounded one — staking makes identity
multiplication costly rather than impossible, a cryptographic fraud proof makes one class of
dishonesty provable with certainty rather than merely likely, and a sampled, quorum-voted semantic
check makes another class of dishonesty *probabilistically* costly to attempt. Section 3.2 states
this threat model in full, including what it explicitly does not claim to solve.

### 1.4 Core Idea

We design the network around a strict separation between two kinds of verification claim that
existing decentralized-compute systems tend to blur together. A **cryptographic fraud proof**
establishes, with certainty and without trusting any party, that a provider served output
inconsistent with what it committed on chain — this is a fact about two hashes, not an opinion,
and it needs no challenge period once proven. A **semantic verdict**, by contrast, is a judgment
call about whether a faithfully-delivered output is actually correct, and it is inherently
probabilistic: it is rendered by one or more independent validator models under a quorum rule,
sampled at a rate the network can tune, and it is the tier this paper's evaluation subjects to the
most scrutiny, because it is also the tier whose failure mode is the least visible until it is
measured. Structuring verification this way lets the cheap, certain tier absorb one entire class
of dishonesty for a fixed, small cost, and confines the expensive, probabilistic tier to exactly
the class of dishonesty that genuinely requires judgment.

### 1.5 Contributions

**Design contributions.**

- We design a market protocol combining a sealed-bid second-price auction with a warm-start
  handicap applied to ranking score but never to payment, so that model residency — which this
  paper measures to be a near-eligibility condition rather than a smoothly tradeable quality
  dimension under realistic latency budgets (Section 5.2) — can be priced without breaking the
  individual-rationality property that makes truthful bidding dominant (Section 3.4).
- We design a two-tier verification protocol that structurally separates a trustless
  cryptographic fraud proof from a sampled, quorum-voted, judge-capability-sensitive semantic
  check, with a three-valued verdict (pass/fail/error) that never collapses "the judge was
  unreachable" into either a pass or a fraud finding (Section 3.6).
- We design settlement as an escrow whose confirming authority is narrow and whose release path
  requires no privileged party to remain online, with unbonding collateral that stays slashable
  specifically to close a front-running window a naive design would leave open (Section 3.7).

**Prototype and evaluation contributions.**

- We build and measure a working, reproducible reference implementation of the above on
  commodity CPU hardware, with every reported figure traceable to a timestamped, configuration-
  snapshotted run directory (Sections 4–5).
- We measure that the deployable verification configuration is confidently, not hesitantly, wrong
  on two of four fraud strategies — and we measure, in a dedicated follow-up experiment, that
  this failure tracks judge *capability* rather than the judge's *lineage* relative to the model
  it polices, refuting an explanation this evaluation initially favored and reporting that
  refutation rather than quietly revising it away (Section 5.4, Section 6.2).
- We measure that quorum-based aggregation across judges reduces the false-positive rate well
  below that of its individual complete members at no measured cost to recall, providing direct
  empirical support for a design choice this paper's own architecture specifies but does not, on
  its own, prove necessary (Section 5.4).
- We identify and quantify a methodological confound — that a model-generated "honest" control
  answer is not reliably honest, and bounds every precision figure any LLM-as-a-Judge evaluation
  reports — that we show recurred in this evaluation's own pipeline even after being diagnosed
  once (Section 6.2), and that we expect generalizes beyond this system.

The remainder of this paper proceeds as follows: Section 2 positions this work against DePIN,
mechanism-design, data-availability, and LLM-judging literatures; Section 3 gives the system
design; Section 4 the implementation and its declared prototype substitutions; Section 5 the
evaluation; Section 6 discusses what the results do and do not support; Section 7 concludes.

---

## 2. Related Work

*(Numbered 2 to match final assembly order; drafted after Sections 3–5 per the workflow's
literature-last-among-code-sections rule. Citations below use the corrected, independently
re-verified bibliography of `docs/REFERENCES.md` — not the original Phase-1 reference list, which
that document's own audit found materially wrong or fabricated in fourteen of twenty entries.
Reference numbers below match `docs/REFERENCES.md`'s numbering.)*

**DePIN and decentralized resource networks.** A family of production networks already sells
compute, storage, or connectivity through a token-incentivized, permissionless provider set:
Bittensor rewards providers for machine-learning work but has been shown empirically to allocate
reward predominantly by stake rather than by output quality [23]; Akash [25] and Golem [26]
auction general-purpose compute without an inference-specific verification layer; Gensyn [27]
targets verifiable machine-learning compute broadly, with a cost model oriented toward training
rather than low-latency interactive inference. None of these couples a latency-bounded scheduling
auction to an inference-specific, sampled semantic verification layer with staked slashing the
way this work's design does — the closest is addressed next.

**Auction-based and market-settled inference.** Morpheus [21, 22] is the most direct architectural
precedent: an Arbitrum-L2-settled marketplace in which compute providers bid and smart contracts
match them to serve LLM inference — precisely the combination of provider bidding and rollup
settlement this project also adopts. This work differs in mechanism (a sealed-bid, warm-start-
aware second-price auction with explicit hard-constraint eligibility, versus Morpheus's own
matching rule) and in adding a two-tier verification layer — a trustless data-mismatch fraud
proof beneath a sampled, quorum-voted semantic judge — that is outside Morpheus's stated scope.
DGrid [12] and Parallax [14] describe decentralized inference serving without the auction or
verification layers evaluated here; DGrid is a non-peer-reviewed corporate litepaper and Parallax
an unrefereed preprint, and any comparison to either is accordingly labeled as a vendor- or
author-reported claim rather than a peer-reviewed result.

**Mechanism design for latency-sensitive scheduling.** The second-price (Vickrey) rule this
work's market protocol builds on is classical; what is less standard is applying it as a
*procurement* auction under a hard latency constraint, with a warm-start handicap applied to
ranking score but never to payment, specifically to preserve individual rationality while still
expressing "this provider cannot serve within budget" as an eligibility condition rather than a
price signal (§3.4). PolyLink [16] — a peer-reviewed blockchain-based decentralized edge AI
platform for LLM inference — is the closest refereed academic parallel for the settlement side of
this combination; it does not evaluate an auction's response to injected network latency the way
this work's container experiment does (§5.3).

**Data availability, fraud proofs, and optimistic verification.** This work's data-availability
layer implements the *binding* property of Celestia's namespaced-blob, Merkle-committed design
[4b] against a local store, explicitly declining to claim Celestia's *availability* guarantee
under a decentralized, sampling validator set [4b] (§4.2, Table 4.1). Settlement's optimistic
challenge-window structure follows the general fraud-proof lineage established for rollups [3, 4b,
20]: a claim stands unless someone proves it wrong within a fixed window, with the proof itself
either a cryptographic certainty (the Merkle mismatch path) or a staked oracle's assertion (the
judge-verdict path) — kept structurally distinct in this design (§3.6) rather than presented as
one undifferentiated "verification."

**Proof of useful work and verifiable computation.** The broader premise that idle compute can be
monetized through provably useful work, rather than wasted hashing, has real theoretical grounding
[5a, 5b] and a formal treatment of proving that a specific computation was performed [5c] — the
closest genuine antecedent to this design's Agentic Verification tier, though that formal work
concerns proof of *training*, not inference, and the judge-based semantic tier evaluated here
remains a probabilistic, not a cryptographic, check. Zero-knowledge proof of correct model
execution — which would make the semantic judge's capability limitations (§5.4) structurally
moot, since a proof system holds no opinions — is architecturally compatible with the fraud-proof
interface this design already exposes, but proving transformer inference remains several orders
of magnitude more expensive than the inference itself and is not evaluated here (§7.2).

**LLM-as-a-Judge and verifiable AI evaluation.** The LLM-as-a-Judge paradigm this design's
semantic verification tier relies on is itself an active research area [8], evaluated on general
chatbot preference [8, 11] and, closer to this work's use case, on models' tendency to reproduce
human misconceptions [9] — the TruthfulQA benchmark this evaluation's fraud-injection corpus is
built from. This work's contribution to that literature is narrower and empirical: a measured
finding that judge *capability*, not model *lineage* relative to the model being policed,
determines whether semantically subtle (negated or plausibly-substituted) falsehoods are caught
(§5.4), which qualifies rather than confirms the intuition — plausible on its face — that
same-family judges share blind spots with the models they police.

**Decentralized and distributed LLM inference systems.** Petals [10, 10b] and systems in the same
line demonstrate collaborative multi-peer inference of models too large for one machine, which is
a complementary rather than competing problem to this work's single-node-per-job model; Navigator
[15] addresses decentralized *scheduling* for latency-sensitive AI workloads specifically, the
closest prior work to this design's market protocol on the scheduling side alone, though without
this work's verification or settlement layers.

**Positioning.** No system surveyed above combines Kademlia-based discovery, a warm-start-aware
second-price procurement auction under a hard latency budget, a two-tier verification pipeline
separating a trustless cryptographic fraud proof from a sampled, quorum-voted, judge-capability-
sensitive semantic check, and on-chain staked slashing with an 80/20 reward split, in one
evaluated, reproducible reference implementation. Morpheus [21, 22] is the nearest production
system on the market-and-settlement side and does not evaluate a comparable verification layer;
PolyLink [16] is the nearest peer-reviewed academic parallel and does not evaluate the auction's
network-latency response measured here. This is a narrower and more defensible claim than "no
prior system combines these mechanisms" stated without qualification, and it is the claim this
paper makes.

---

## 3. System Design

### 3.1 System Model and Roles

We design a network of mutually untrusting peers that jointly deliver AI inference without a
central directory, a central scheduler, or a central payment rail. The network recognizes four
roles, and a single peer may occupy more than one of them simultaneously:

- **Requesters** submit inference jobs and pay for them. A requester specifies the work (a
  prompt and a model identifier), a price ceiling, and a latency budget, and receives back a
  signed output together with a receipt that a third party can later audit.
- **Providers** execute inference jobs on their own hardware and are paid for doing so. A
  provider advertises the models it can serve, its current capacity, and — when it holds a
  model already resident and ready to execute — that fact specifically, because readiness
  carries real value to a latency-sensitive requester.
- **Validators** independently re-examine a sampled fraction of completed jobs and rule on
  whether the delivered output was faithful to what the provider actually produced, and,
  separately, whether that output is honest. A validator's ruling is either a matter of
  objective, checkable fact or a matter of judgment, and the design keeps those two kinds of
  ruling structurally distinct rather than blending them into one opaque verdict (§3.6).
- **The settlement layer** holds every provider's collateral, escrows every job's payment, and
  releases or confiscates that escrow according to rules that no single party — including the
  operator of the network, if there is one — can unilaterally override.

No peer is privileged. Any peer may act as requester, provider, or validator for a given job,
subject only to holding sufficient stake where staking is a precondition (§3.7). The network's
job lifecycle is a five-stage pipeline — discovery, market, inference, verification,
settlement — and each stage communicates with its neighbors through a fixed, versioned message
contract (Figure 1). Fixing that contract is a design decision in its own
right: a track that produces or consumes one stage of the pipeline can be built, changed, and
tested independently of every other track, because the only thing tracks share is the shape of
the messages between them, never one another's internal state. Figure 2 traces
one job through the full pipeline, from broadcast to settlement.

![Figure 1. System architecture: the five-stage job pipeline and the fixed message contract between stages.](docs/figures/architecture.png)

*Figure 1. System architecture: the five-stage job pipeline and the fixed message contract between stages.*


![Figure 2. Sequence of one job through the full pipeline, from broadcast to settlement.](docs/figures/sequence.png)

*Figure 2. Sequence of one job through the full pipeline, from broadcast to settlement.*


### 3.2 Threat Model

We ground the threat model in what the settlement mechanism can and cannot detect, rather than
in an abstract taxonomy, because a threat the mechanism cannot act on is not a threat the design
addresses — it is a limitation to be stated plainly in evaluation, not a case to claim coverage
of here.

**Cheating providers.** A provider may return a low-quality, truncated, hallucinated, or
otherwise unfaithful output while claiming payment for correct work. The design addresses this
directly: a sampled fraction of jobs are independently re-scored (§3.6), and a confirmed
fraudulent output costs the provider a share of its staked collateral (§3.7). We distinguish two
sub-cases with different trust requirements. A provider that reports an output different from
the one it actually committed to the network is *provably* dishonest — the mismatch is a fact
about two hashes, checkable by anyone, and requires trusting no party. A provider that reports
the output it actually produced, but that output is factually wrong or unfaithful to the prompt,
is dishonest in a way that only a competent judge can detect, and that judgment carries the
judge's own error rate as an irreducible cost.

**Colluding provider and validator.** A validator that is also, or is bribed by, the provider it
is auditing has an incentive to rule PASS regardless of the true quality of the output. The
design's response is structural rather than purely economic: the deterministic, unpredictable
sampling rule (§3.6) means a provider cannot know in advance which of its jobs will be audited,
which limits the value of corrupting a specific validator to corrupting validators broadly; and
a validator's own stake and the requirement that a quorum, not a single validator, confirm a
verdict are both direct mitigations, discussed further as a targeted rather than achieved
property in §3.8. The design does **not** claim to solve validator collusion at scale — that is
an open question about validator selection and reputation that this design's staking mechanism
alone does not close, and it is stated as such rather than assumed away.

**Sybil nodes.** A single adversary controlling many low-cost identities could attempt to bias
discovery, flood the bid pool, or manufacture apparent competition in an auction it in fact
controls entirely. Binding every network identity to a cryptographic keypair that also serves as
a wallet address (§3.3) does not prevent an adversary from generating many keypairs cheaply; what
it prevents is that adversary acting under any identity without also being able to stake and be
slashed under that identity. Sybil resistance in this design is therefore economic — the cost of
mounting an attack scales with the stake an attacker is willing to put at risk — rather than
identity-based, and its adequacy against a well-resourced adversary is an open question the
design does not resolve.

**Latency manipulation.** A provider could misreport its expected latency to win an auction it
cannot actually serve within budget, or a requester could manipulate the timing of its own
auction to disadvantage specific bidders. The market protocol (§3.4) treats a latency claim as a
hard eligibility constraint rather than a soft preference, and settlement does not depend on the
claim being honest in the way that price does — a provider that wins on a false latency claim and
then fails to deliver within budget is a case the design defers to the general verification path
rather than solving with a separate latency-specific mechanism, and we flag this as a gap rather
than claim otherwise.

### 3.3 Discovery and Identity

We design node identity around a single cryptographic keypair per participant, so that one
signature verifies three things at once: the peer's identity on the discovery network, the
wallet address that holds its stake and receives its payments, and the authenticity of every
message the peer sends. Binding these three together removes an entire class of impersonation
attack — a validator cannot rule on a job under one identity while collecting a reward under
another, and a provider's discovery-layer advertisement cannot be forged by a peer that does not
hold the corresponding private key.

Node capability is advertised through two channels that we deliberately keep separate, because
they change at different rates and serve different consumers. A **capability record** — which
models a node serves, its hardware tier, its stake, its network address — changes rarely and is
propagated through a distributed hash table: expensive to update, but durable and queryable by
any peer that needs to find a provider for a given model. A **liveness signal** — whether a peer
is currently reachable, and critically, which of its models are currently loaded and ready
rather than merely installed — changes on the order of seconds and is propagated directly between
neighboring peers rather than through the distributed store. Collapsing these two into one
channel would force a choice between an expensive, high-frequency update to a durable store or a
liveness signal too stale to be useful to an auction that clears in seconds; keeping them
separate lets the durable store stay durable and the liveness signal stay current. Every message
on both channels is signed, and a message whose signature does not match its claimed identity is
rejected at the point of receipt rather than passed downstream for another component to
distrust.

### 3.4 Market Protocol

We design the market as a **sealed-bid, second-price procurement auction**: a requester
broadcasts a job with a price ceiling and a latency budget; eligible providers submit sealed
bids naming the price they want to be paid; the requester's own node closes the auction after a
fixed collection window and computes the result without any further trust in the requester,
because the winner and clearing price are a deterministic function of the bid set that any
observer holding the same bids can recompute and audit.

Eligibility is evaluated as a set of hard constraints before any price comparison: a bid that
fails to meet the job's minimum hardware tier, that claims a latency above the requester's
budget, or that exceeds the price ceiling is not a *worse* bid, it is not a bid at all, and it is
excluded with a named reason rather than merely losing on price. Among eligible bids, the winner
is the one offering the lowest price, and — the defining property of a second-price mechanism —
the winner is paid not its own bid but the price of the second-lowest eligible bid. This
decouples what a bid decides (whether a provider wins) from what a bid determines (nothing,
about the provider's own payment), which is what makes truthful bidding of one's actual reserve
price the dominant strategy: a provider that shades its bid upward risks losing a job it would
have profitably won, and a provider that shades downward only risks winning at a price it would
have accepted anyway.

We extend the plain second-price rule with one deliberate distortion: a provider whose bid claims
the requested model is already resident and ready receives a discount applied to its *ranking
score* only, never to the price it is ultimately paid. The justification is architectural rather
than incidental. A cold provider must load a model before it can begin serving a request, and
that load cost is large and fixed regardless of how fast the provider can subsequently generate;
over the latency ranges an interactive requester cares about, warmth is closer to an eligibility
condition than a smoothly tradeable quality dimension, and a market that cannot express that
difference will route latency-sensitive requests to providers that cannot actually serve them in
time. Applying the discount to score but never to payment preserves the individual-rationality
property that makes the mechanism trustworthy in the first place: a winning provider is always
paid at least what it bid, so no provider is ever incentivized to walk away from a job it won.
With a single eligible bidder — no runner-up to set a second price against — the requester's own
declared price ceiling stands in as the clearing price, which is the natural extension of the
reserve-price convention rather than a special case bolted on.

### 3.5 Inference Pipeline

We design the inference stage as a narrow contract between the market layer and any runtime
capable of streaming generated tokens and reporting, per request, the true wall-clock time to the
first generated token, the number of tokens actually produced by the model's own tokenizer, and
whether the model was already loaded at the moment the request arrived. Nothing about the market
or verification layers depends on which runtime satisfies this contract, or on how it is deployed
— the contract is deliberately runtime-agnostic, because the property the rest of the system
relies on is that time-to-first-token is *measurable at all*, which requires only that generation
be observed as a stream rather than awaited as a single completed response. A provider that
cannot report a genuine token-level count, or cannot distinguish a cold load from a warm one,
cannot participate honestly in a market that prices exactly those two quantities, and the design
treats a missing measurement as a refusal to serve the job rather than as a plausible-looking
default.

The output of this stage is a signed claim binding one job to one output: the provider's own
account of what it produced, how long it took, and how many tokens it generated, cryptographically
tied to the provider's identity so the claim cannot later be repudiated or reattributed.

### 3.6 Verification Protocol

We design verification as a two-tier mechanism that spends the cheaper form of scrutiny before
the more expensive one, and that keeps a cryptographically certain finding structurally distinct
from a model's subjective judgment.

Every completed job is committed to an append-only, content-addressed store before it is
eligible for payment: the provider's output is bound to a cryptographic commitment that any
verifier can later check against the delivered bytes without trusting the store that holds them.
A fraction of committed jobs, selected by a rule that is deterministic — reproducible by any
auditor who holds the sampling key — and unpredictable to the provider being audited, is pulled
for verification. The first check run against a sampled job is purely mechanical: does the
committed output actually match what is retrievable from the store, under a cryptographic proof
that requires no runtime, no model, and no judgment call? A mismatch here is a **fraud proof**,
not an opinion, and the design treats it as certain: it needs no further corroboration and no
challenge window before it can be acted on.

Only a job that survives this mechanical check proceeds to the second tier, where one or more
independent validator agents judge the semantic quality of the output — whether it is faithful
to the prompt and, where applicable, factually accurate. Because this tier is inherently a
matter of judgment rather than proof, the design requires that a validator's ruling be one of
three values, never two: an output passes, an output fails, or a validator's ruling is
unavailable — because the model it depends on could not be reached, returned no usable
judgment, or the pool of validators failed to reach whatever quorum is required. Collapsing
"unavailable" into either pass or fail is a design failure the mechanism is built to avoid in
both directions: reading it as pass would let an adversary win by making a validator
unreachable, and reading it as fail would make ordinary infrastructure trouble indistinguishable
from proven fraud. Where more than one validator judges the same job, we further design for
validator *diversity* to be a first-class, recorded property rather than an assumption: a
validator pool assembled from independent copies of one model provides correlated evidence, not
independent evidence, and the design keeps that distinction visible in every result rather than
letting a panel's size imply an independence it may not have.

### 3.7 Settlement and Slashing

We design settlement as an escrow whose state transitions are fixed and whose confirming
authority for any given job is narrow, so that neither a requester nor a provider can
unilaterally alter the outcome of a job it is a party to. A requester's payment is locked in
escrow at the moment a job is awarded, against a provider that must hold active collateral
before it can be awarded work at all — collateral that exists specifically so a confirmed act of
fraud has something real to confiscate. Once the provider commits its output, a fixed challenge
window opens during which the commitment may be contested; if the window closes uncontested, the
escrow releases to the provider through a step that requires no privileged party to remain
online, because an honest outcome that depends on someone staying available is not a trustless
outcome. If the window closes on a confirmed fraud — either the mechanical fraud proof of §3.6 or
a validator's FAIL ruling — the escrow instead returns to the requester, and a portion of the
provider's collateral is confiscated and split between the party that surfaced the fraud and a
shared treasury, so that reporting fraud carries a direct economic reward rather than relying on
altruism.

Collateral that a provider attempts to withdraw does not become safe from confiscation the moment
withdrawal is requested: we design an unbonding delay during which withdrawn stake remains
slashable, specifically to close the possibility that a provider who anticipates a challenge
could simply withdraw its collateral ahead of it. A slash that exceeds a provider's currently
available collateral is reported as such rather than silently capped and forgotten — an
under-collateralized provider is a fact the design surfaces, not one it launders into an
apparently complete recovery.

![Figure 3. The escrow state machine: OPEN -> AWAITING_VERIFICATION -> {SETTLED | SLASHED}, and OPEN -> REFUNDED on award timeout.](docs/figures/settlement_states.png)

*Figure 3. The escrow state machine: OPEN -> AWAITING_VERIFICATION -> {SETTLED | SLASHED}, and OPEN -> REFUNDED on award timeout.*


### 3.8 Targeted Invariants

The design targets, rather than proves, the following properties, and we are explicit about
which of them the architecture *enforces structurally* versus merely *incentivizes*:

- **Truthful bidding is the dominant strategy** for a provider in the market mechanism of §3.4 —
  this follows directly from the second-price rule and is a property of the mechanism's
  construction, not an empirical claim requiring measurement.
- **No value is created or destroyed by settlement** — every unit that enters escrow is
  accounted for on exactly one of the outcomes the state machine of §3.7 permits. This is a
  structural invariant the implementation is required to check on every run (§4), not merely an
  aspiration.
- **A cryptographically confirmed fraud is acted on with certainty and without a subjective
  judgment call**, while a judgment-based fraud finding is acted on only through the quorum and
  challenge-window process of §3.6–§3.7 — this is a structural separation the design enforces by
  construction.
- **Honest providers are rarely slashed, and fraud is detected with probability related to the
  audit sampling rate** — these are targeted properties, not proven ones. The distinction between
  a fraud proof (certain) and a validator verdict (a judgment call with an irreducible error
  rate) means the second half of this property is only as strong as the underlying judge, which
  is an empirical question the design does not resolve by construction and which Evaluation
  addresses directly.
- **Validator collusion and large-scale Sybil attack resistance** are explicitly *not* claimed
  as solved properties of this design (§3.2); they are named as open questions the staking
  mechanism narrows but does not close.

---

## 4. Implementation

### 4.1 Concrete Stack

The design of Section 3 is realized as five cooperating tracks sharing one wire contract
(§4.2), all in Python except settlement. Peer discovery and the market protocol run over a
peer-to-peer networking library implementing Kademlia distributed-hash-table routing and a
gossip-based publish/subscribe mesh (py-libp2p), with node identity carried by a secp256k1
keypair that doubles as an Ethereum-style wallet address. The inference stage is a thin
streaming HTTP client against a locally-hosted open-weight model runtime (Ollama), chosen
because it is the one runtime on the development hardware that exposes a token-level streaming
API and per-request timing counters — the two properties the design's inference contract (§3.5)
actually depends on; nothing about the design requires this specific runtime, and the client
library is small enough that a different runtime satisfying the same streaming contract is a
drop-in replacement. Verification runs the same or a different model through the same client,
plus HTTP clients for two hosted inference providers (Groq and OpenRouter) used only in the
judge-diversity experiment of §5.4. Settlement is four smart contracts written in Solidity,
compiled and deployed to a local Ethereum-compatible development chain, with an in-repo
minimal-ownership and reentrancy-guard library rather than a third-party dependency, specifically
so the contract set has no build dependency beyond the chain toolchain itself. Model-weight
distribution runs against a real InterPlanetary File System (IPFS) daemon (kubo) reached over
its local HTTP API, with the client-side content-identifier computation reimplemented from the
daemon's own on-disk encoding rather than trusted from the daemon's response, which is the
property the weight-distribution design in Table 4.1 depends on. Every wire message is a
schema-validated object that rejects an unrecognized field rather than silently accepting it,
and every message that carries economic consequence is signed and independently re-verified by
its recipient before it is acted on.

### 4.2 Design-versus-Prototype

Every component below is implemented against the same interface a production version would
occupy; a prototype-scale stand-in is invisible to every other component's code and is replaced
by reimplementing the two functions the interface actually exposes, never by rewiring the
system around it. Table 4.1 states this plainly, component by component, rather than leaving it
implicit.

**Table 1 — Design versus prototype: substitutions, production targets, and open limitations**

| Component | Prototype instantiation | Intended production target | Limitation left open |
|---|---|---|---|
| Settlement chain | Solidity contracts on a local, single-node Ethereum-compatible development chain | An Arbitrum Stylus (Rust/WASM) rollup | Gas semantics, access control, the escrow state machine, and the 80/20 slash split are real and measured; there is no public fee market, no mempool contention, and no finality delay. Whether a fraud proof can be censored under contested block space is unaddressed. |
| Data availability | A local, namespaced, Merkle-committed append-only blob store implementing the identical binding scheme (domain-separated leaf/node hashing) that the on-chain fraud-proof verifier checks | Celestia, with data-availability sampling over a decentralized validator set | The *binding* property — a provider cannot show a verifier one output and the chain another — is real, tested, and exercised on chain. The *availability* guarantee is absent: a provider that deletes its own blob makes the audit impossible rather than making itself provably guilty. Migration requires reimplementing two functions (`submit_blob`, `get_blob`) against the same interface. |
| Inference runtime | A CPU-only, single-node streaming model runtime serving one 2-billion-parameter open-weight model | A GPU-accelerated serving stack (e.g. paged-attention batching) across a discrete-GPU hardware tier | All latency, throughput, and cost figures characterize the CPU tier only; nothing here bears on GPU-tier serving economics, which the network's design also targets but this evaluation cannot exercise for lack of the hardware. |
| Verification judge | A single small (2B-parameter) locally-hosted model, or — in one dedicated experiment — a panel of larger, hosted models reached over a public API | A validator pool of independently-operated, adequately-capable models, ideally locally hostable at the edge tier | The deployable configuration fails on the two adversary-favored fraud strategies measured (§5.4); the configuration that closes the gap uses judges not deployable on the CPU tier this network targets, so no configuration measured here is simultaneously edge-hostable and fully capable. |
| Provider economic stake | Test-denomination collateral on the local development chain, with no real transferable value | Real, market-priced collateral on a production deployment | The staking, unbonding, and slashing *mechanics* are exercised and value-conserving by construction; no conclusion follows about whether any specific stake level or slash share is an adequate deterrent, because no participant in any measured run has anything real to lose. |

Two further, narrower substitutions are worth naming because they affect how specific numbers in
Section 5 should be read rather than because they weaken the mechanism itself. First, the
fraud-injection corruptions used to manufacture dishonest outputs in the verification experiment
are template-based rather than adversarially generated; a competent adversary writing fluent
false prose is a different and untested threat (§6.2). Second, the "honest" control answers
against which false-positive rates are measured are themselves language-model outputs rather
than human-verified ground truth, which bounds every precision and false-positive figure reported
in Section 5 as a confound rather than a clean measurement (§6.2 elaborates).

### 4.3 Deployment Topology

Every experiment reported in Section 5 ran on a single physical development host — sixteen
logical cores, approximately 31 GB of RAM, no hardware accelerator — with one partial exception.
The single-machine auction experiment runs each simulated network node as a separate operating-
system process communicating over the loopback network interface. A second, later auction
experiment instead runs each node inside its own Linux container with its own network namespace
and its own address on a software bridge, which removes the shared loopback interface and makes
it possible to attach a controllable one-way link delay to each container independently. Neither
arrangement is a multi-machine deployment: both share one kernel, and the container topology in
particular has no physical network interface, no switch, and no wide-area path — what it adds is
solely the ability to set and read back a per-link delay, which is what turns "the auction clears"
into a measurable latency response (§5.3).

Two non-obvious build issues are resolved by the project's setup script rather than left for an
operator to debug independently: the peer-to-peer networking library's elliptic-curve dependency
ships no prebuilt binary wheel for the development platform's Python version and requires a
system cryptography header the script fetches without elevated privileges, and the resulting
build then links against the wrong variant of that library by default, which the script also
corrects. Both are stated here because they are a genuine barrier to reproduction that is not
otherwise visible in the code, and the project provides a `--check` mode specifically to verify
an existing environment against them without re-running the full setup.

### 4.4 Reproducibility Infrastructure

Every experiment run writes to its own timestamped, never-overwritten output directory containing
a full configuration snapshot, the git commit hash and dirty-tree status, hostname, and
interpreter version, so that any number quoted later can be traced back to the exact code and
configuration that produced it — this is enforced by a shared run-logging component that every
experiment uses rather than each experiment implementing its own output convention. Every result
row records the backend and model that actually served it, read back from the client rather than
assumed from a command-line argument, because the two can differ whenever a name is aliased or a
request is silently substituted. A component that cannot be reached, or that returns an
unparseable or incomplete result, is required to raise a named exception or record a distinct
`error` outcome rather than falling back to a default that would look like a genuine measurement;
this rule is applied uniformly across the inference client, the verification judge, and the
model-weight resolver. Every dropped case — a discarded warm-up trial, a corrupted item, a
paraphrase that failed a validity guard — is counted and reasoned in the run's manifest, so that
a rate computed from N items is never silently computed from fewer than N.

---

## 5. Evaluation

### 5.1 Evaluation Goals and Setup

Five questions ground this section, one per experiment plus one supporting measurement: does an
edge node meet a sub-second time-to-first-token target, and what does the warm/cold distinction
cost (§5.2)? How does the sealed-bid auction's clearing behavior change with network size and
with injected link latency (§5.3)? How reliably does the verification judge separate honest from
fraudulent output, and does that reliability depend on the judge's model family or its capability
(§5.4)? What does content-addressed model-weight distribution cost, and does it actually reject
tampering (§5.5)? And what does a verified inference cost end to end, including the settlement
layer's own overhead (§5.6)?

Every number below is quoted from a specific, timestamped run directory under `docs/results/`
carrying its own configuration snapshot and git commit hash (§4.4); the run identifier is given
at first use in each subsection so a reader can trace the figure to the file it came from
(`docs/paper-factsheet.md` §6 quotes the same figures against the same files independently). The
model served for inference and judging throughout the four primary experiments is a
2-billion-parameter open-weight instruction model; the judge-diversity experiment of §5.4
additionally exercises four larger, hosted models. All runs executed on the single CPU-only
development host described in §4.3, except the auction-under-latency experiment, which used the
container topology of §4.3.

### 5.2 Latency

Twenty warm trials and five matched cold/warm evict-and-reload pairs were measured against a
fixed 64-token-ceiling prompt (`inference-benchmark-20260902T120811Z`). Warm time-to-first-token
had a mean of 609.6 ms, a median of 587.9 ms, and a 95th percentile of 723.6 ms, with a standard
deviation of 75.7 ms; **all twenty of twenty warm trials completed under one second.** Sustained
throughput averaged 12.86 tokens/second (s.d. 0.64). Paired against the same prompt, cold
time-to-first-token averaged 7,963.8 ms versus a paired warm mean of 653.7 ms — a **12.18×**
cold-to-warm ratio and a mean penalty of 7,310 ms, with the cold side tightly clustered (s.d.
237 ms), consistent with a penalty dominated by a fixed cost rather than scheduling noise. The
runtime's own reported load-time breakdown attributes the great majority of that penalty to
model loading rather than to generation (mean cold load 7,418 ms vs. mean warm load 568 ms):
**the cold-start cost is loading, not inference**, which is the direct empirical justification
for pricing warmth as a near-eligibility condition in the auction score (§3.4) rather than as a
smoothly tradeable quality dimension — over this latency range, a cold node is not a slower
node, it is a node that cannot serve the request in budget at all. A hosted-API baseline was
configured to be skipped rather than estimated when no comparison endpoint was available in this
run, per the evaluation protocol's rule that a missing baseline is reported as absent, never
approximated.

![Figure 4. Time to first token, warm versus cold start, on a logarithmic time axis.](docs/figures/fig_ttft.png)

*Figure 4. Time to first token, warm versus cold start, on a logarithmic time axis.*


### 5.3 Auction Convergence

**Single-machine, multi-process.** Fifty-seven auctions (nineteen per node count) were run at
three, four, and five simulated nodes, zero failures
(`exp2-auction-convergence-summary-20260902T110609Z`). Broadcast-to-award time was flat at
2,007–2,008 ms across all three node counts, because it is pinned by the fixed two-second bid
collection window and is therefore a property of the configured constant, not a scaling
measurement — reporting it as evidence of scaling would repeat exactly the error the protocol
warns against for a warm-only latency figure. The quantities that do carry scaling information
are the un-pinned bid-arrival times: first-bid latency rose from 16.9 ms (three nodes) to 22.3 ms
(four) before falling to 21.1 ms (five), and last-bid latency rose monotonically from 21.3 ms to
32.6 ms to 36.7 ms. The gap between first and last bid widens with node count (4.4, 10.3, 15.6
ms), the expected signature of bid collection: with more bidders, the tail arrival grows while
the fastest arrival is comparatively insensitive to the number of others. With standard
deviations comparable in size to the differences between conditions, three node counts cannot
establish a growth law; what can be said is that the whole of bid collection completes one to two
orders of magnitude below the two-second window at this scale, so the window is not close to
binding here — whether that remains true at fifty or five hundred nodes is not measured.

**Container topology, controllable link latency.** Because every process in the single-machine
runs shares one loopback interface, there is no per-peer link to attach a delay to, and the
latency-response measurement below could not have been taken on that topology at all — not
because it would have been inaccurate, but because the independent variable did not exist.
Running each node in its own container with its own network-namespace address removes exactly
that shortcut. At injected one-way per-link delays of 0, 10, 25, and 50 ms
(`exp2-swarm-containers-20260902T170808Z` and the three `exp2-swarm-netem-*` runs, seven auctions
total), first-bid arrival rose from 6.0 ms to 44.5, 71.0, and 114.0 ms, and last-bid arrival from
7.0 ms to 51.0, 73.5, and 117.5 ms. A least-squares fit gives roughly 2.06 ms of added first-bid
delay per millisecond of injected one-way delay — close to, but consistently above, the reference
line for a bare request/response round trip (one crossing each way), which is consistent with a
mesh in which some bids are relayed through a third peer and some are not; a fixed three-crossing
model overpredicts the observed values just as clearly as the two-crossing reference
underpredicts them, so the true behavior sits between the two and no fixed-hop-count model fits
exactly. This remains one kernel on one machine — no physical interface, no switch, no wide-area
path, no packet loss, no jitter — and is not described as a network deployment anywhere in this
paper; what it establishes is that the mesh's response to a controlled, symmetric, per-link delay
is close to linear and roughly consistent with the protocol's expected message-forwarding
pattern, which a shared-loopback topology could not have shown at all.

![Figure 5. Bid arrival and auction close against network size (single-machine, multi-process); the flat upper line is the fixed two-second bid window.](docs/figures/fig_auction.png)

*Figure 5. Bid arrival and auction close against network size (single-machine, multi-process); the flat upper line is the fixed two-second bid window.*


![Figure 6. Auction timing against injected one-way link latency in the container topology, with the request/response reference line.](docs/figures/fig_swarm.png)

*Figure 6. Auction timing against injected one-way link latency in the container topology, with the request/response reference line.*


### 5.4 Verification Accuracy

**Method.** Twenty TruthfulQA questions were each presented in five conditions — one honest
answer generated by the actual local inference node, and four independently-corrupted answers
produced by fixed corruption strategies (entity/number fabrication, semantic negation, swapping
in a plausible-but-incorrect answer, and substituting an off-topic answer) — for 100 trials: 80
fraudulent, 20 honest (`verification-20260902T121801Z`). Every answer was written to the
data-availability layer and its inclusion proof checked before any judge call; all 100 passed
that check, so every result below is a semantic judging outcome, not a data-mismatch fraud proof.

**Precision and the false-positive rate.** The judge failed **none** of the twenty honest
answers: precision 100%, false-positive rate 0%, mean honest score 4.95/5. This is the corrected
figure superseding an earlier, unpublished run that had reported an alarming 75% false-positive
rate; that earlier figure is now understood to have been a fault in the honest-answer *generator*
(a separate, weaker hosted model whose own answers were often factually wrong) rather than in the
judge, which was in fact correctly failing bad answers that had been mislabeled honest by
construction. With the honest condition corrected to use the network's own local generator, the
same judge model, rubric, and threshold produce zero false positives. We flag the generalizable
lesson: **an LLM-as-a-Judge evaluation's honest control is itself a model output, and a measured
false-positive rate is confounded with that control's own error rate unless the control is
independently verified** — a confound that, as later paragraphs of this subsection show, recurs
in milder form even after this correction.

**Recall, and where it collapses.** Overall recall was 65% (52 of 80 caught), but this single
number conceals a sharply bimodal result by strategy: 100% on off-topic substitution, 95% on
fabricated entities, versus **35%** on plausible-incorrect substitution and **30%** on negation.
The mean-score evidence rules out "near-threshold hesitation" as an explanation: the two hard
strategies scored 3.80 and 4.05 on a 1–5 scale against a pass threshold of 3 — a full point above
the line, close to the 4.95 mean the same judge gave genuinely honest answers, and the
score-distribution data shows the missed frauds clustering at the same top-of-scale marks as
honest answers rather than near the threshold. Raising the pass threshold from 3 to 5, the
maximum the rubric admits, recovers only 3 of 28 missed frauds while beginning to cost honest
providers. **The judge catches fraud with a lexical trace and fails on fraud that is
grammatically ordinary and topically appropriate but false** — detecting the latter requires the
judge to actually know the fact, not merely to notice something is off.



![Figure 7. Fraud detection by corruption strategy: precision, class-balanced precision, recall and F1.](docs/figures/fig_verification.png)

*Figure 7. Fraud detection by corruption strategy: precision, class-balanced precision, recall and F1.*


![Figure 8. Judge score distribution, honest against fraudulent answers, on the five-point rubric.](docs/figures/fig_score_dist.png)

*Figure 8. Judge score distribution, honest against fraudulent answers, on the five-point rubric.*

**Capability, not lineage.** The same 80 fraudulent strings were re-judged by four larger,
hosted models under three quorum rules (`judge-panel-20260902T173031Z`, 99 items after one
honest item was dropped as unrecoverable). Two of the four judge configurations returned complete
data: a ~13×-larger model from the *same* model family as the original judge recovered negation
recall to **100%** and incorrect-substitution recall to 95% (98% overall, zero errors); a model
from an *unrelated* family reached 100% and 90% respectively (96% overall, zero errors). This
directly tests, and refutes, a hypothesis this evaluation initially favored — that a same-family
judge would inherit the fraud-generating model's own misconceptions and so a larger same-family
judge would fail similarly. It did not: **holding family constant and raising capability
eliminated the blind spot completely, and an unrelated-family judge of comparable capability
closed the same gap**, so capability, not lineage, is the variable that determined the original
failure. (The other two panel members returned usable data on fewer than 20% of items, exhausted
by free-tier API rate limits within minutes; their apparent perfect scores are explicitly
**not** reported as results — see §6.2.)

**Quorum.** Under a majority-vote rule across all four panel members (including the two
mostly-erroring ones), the panel reached 97% recall at a **7%** false-positive rate and 94%
class-balanced precision — a substantial improvement over either complete member's own
false-positive rate (26% and 16% individually) at essentially unchanged recall. This is direct
empirical support for aggregating multiple validators under a quorum rule rather than deploying
a single judge, though the result is qualified by the same two members' unreliability: many of
the panel's decisions were effectively taken by two voters rather than four (§6.2). Judge
self-consistency under paraphrase was separately measured with the original small judge on 8
questions (30 judgements): the verdict flipped on 25% of answers when the same claim was
reworded, with individual scores spanning the full 1–5 scale on the same underlying claim in one
case — evidence that instability is real at this judge capability, though the sample is too
small to size it precisely, and it has not been re-measured with a capable judge.

![Figure 9. Recall by corruption strategy for each individual judge and for each quorum rule, with each configuration's honest false-positive rate carried in the legend.](docs/figures/fig_judge_panel.png)

*Figure 9. Recall by corruption strategy for each individual judge and for each quorum rule, with each configuration's honest false-positive rate carried in the legend.*


![Figure 10. Judge self-consistency under paraphrase: score spread per answer, with the two answers whose verdict flipped marked.](docs/figures/fig_paraphrase.png)

*Figure 10. Judge self-consistency under paraphrase: score spread per answer, with the two answers whose verdict flipped marked.*


### 5.5 Content-Addressed Weight Distribution

Five synthetic artefacts (64 KiB–48 MiB) were published to a real IPFS daemon and fetched back
through the network's local cache (`weights-20260902T170213Z`). Cold fetch time rose sub-linearly
with size (6.6 ms at 64 KiB to 317.5 ms at 48 MiB; throughput 9.9–158.5 MB/s), while warm
(cache-hit) fetch stayed under ~1.5 ms regardless of size, for cache speedups of **12.3×–895.8×**.
On every fetch, the content identifier was recomputed independently from the received bytes
rather than trusted from the daemon's own claim, and all five artefacts verified. Least-recently-
used cache eviction was exercised under a budget deliberately smaller than the working set and
verified to evict exactly the correct entry when forced to choose one. Three tamper cases — a
store serving a substituted artefact, a single bit flipped in a cached file after verification,
and a corrupted cache reaching the resolver — were each rejected with a named exception (or, for
the in-place bit flip, a boolean re-verification failure), against an honest control accepted
through the identical code path: **a node can accept model weights from an untrusted peer and
still know whether it received what it asked for**, without trusting the party that served them.
Two limits travel with this result: the artefacts are synthetic random-byte files rather than
real model weights, so the timings measure transfer and hashing, not deserialization or runtime
load; and the daemon is local, reached over loopback, so the cold-fetch figures measure
client-side retrieval and verification cost, not wide-area transfer.

### 5.6 Cost and Settlement

Three settlements were driven through a locally-deployed four-contract settlement layer on a
development chain (`settlement-onchain-20260902T120752Z`): an honest job settled after its
challenge window elapsed; a job slashed via the trustless data-mismatch fraud proof; and a job
slashed via a validator's oracle verdict — all three resolved to the correct state, the 80/20
validator/treasury slash split was exact in both slashed cases, and value conservation held
across every account checked. Gas per operation ranged from 32,317 (withdrawal) to 221,353 (the
trustless fraud-proof path — the single most expensive operation measured, and, on this evidence,
the best value in the system: it requires no model and produces certainty rather than a 65%-
recall probability). Composing the measured clearing price, token count, and amortized
verification cost (`cost-20260902T123714Z`), the grid model costs **$0.00115** per 1,000
delivered tokens (of which verification is 4.8%) against a **$0.002** published centralized-API
list rate — a **0.576×** ratio. We flag this comparison's honesty condition explicitly, as the
figures themselves already do: the network's internal unit has no market price, so the dollar
comparison is a cost *model* at a stated notional conversion rate, not an observed market price;
the token-denominated and gas-denominated figures are the actual measurements, and only those are
load-bearing.

![Figure 11. Gas cost per settlement operation.](docs/figures/fig_gas.png)

*Figure 11. Gas cost per settlement operation.*


![Figure 12. Cost per 1,000 delivered tokens: this work versus a centralized-API baseline.](docs/figures/fig_cost.png)

*Figure 12. Cost per 1,000 delivered tokens: this work versus a centralized-API baseline.*


---

## 6. Discussion and Limitations

### 6.1 What the Prototype Convincingly Shows, and What It Does Not

The prototype convincingly shows that the five-stage pipeline of Section 3 holds together as a
running system rather than as a set of separately-plausible components: a real signed job travels
from broadcast, through a real gossip-mesh auction that clears correctly and is individually
rational on every measured award, through a real streaming inference call whose warm latency
clears a sub-second target, through a real content-addressed weight fetch that a node can verify
without trusting its source, through a real Merkle-committed data-availability check, to a real
on-chain escrow that resolves correctly down all three of its state-machine paths and conserves
value exactly. That composition — and not any individual mechanism, all of which are prior art
(§2) — is what this evaluation is actually able to support at small scale.

What it does not show is comparably important to state without softening. Every network
measurement, including the container-topology auction under injected latency, ran on one physical
kernel; nothing here establishes behavior across real wide-area links, NAT traversal, packet
loss, or peer churn. Three and five node counts cannot establish a scaling law for the auction.
The settlement layer's gas figures are real but priced against no fee market and no contention.
The staked collateral in every settlement run is a test value with no real cost to lose, so no
conclusion follows about whether the specific 80/20 slash split or any given stake level is an
adequate deterrent against a rational, resourced adversary — the mechanism's incentive-
compatibility argument (§3.4, §3.7) is a property of its construction, not something this
evaluation measures empirically. And, most consequentially for the system's central claim, the
verification layer as actually deployable on the network's target hardware tier (a small,
locally-hostable judge) is the configuration measured to fail on exactly the two fraud strategies
-- negation and plausible substitution — that a rational adversary would choose specifically
because they evade detection (§5.4); the configuration that closes that gap uses judges not
deployable on that same hardware tier. Table 1 (§4.2) already states, component by component, the
specific limitation each substitution leaves open; none of those limitations is closed by this
evaluation, and the verification-judge row is the one this section returns to.

### 6.2 Judge Capability, Judge Diversity, and the Honest-Control Confound

The most important qualitative finding of this evaluation is not the size of any single number
but the *shape* of the verification failure and the correction to an initial, plausible-sounding
explanation of it. It would have been easy — and the evaluation initially favored this reading --
to conclude that a judge sharing a model family with the fraud-generating model would share its
blind spots, and that model *diversity* was therefore the fix. The panel experiment (§5.4) tested
this directly and refutes it: a same-family judge at roughly thirteen times the parameter count
closed the recall gap completely, and so did an unrelated-family judge of comparable capability.
**The variable that mattered was capability, not lineage.** We report this as a refuted
hypothesis rather than quietly dropping it, because a prediction that was tested and found wrong
is worth more to the next iteration of this work than an untested intuition left standing — and
because it changes the design's practical recommendation from "diversify judge model families" to
the narrower and more testable "ensure deployed judges clear a capability threshold for parsing
negation and plausible-substitution fraud," a threshold this evaluation locates only qualitatively
(between 2B and ~27B parameters on the models tested) rather than precisely.

A second, methodologically general finding travels alongside the first and is easy to
under-weight because it is not about the judge at all: **every false-positive rate reported in
this evaluation is confounded by the fallibility of whichever model generated the "honest"
control answers.** This is not a hypothetical concern — roughly half of the false positives
recorded against the larger, more capable judges in the panel run were, on inspection, cases
where the honest generator itself produced a fabricated or mistaken claim that the judge was
correct to fail. This is the same failure mode, in milder form, that produced an alarming and
ultimately misdiagnosed 75%-false-positive result in an earlier, superseded run of this same
verification pipeline (§5.4) — and the fact that a diagnosed failure mode recurred, even after
being named, is itself the strongest available argument for a human-adjudicated honest control
(§7.2) rather than continuing to generate one.

**Judge self-consistency under paraphrase compounds both findings rather than sitting beside
them.** A 25% verdict-flip rate under semantically-preserving rewording, measured with the small
deployed judge, means that even where recall is adequate on average, a single verdict is not a
stable basis for an irreversible economic action — a provider could be slashed for one phrasing of
a correct answer and spared for another, and neither the provider nor the network can determine
after the fact which verdict was "correct." This sharpens rather than merely adds to the case for
quorum-based verification (§3.6): a single judge's instability is a second, independent argument
for aggregation beyond its accuracy alone, and it has not yet been re-measured with a capable
judge, so whether capability also resolves instability is a genuinely open question.

### 6.3 Deployment and Governance Considerations

Automated, on-chain slashing driven by an LLM's judgment carries a consequence that a purely
technical accuracy figure understates: a false FAIL verdict is not a misclassification recorded in
a spreadsheet, it is an irreversible transfer of real economic value away from a participant who
did nothing wrong. This evaluation's finding that a small judge is confidently — not hesitantly --
wrong on a specific, adversary-favored class of fraud (§5.4) means that the deployment decision of
*which judge, at what capability, under what quorum rule* is not a performance-tuning parameter to
be set once and left alone; it is closer to a safety-critical configuration choice that determines
the network's real economic fairness to honest participants, and it should be treated with the
same scrutiny given to the settlement contracts' own access control (§3.7), not left to whichever
judge happens to run cheaply on a given operator's hardware.

---

## 7. Conclusion and Future Work

### 7.1 Conclusion

This work asked whether five mechanisms studied separately in the decentralized-systems and
machine-learning-evaluation literatures — peer-to-peer discovery, an auction-based scheduling
market, streaming edge inference, sampled semantic and cryptographic output verification, and
staked on-chain settlement — can be composed into one job pipeline that runs end to end and is
measured rather than merely described. The evidence in Section 5 supports that they can: every
stage of the pipeline corresponds to code that executed against real cryptography, a real
peer-to-peer network stack, a real streaming inference runtime, a real content-addressed storage
daemon, and a real smart-contract chain, with every reported figure traceable to a timestamped,
configuration-snapshotted run directory. The prototype's own most important result, however, is
not a positive one: the verification layer, in the configuration actually deployable on the
network's target hardware tier, was measured to fail on exactly the fraud strategies a rational
adversary would prefer, and the evaluation's own initial explanation for that failure — model
lineage rather than capability — was tested and found wrong. Reporting that correction plainly,
rather than either hiding the failure or keeping the wrong explanation, is what makes this
evaluation useful to whoever builds on it next: it is achievable in a research-scale prototype
that a small, resource-constrained team can build, measure, and reason honestly about in a short
timeframe, while production deployment — across real networks, against a real fee market, and
with a verification layer capable enough to be trustworthy on hardware the network can actually
recruit — remains substantial, specifically enumerated future work.

### 7.2 Future Work

Each item below is derived directly from a limitation named in Section 6, not from a general
aspiration to do more.

1. **A genuine multi-machine deployment.** The largest unaddressed threat to validity (§6.1):
   every network measurement in this evaluation, including the container-topology auction, shares
   one kernel. Running nodes on physically distinct hosts across real networks would test the
   discovery layer, the gossip mesh, and the fixed bid window against real wide-area latency, NAT
   traversal, clock skew, and peer churn — none of which a single-host topology can produce
   regardless of how much delay is injected into it.

2. **A capability-adequate, edge-hostable verification judge.** The central open problem this
   evaluation surfaces (§5.4, §6.2): the judge configuration that closes the recall gap is not
   deployable on the hardware tier the network targets. Locating the actual capability threshold
   at which negation and plausible-substitution fraud become reliably detectable — by sweeping
   model size within one family on locally-hostable hardware, rather than comparing families on
   hosted APIs — is a direct, answerable next experiment, as is repeating it with a full,
   rate-limit-free panel (two of four members in this evaluation's panel run produced no usable
   data at all) and a larger paraphrase self-consistency sample.

3. **A human-adjudicated honest control set.** Every precision and false-positive figure in this
   evaluation is confounded by the fallibility of a model-generated honest answer (§6.2). A
   modest, independently human-verified control set (tens to low hundreds of items) is the
   precondition for any clean measurement of judge precision, including a repeat of the
   judge-diversity comparison in §5.4.

4. **The production substitutions of Table 1**, each behind the interface it already
   occupies: a Celestia light client in place of the local data-availability store; deployment to
   a real rollup in place of the local development chain, to subject the same contract logic to a
   real fee market and finality; a GPU-accelerated serving path benchmarked on discrete-GPU
   hardware, to characterize the serving economics of the tier this evaluation could not exercise;
   and weight distribution exercised against a public IPFS swarm with real model artefacts rather
   than synthetic files.

5. **An adaptive adversary.** No experiment in this evaluation adapts its fraud strategy to what
   the deployed judge actually catches (§6.1); a red-team exercise in which one party is tasked
   with maximizing undetected fraud against the deployed sampler and judge is the single
   experiment most likely to change confidence in the system's real-world security, and none has
   been run.

6. **Zero-knowledge verification of model execution**, placed last because it is presently
   impractical rather than because it is unimportant: it would make the judge-capability
   limitation of §5.4 structurally moot, since a cryptographic proof holds no opinions, and the
   architecture's fraud-proof interface already accepts a validity proof in place of a mismatch
   proof without redesign (§2). Proving transformer inference remains orders of magnitude more
   expensive than the inference itself, which is presently irreconcilable with a sub-second
   time-to-first-token target.

---

## Acknowledgments

We thank Dr. Savita Choudhary, Professor & Head, Department of Computer Science and Engineering
(IoT, Cyber-Security and Blockchain Technology), Sir M. Visvesvaraya Institute of Technology, for
guidance throughout this project's design, implementation, and evaluation.

---

## References

[3] L. Bousfield et al., "Arbitrum Nitro: A second-generation optimistic rollup," Offchain Labs,
Inc., Whitepaper, Aug. 2022.

[4b] M. Al-Bassam, A. Sonnino, V. Buterin, and I. Khoffi, "Fraud and data availability proofs:
Detecting invalid blocks in light clients," in *Financial Cryptography and Data Security (FC
2021)*, LNCS vol. 12675, Springer, 2021, pp. 279–298. doi: 10.1007/978–3–662–64331–0_15.

[5a] M. Ball, A. Rosen, M. Sabin, and P. N. Vasudevan, "Proofs of useful work," IACR ePrint
2017/203, 2017.

[5b] M. Fitzi, A. Kiayias, G. Panagiotakos, and A. Russell, "Ofelimos: Combinatorial optimization
via proof-of-useful-work," in *CRYPTO 2022*, LNCS vol. 13508, Springer, 2022, pp. 339–369.

[5c] H. Jia et al., "Proof-of-Learning: Definitions and practice," in *2021 IEEE Symposium on
Security and Privacy (SP)*, 2021, pp. 1039–1056. doi: 10.1109/SP40001.2021.00106.

[8] L. Zheng et al., "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena," in *NeurIPS 2023,
Datasets and Benchmarks Track*, 2023.

[9] S. Lin, J. Hilton, and O. Evans, "TruthfulQA: Measuring how models mimic human falsehoods,"
in *Proc. ACL 2022*, pp. 3214–3252. doi: 10.18653/v1/2022.acl-long.229.

[10] A. Borzunov et al., "Petals: Collaborative inference and fine-tuning of large models," in
*Proc. ACL 2023, System Demonstrations*, pp. 558–568.

[10b] A. Borzunov et al., "Distributed inference and fine-tuning of large language models over
the Internet," in *NeurIPS 2023*.

[11] W.-L. Chiang et al., "Chatbot Arena: An open platform for evaluating LLMs by human
preference," in *ICML 2024*, PMLR vol. 235, pp. 8359–8388.

[12] DGrid.AI, "DGrid AI: The decentralized AI inference network for open, low-cost &
community-powered AI," Litepaper, Jun. 2025. (Corporate litepaper, not peer-reviewed.)

[14] C. Tong et al., "Parallax: Efficient LLM inference service over decentralized environment,"
preprint, arXiv:2509.26182, Sep. 2025. (Preprint, not peer-reviewed.)

[15] Y. Yang et al., "Navigator: A decentralized scheduler for latency-sensitive AI workflows,"
in *2024 IEEE International Conference on Edge Computing and Communications (EDGE)*, 2024,
pp. 35–47.

[16] H. Liu et al., "PolyLink: A blockchain based decentralized edge AI platform for LLM
inference," in *2025 IEEE International Conference on Blockchain*, 2025, pp. 101–108.
doi: 10.1109/Blockchain67634.2025.00023.

[20] J. Teutsch and C. Reitwießner, "A scalable verification solution for blockchains," TrueBit
Whitepaper, Nov. 2017.

[21] Morpheus, Trinity, and Neo (pseudonymous), "Morpheus: A network for powering smart agents,"
Whitepaper, Sep. 2023.

[22] MorpheusAIs, *Morpheus-Lumerin-Node* [Computer software], 2024-.

[23] E. Lui and J. Sun, "Bittensor protocol: The Bitcoin in decentralized artificial
intelligence? A critical and empirical analysis," in *MARBLE 2025*, Springer, 2026, pp. 145–165.

[25] G. Osuri and A. Bozanich, "AKT: Akash network token & mining economics," Akash Network,
Whitepaper, Jan. 2020.

[26] Golem Factory GmbH, "The Golem project: Crowdfunding whitepaper," Nov. 2016.

[27] Gensyn AI Ltd., "Gensyn litepaper: A protocol for verifiable machine learning compute,"
2022.

