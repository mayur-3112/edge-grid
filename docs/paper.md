# The Edge Grid — Publication Draft

*Assembled from `docs/paper-factsheet.md` and `docs/report/` (chapters 1–9), which are the
authoritative, result-grounded sources for this draft. `docs/PAPER_DRAFT.md` is superseded
and is not used — see `docs/paper-factsheet.md` §7 for why.*

*Sections are drafted in dependency order (code-grounded sections first, Abstract and
Introduction last) per the drafting plan. This file currently contains Section 3 only.*

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
contract (Figure \ref{fig:architecture}). Fixing that contract is a design decision in its own
right: a track that produces or consumes one stage of the pipeline can be built, changed, and
tested independently of every other track, because the only thing tracks share is the shape of
the messages between them, never one another's internal state. Figure \ref{fig:sequence} traces
one job through the full pipeline, from broadcast to settlement.

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

### Coverage note

Drawn from `docs/paper-factsheet.md` §1.1–§1.5 (module summaries), §2 (wire schema), §3
(auction mechanism as implemented in `edgegrid/market.py`), §4 (verification flow in
`verification/validator.py` and `verification/panel.py`), and §5 (settlement state machine in
`edgegrid/ledger.py` and `contracts/contracts/*.sol`). No implementation-specific tool, library,
or language name appears above by design; those appear in Section 4 (Implementation), not yet
drafted. The threat-model subsection (§3.2) is written to match what the settlement and
verification mechanisms can structurally act on, per the fact sheet's own framing in §4–§5, and
explicitly declines to claim coverage of validator collusion at scale or Sybil resistance beyond
"costly," since the fact sheet's evaluation data (its §6, and `ch8_results.md`) does not contain
a dedicated experiment for either — a gap that should be named again, not silently dropped, when
Evaluation and Discussion are drafted. One judgment call: §3.6's description of validator
*diversity* draws on the fact sheet's `panel.py` summary (heterogeneous judges, `independent`/
`diverse` flags recorded rather than assumed) even though that mechanism is used in only one of
the experiment runs (§6.8, the judge-panel experiment) rather than in the main verification
result (§6.4); Implementation and Evaluation should be explicit about which verification path
(single-validator `ValidatorPool` vs. multi-model `JudgePanel`) produced which reported numbers,
since the fact sheet's own open question #9 flags the panel run as incomplete by its own
admission.
