# The Edge Grid: A Prototype for Decentralized, Verifiable Peer-to-Peer AI Inference

**Harshit Raj, Chetan Raghuvanshi, Keshav Narayan, Mayur Agarwal**
*Department of Computer Science and Engineering (IoT, Cyber-Security and Blockchain Technology)*
*Sir M. Visvesvaraya Institute of Technology, VTU Belagavi*

---

## Abstract

Centralized inference services concentrate pricing power, availability, and trust in a single
operator, and decentralized physical infrastructure networks have already shown that peer-to-peer
markets can sell connectivity, storage, and general compute at scale. Selling verifiable AI
inference the same way raises a problem those networks do not solve: an anonymous edge provider,
operating under a hard latency budget, has both the means and the incentive to return a
plausible-looking wrong answer, and naive redundancy or centralized scheduling cannot detect that
cheaply. We design a network in which a job is discovered and auctioned under a sealed-bid,
warm-start-aware second-price rule; executed by a streaming edge inference runtime; committed to a
Merkle-backed data-availability layer; and verified by a two-tier mechanism that separates a
trustless cryptographic fraud proof from a sampled, quorum-voted semantic judge, with staked
collateral slashed 80/20 between reporter and treasury on confirmed fraud. We build and measure a
small-scale reference implementation on commodity CPU hardware: warm time-to-first-token averaged
610 ms with a 12× cold-start penalty; a sealed-bid auction cleared correctly across 57 trials and
responded near-linearly to injected per-link network delay; content-addressed model weights were
fetched, independently re-verified, and correctly rejected under three tampering scenarios; and
on-chain settlement conserved value exactly across all three resolution paths. The verification
layer's measured behavior is the paper's central finding: the deployable, edge-hostable judge
configuration caught only 30–35% of two semantically subtle fraud strategies while scoring them
confidently rather than hesitantly, and a follow-up experiment shows this failure tracks judge
capability rather than the judge's relationship to the model it polices — refuting an explanation
this evaluation initially favored.

**Index Terms** — decentralized physical infrastructure networks, verifiable AI inference,
LLM-as-a-Judge, second-price auctions, optimistic fraud proofs, staked settlement.

---

## 1. Introduction

Centralized inference services have made large-model access cheap and reliable at the cost of
concentrating pricing power, infrastructure control, and data governance in a small number of
operators. Decentralized physical infrastructure networks (DePIN) already sell connectivity,
storage, and general compute competitively; none solve the narrower problem this paper addresses:
**selling AI inference, rather than raw compute, from anonymous edge providers under a latency
budget, with correctness verified cheaply enough that verification does not eat the economics it
protects.**

Redundant execution multiplies cost by the redundancy factor for every job, not just the audited
fraction — economically incompatible with a centralized provider's marginal cost. Centralized
verification reintroduces the single point of control the network exists to remove. Reputation
punishes a *pattern*, not a specific transaction. What is needed is a mechanism that prices a
latency budget at routing time, verifies a *sampled* fraction of jobs cheaply, and makes fraud's
economic consequence automatic — without a central adjudicator. Two properties make this harder
than verifying storage: a wrong answer need not look wrong (a fabricated fact or negated claim can
be fluent and indistinguishable from a correct one), and the party best positioned to catch a
subtle semantic error is itself a language model — so the verification layer inherits every
reliability question that attaches to language models generally, including that it can be
confidently wrong. Section 5.4 is a direct measurement of that risk.

We design the network around a strict separation between two kinds of verification claim.
A **cryptographic fraud proof** establishes with certainty that a provider served output
inconsistent with what it committed on chain — a fact about two hashes, needing no challenge
period. A **semantic verdict** is a judgment call about whether a faithfully-delivered output is
actually correct, rendered by one or more independent validator models under a quorum rule. This
lets the cheap, certain tier absorb one entire class of dishonesty for a fixed cost, and confines
the expensive, probabilistic tier to the dishonesty that genuinely requires judgment.

**Design contributions:** (1) a market protocol combining a sealed-bid second-price auction with a
warm-start handicap applied to ranking score but never to payment, since model residency measures
as a near-eligibility condition rather than a tradeable quality dimension under realistic latency
budgets; (2) a two-tier verification protocol with a three-valued verdict that never collapses
"unreachable" into either a pass or a fraud finding; (3) settlement as an escrow whose release
requires no privileged party online, with unbonding collateral that stays slashable to close a
front-running window.

**Prototype and evaluation contributions:** (1) a working, reproducible reference implementation
with every figure traceable to a configuration-snapshotted run; (2) measurement that the
deployable verification configuration is confidently wrong on two of four fraud strategies, and
that this tracks judge *capability* rather than *lineage* — refuting an explanation we initially
favored; (3) measurement that quorum aggregation reduces false positives well below individual
members at no cost to recall; (4) identification of a methodological confound — a model-generated
"honest" control is not reliably honest, and bounds every LLM-as-a-Judge precision figure — that
recurred in our own pipeline even after being diagnosed once.

---

## 2. Related Work

**DePIN networks** already sell compute, storage, or connectivity through token-incentivized
provider sets: Bittensor rewards ML work but allocates reward predominantly by stake rather than
output quality [23]; Akash [25] and Golem [26] auction general compute without an
inference-specific verification layer; Gensyn [27] targets verifiable ML compute oriented toward
training. None couples a latency-bounded auction to a sampled semantic verification layer with
staked slashing.

**Morpheus** [21, 22] is the closest architectural precedent — an Arbitrum-L2 marketplace where
providers bid and contracts match them to serve LLM inference. We differ in mechanism (a
warm-start-aware second-price auction with hard-constraint eligibility) and in adding a two-tier
verification layer outside Morpheus's stated scope. DGrid [12] and Parallax [14] describe
decentralized inference serving without the auction or verification layers evaluated here; both
are vendor litepaper/preprint, not peer-reviewed. **PolyLink** [16] is the closest refereed
academic parallel for settlement but does not evaluate an auction's response to injected latency
(§5.3).

Our data-availability layer implements Celestia's *binding* property [4b] against a local store,
explicitly declining its *availability* guarantee. Settlement's challenge-window structure follows
the fraud-proof lineage established for rollups [3, 4b, 20]. The LLM-as-a-Judge paradigm [8, 9, 11]
underlies our semantic tier; our contribution is a measured finding that judge *capability*, not
*lineage* relative to the policed model, determines whether subtle falsehoods are caught (§5.4) —
qualifying the intuition that same-family judges share blind spots. Petals [10, 10b] addresses
collaborative multi-peer inference, a complementary problem; Navigator [15] addresses decentralized
*scheduling* alone, without verification or settlement.

**No system surveyed combines** Kademlia discovery, a warm-start-aware second-price auction under
a hard latency budget, a two-tier verification pipeline separating a trustless fraud proof from a
capability-sensitive semantic check, and staked slashing, in one evaluated reference
implementation.

---

## 3. System Design

**System model.** Four roles — requesters, providers, validators, and a settlement layer — compose
a five-stage pipeline (discovery, market, inference, verification, settlement) communicating
through a fixed message contract (Figure 1).

**Threat model.** Cheating providers are split into two trust classes: an output inconsistent with
what was committed on chain is *provably* dishonest (a hash mismatch, checkable by anyone); an
output that is faithfully delivered but factually wrong requires a judge, whose error rate is an
irreducible cost. Colluding provider/validator pairs are mitigated structurally — deterministic,
unpredictable sampling means a provider cannot know which jobs will be audited — but validator
collusion at scale is **not** claimed solved. Sybil resistance is economic (identity multiplication
is cheap, but acting under an identity without stake is not) rather than identity-based, and its
adequacy against a well-resourced adversary is an open question. Latency misreporting is deferred
to the general verification path rather than a dedicated mechanism — a stated gap.

**Discovery and identity.** One secp256k1 keypair doubles as network identity and settlement
wallet. A slow-changing **capability record** (models served, tier, stake) propagates through a
DHT; a fast-changing **liveness signal** (reachability, which models are *loaded and ready*)
propagates directly between peers — kept separate because collapsing them forces a choice between
an expensive high-frequency DHT update or a stale signal an auction that clears in seconds cannot
use.

**Market protocol.** A sealed-bid, second-price *procurement* auction: providers bid the price they
want to be paid; the lowest eligible bid wins but is paid the second-lowest eligible price, making
truthful bidding dominant. Eligibility (tier, latency, price ceiling) is checked as hard
constraints before any price comparison. A warm-model discount is applied to *ranking score* only,
never to payment — preserving individual rationality — because over realistic latency budgets, a
cold provider's fixed load cost makes warmth closer to an eligibility condition than a tradeable
quality dimension (confirmed empirically in §5.2).

**Inference contract.** A narrow, runtime-agnostic contract: stream tokens, report true
time-to-first-token and a real per-request token count, and report cold/warm state. A provider
that cannot report these honestly cannot participate.

**Verification protocol.** Every job is committed to a content-addressed store. A
deterministic-but-unpredictable sample is audited: first a purely mechanical check (does the
committed output match the store, under a Merkle proof requiring no model or judgment — a
**fraud proof**, treated as certain); only if that passes does one or more validators render a
semantic verdict, required to be one of three values — pass, fail, or **unavailable** — so a judge
outage is never conflated with either an acquittal or a slash. Validator *diversity* is recorded as
a first-class property rather than assumed.

**Settlement.** A fixed challenge window follows commitment; uncontested release requires no
privileged party online. Confirmed fraud (proof or FAIL verdict) returns escrow to the requester
and slashes provider collateral 80/20 between reporter and treasury (Figure 3). Withdrawn
collateral stays slashable during an unbonding delay, closing a front-running window.

---

## 4. Implementation

Five cooperating tracks share one wire contract, in Python except settlement: discovery/market
over a Kademlia+GossipSub library (py-libp2p); inference against a streaming open-weight runtime
exposing token-level timing (Ollama, replaceable behind the same contract); settlement as four
Solidity contracts on a local development chain; weight distribution against a real IPFS daemon
with client-side content-identifier recomputation (never trusting the daemon's own claim). Every
message carrying economic consequence is signed and independently re-verified.

**Table 1 — Design versus prototype**

| Component | Prototype | Production target | Limitation left open |
|---|---|---|---|
| Settlement chain | Local single-node dev chain | Arbitrum Stylus rollup | No fee market, no finality delay; fraud-proof censorability untested |
| Data availability | Local Merkle-committed blob store | Celestia (DA sampling) | Binding is real; availability under deletion is not |
| Inference runtime | CPU-only, single 2B model | GPU-accelerated serving stack | No figure bears on GPU-tier economics |
| Verification judge | Small locally-hosted (deployed); larger hosted (measured separately) | Locally-hostable, adequately-capable pool | Deployable config fails adversary-favored strategies; the config that succeeds isn't edge-hostable |
| Economic stake | Test-denomination collateral | Real, market-priced | No conclusion about deterrent adequacy |

Every experiment ran on one host (16 cores, ~31 GB RAM, no accelerator); the auction was also run
in per-container network namespaces with `tc netem`-injected link delay, isolating a per-peer link
delay that a shared-loopback topology cannot express. Every run writes a timestamped,
never-overwritten directory with a full config snapshot and git SHA; a component that cannot be
reached raises a named exception rather than a plausible-looking default.

---

## 5. Evaluation

### 5.1 Latency

Warm time-to-first-token: mean 609.6 ms, median 587.9 ms, p95 723.6 ms (n=20; **all 20 under one
second**). Cold TTFT averaged 7,963.8 ms against a paired warm 653.7 ms — a **12.18×** ratio,
attributable almost entirely to model *load* time (7,418 ms cold vs. 568 ms warm), not generation
— the direct empirical justification for pricing warmth near eligibility rather than as a smooth
quality signal.

![Figure 1. Time to first token, warm versus cold start.](docs/figures/fig_ttft.png)

*Figure 1. Time to first token, warm versus cold start, on a logarithmic time axis.*

### 5.2 Auction Convergence

Fifty-seven single-machine auctions (3/4/5 nodes) cleared with zero failures; broadcast-to-award
was pinned at ~2,008 ms by the fixed bid window (not a scaling signal), while un-pinned bid-arrival
times carried the real signal: last-bid latency rose from 21.3→32.6→36.7 ms. Under container
network namespaces with injected one-way link delay (0/10/25/50 ms), first-bid arrival rose
6.0→44.5→71.0→114.0 ms, close to but above a bare round-trip reference line — roughly **2.06 ms of
added delay per ms injected**, consistent with GossipSub relaying through a third peer on some
messages.

### 5.3 Verification Accuracy

100 trials (80 fraudulent across four corruption strategies, 20 honest) against TruthfulQA. The
judge failed **none** of 20 honest answers (100% precision, 0% FPR) — a corrected result: an
earlier run had shown a 75% false-positive rate, since diagnosed as a fault in the honest-answer
*generator*, not the judge. **Overall recall was 65%, sharply bimodal**: 100%/95% on
off-topic/fabricated-entity fraud, versus **35%/30%** on plausible-substitution/negation fraud —
scored *confidently* (3.80–4.05/5 against a threshold of 3), not near-threshold, ruling out
"raise the threshold" as a fix.

![Figure 2. Fraud detection by corruption strategy.](docs/figures/fig_verification.png)

*Figure 2. Fraud detection by corruption strategy: precision, recall, and F1.*

**Capability, not lineage.** Re-judging the same 80 strings with larger hosted models: a
~13×-larger *same-family* judge recovered negation recall to 100% (98% overall); an *unrelated*
family reached 100%/90% (96% overall) — refuting our own initial hypothesis that lineage, not
capability, explained the gap. Under majority-vote quorum across all four panel members, recall
reached 97% at a **7%** false-positive rate versus 26%/16% for individual complete members —
direct support for quorum aggregation, qualified by two of four members being rate-limited into
near-uselessness. Judge self-consistency under paraphrase (small judge, 8 questions): a **25%**
verdict-flip rate on reworded claims.

![Figure 3. Judge panel recall by strategy and quorum rule.](docs/figures/fig_judge_panel.png)

*Figure 3. Recall by corruption strategy for each judge and quorum rule.*

### 5.4 Weight Distribution and Cost

Five artefacts (64 KiB–48 MiB) via a real IPFS daemon: cold fetch 6.6–317.5 ms, warm (cached)
under ~1.5 ms — speedups of **12.3×–895.8×** — with the content identifier independently
recomputed on every fetch and three tampering scenarios correctly rejected against one honest
control accepted. Three on-chain settlements (honest-release, fraud-proof-slash,
verdict-slash) all resolved correctly with an exact 80/20 split and exact value conservation; gas
ranged 32,317 (withdrawal) to 221,353 (the trustless fraud-proof — the best value in the system,
since it requires no model). Composed cost: **$0.00115** per 1,000 tokens (verification 4.8% of
that) against a **$0.002** centralized baseline — a notional-rate cost *model*, not a market
observation.

---

## 6. Discussion

The prototype's composition holds together end to end at small scale — every stage executed
against real cryptography, a real P2P stack, a real streaming runtime, and a real chain — but
every network measurement shares one physical kernel, three/five node counts cannot establish a
scaling law, and staked collateral in every run is a test value with nothing real to lose.

The **central finding** is not a number but a corrected explanation: it would have been easy to
conclude that a same-family judge inherits the fraud-generating model's blind spots, making
diversity the fix. The panel experiment refutes this — capability, not lineage, closed the gap. A
second, general finding travels alongside it: **every false-positive rate in this evaluation is
confounded by the fallibility of whichever model generated the "honest" control**, a milder
recurrence of the exact failure that produced the earlier 75%-false-positive result. Judge
self-consistency under paraphrase compounds both: a single verdict is not a stable basis for an
irreversible slash. Automated, on-chain slashing driven by an LLM's judgment therefore makes *which
judge, at what capability, under what quorum rule* a safety-critical configuration choice, not a
tuning parameter.

---

## 7. Conclusion and Future Work

Five mechanisms studied separately in prior literature — P2P discovery, an auction market,
streaming edge inference, two-tier verification, and staked settlement — compose into one
measured, end-to-end pipeline. The prototype's most useful result is a refuted hypothesis: the
verification layer's failure on adversary-favored fraud tracks judge capability, not lineage,
correcting our own initial explanation rather than confirming it.

**Future work**, each tied to a named limitation: (1) a genuine multi-machine deployment — the
largest unaddressed threat, since every network measurement here shares one kernel; (2) a
capability-adequate, *edge-hostable* judge, since the configuration that closes the recall gap is
not deployable on the hardware tier this network targets; (3) a human-adjudicated honest control
set, the precondition for any clean judge-precision measurement; (4) the production substitutions
of Table 1 — Celestia, a real rollup, GPU-accelerated serving, a public IPFS swarm; (5) an adaptive
adversary that reallocates toward undetected fraud strategies; (6) zero-knowledge verification of
model execution, which would make judge capability moot but remains orders of magnitude too
expensive for a sub-second latency target today.

---

## References

[3] L. Bousfield et al., "Arbitrum Nitro: A second-generation optimistic rollup," Offchain Labs,
Whitepaper, 2022.
[4b] M. Al-Bassam et al., "Fraud and data availability proofs: Detecting invalid blocks in light
clients," *FC 2021*, LNCS 12675, pp. 279–298.
[8] L. Zheng et al., "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena," *NeurIPS 2023*.
[9] S. Lin et al., "TruthfulQA: Measuring how models mimic human falsehoods," *ACL 2022*,
pp. 3214–3252.
[10] A. Borzunov et al., "Petals: Collaborative inference and fine-tuning of large models,"
*ACL 2023 Demos*, pp. 558–568.
[11] W.-L. Chiang et al., "Chatbot Arena: An open platform for evaluating LLMs by human
preference," *ICML 2024*, PMLR 235.
[12] DGrid.AI, "DGrid AI: The decentralized AI inference network," Litepaper, 2025.
[14] C. Tong et al., "Parallax: Efficient LLM inference service over decentralized environment,"
arXiv:2509.26182, 2025.
[15] Y. Yang et al., "Navigator: A decentralized scheduler for latency-sensitive AI workflows,"
*IEEE EDGE 2024*, pp. 35–47.
[16] H. Liu et al., "PolyLink: A blockchain based decentralized edge AI platform for LLM
inference," *IEEE Blockchain 2025*, pp. 101–108.
[20] J. Teutsch and C. Reitwießner, "A scalable verification solution for blockchains," TrueBit
Whitepaper, 2017.
[21] Morpheus, Trinity, Neo, "Morpheus: A network for powering smart agents," Whitepaper, 2023.
[22] MorpheusAIs, *Morpheus-Lumerin-Node* [Software], 2024–.
[23] E. Lui and J. Sun, "Bittensor protocol: The Bitcoin in decentralized AI?" *MARBLE 2025*,
Springer, pp. 145–165.
[25] G. Osuri and A. Bozanich, "AKT: Akash network token & mining economics," Whitepaper, 2020.
[26] Golem Factory GmbH, "The Golem project: Crowdfunding whitepaper," 2016.
[27] Gensyn AI Ltd., "Gensyn litepaper," 2022.
