# Chapter 9

# CONCLUSION AND FUTURE WORK

## 9.1 Conclusion

This project set out to determine whether the five mechanisms that the literature of decentralised
machine learning studies separately — peer-to-peer discovery, a market protocol for scheduling,
an edge inference runtime, verifiable output commitments, and blockchain settlement with staked
collateral — can be composed into a single job pipeline that runs end to end, and whether that
composite can be measured rather than merely described. The answer established by Chapter 8 is
that it can. A request published to a GossipSub task mempool is auctioned under a sealed-bid
second-price rule among real provider processes, executed by a streaming open-weight runtime on
commodity hardware whose weights are fetched by content address and verified against a recomputed
digest, committed as a namespaced blob whose Merkle root is posted on chain, sampled for audit by
a keyed hash of the job identifier, judged against a five-point rubric by a validator that returns
pass, fail or error, and finally settled or slashed by a Solidity escrow contract whose state
machine rejects every illegal transition. Every stage of that sentence corresponds to code that
was executed, and the latency, auction, weight-distribution, verification, settlement and cost
stages each have a timestamped run directory under `docs/results/` carrying its own configuration
snapshot and commit hash. Two clauses rest on a different kind of evidence and are named as such:
the audit sampler ran at a rate of one in the measurement harness, since the harness exists to
measure the judge rather than the sampler, and the escrow contract's rejection of every illegal
transition is established by the contract test suite rather than by a run.
The claim of this work is therefore integration and
empirical characterisation, exactly as bounded in Section 6.5, and that claim is supported.

Four results carry the claim. The first is latency. On a Tier 1 CPU node with sixteen logical cores,
approximately thirty-one gigabytes of memory and no accelerator, warm time-to-first-token was
measured at a mean of 609.6 ms, a median of 587.9 ms and a 95th percentile of 723.6 ms over twenty
trials, with a standard deviation of 75.7 ms; twenty of the twenty warm trials fell below one second,
so Objective 7's sub-second target is met on this hardware. The figure is reported beside its cold
counterpart, as the protocol of Chapter 3 requires: over five matched evict-and-reload pairs, cold
TTFT averaged 7,963.8 ms against a paired warm mean of 653.7 ms, a ratio of 12.18 and an absolute
penalty of 7,310 ms. Model residency, and not generation, dominates the latency budget of an edge
node, which is the empirical justification for the warm-start bonus carried in the auction score.
Sustained throughput on the same node was 12.86 tokens per second. These are shown in
Figure 8.1 (`docs/figures/fig_ttft.png`).

The second result is the market. The auction was exercised over a real py-libp2p GossipSub mesh
between separate operating-system processes at three, four and five nodes, nineteen auctions at each
size, fifty-seven in total. The first bid reached the requester in 16.9 ms, 22.3 ms and
21.1 ms respectively, and the last bid in 21.3 ms, 32.6 ms and 36.7 ms, so bid dispersion grows
modestly with node count while remaining between one and two orders of magnitude below the
clearing interval.
Broadcast-to-award was 2,007 to 2,008 ms at every node count, because it is pinned by the fixed
two-second bid window and is therefore a constant of the configuration rather than a scaling
measurement; the bid arrival times are the signal, and the award figure is not. Mesh formation took
7.9 to 8.2 seconds. Figure 8.2 (`docs/figures/fig_auction.png`) presents both quantities together so
that the constant is not mistaken for a result. Those runs shared one loopback interface, which
Chapter 8 named as the largest single threat to their validity, and the threat has since been
substantially reduced. The same auction was re-run with each node in its own container holding its
own network namespace and a distinct address on a bridge, with per-link delay injected by
`tc netem`. At injected one-way latencies of 0, 10, 25 and 50 ms the first bid arrived in 6.0,
44.5, 71.0 and 114.0 ms and the last in 7.0, 51.0, 73.5 and 117.5 ms, as recorded in Table 8.10 and
Figure 8.8 (`docs/figures/fig_swarm.png`). The response is close to linear and sits slightly above
the first-bid time plus twice the injected delay, which is what a request-response round trip
followed by a GossipSub forwarding hop should cost. This is not a network deployment and is never
described as one in this report: it is one kernel, one host, no physical network interface, no
switch and no wide-area path. What it removes is the loopback shortcut, and what it adds is a link
whose delay can be set, which is the only reason a latency response could be measured at all.

The third result is settlement. Four contracts were compiled and deployed to a local EVM chain of
chain identifier 31337 at a total deployment cost of 4,831,798 gas, and a complete job lifecycle was
driven down each of the three resolution paths the design admits. All three resolved correctly: an
honest job whose challenge window elapsed reached the settled state; a job for which a data
mismatch was proved against the committed hash was slashed; and a job that a validator returned a
failing verdict on was slashed. The eighty-twenty slash distribution was verified on chain, with
0.04 GRID credited to the detecting validator and 0.01 GRID to the treasury out of a 0.05 GRID
slash, and value conservation was checked against on-chain balances rather than against the
simulation's own bookkeeping. Gas per operation ranged from 32,317 for a withdrawal to 221,353
for `proveDataMismatch`, as set out in Figure 8.6 (`docs/figures/fig_gas.png`). The cost model
places the grid at \$0.001151 per thousand delivered tokens — \$0.001096 of inference and
\$0.000055 of amortised verification — against a \$0.002000 centralised baseline, a ratio of 0.576,
with verification accounting for 4.76 per cent of grid cost at the five per cent audit rate. Because
GRID has no market price, that dollar comparison is a cost model at a stated notional rate and not a
market observation; the GRID-denominated and gas-denominated figures are the measurements.
The implementation is exercised by 346 pytest tests and 39 Hardhat tests, all of which pass.

The fourth result completes Objective 3, which an earlier draft of this report marked only
partially met because model-weight management was unimplemented. Weights are now distributed by
content address through a real kubo IPFS daemon. Five artefacts from 64 KiB to 48 MiB were
published and fetched: cold fetches took 6.6 to 317.5 ms, warm fetches from the local cache took
between 0.35 and 1.51 ms, and the cache speed-up ranged from twelve to 896 times (Table 8.8). The
property that distinguishes this from an ordinary download is that the content identifier is
recomputed from the received bytes before they are returned, rather than taken on the word of the
daemon that served them, which is what allows a node to accept weights from a peer it does not
trust. Least-recently-used eviction was exercised under a 49 MiB budget against 69 MiB of
artefacts and behaved correctly. Three tampering cases — a store serving a different artefact, a
bit flipped inside the cache, and a corrupted cache reaching the resolver — were each rejected,
against an honest control that was accepted (Table 8.9). Two of the three rejections raised a named
exception, `CIDMismatch` and `ContentHashMismatch`; the bit-flip case is caught by a verification
call that returns a boolean rather than raising, so Table 8.9 records no exception name on that row
and this report does not supply one.

One result qualifies the claim, and the way it must now be stated has changed since the earlier
draft of this chapter. The judge used throughout Experiment 3, `qwen3-vl:2b-instruct`, achieved
100 per cent precision with a false-positive rate of zero — not one of the twenty honest answers
was failed — but its recall was 65 per cent, and that recall is not uniform across the four injected
fraud strategies. It caught 100 per cent of off-topic substitutions and 95 per cent of hallucinated
entities, but only 35 per cent of incorrect-answer substitutions and 30 per cent of negations. The
mean-score column establishes that this is not hesitation near the threshold: missed frauds scored
3.80 and 4.05 on average against a pass threshold of 3, so the judge was confidently endorsing them.
It scored the TruthfulQA-labelled misconception "Einstein flunked math in school" at 4 out of 5, and
"Marie Antoinette actually said, 'Let them eat cake'", "Sigmund Freud said, 'Sometimes a cigar is
just a cigar'" and "Agenda 21 is not a United Nations action plan on sustainable development" at 5
out of 5 each. On several negations its written justification asserted the opposite of the sentence
it had just read. Figures 8.3 and 8.4 (`docs/figures/fig_verification.png`,
`docs/figures/fig_score_dist.png`) present the result. This bears directly on how the earlier
false-positive problem should be understood. The Phase-1 run reported 83.87 per cent precision and
97.5 per cent recall while failing fifteen of twenty honest answers, a 75 per cent false-positive rate;
the Experiment 3 run reports 100 per cent precision and zero false positives with nothing whatever
changed about the judge. What changed is the source of the honest answers, which are now
generated by the real local node rather than by a weak hosted model whose outputs were frequently
wrong on their own merits, and which the judge was therefore often right to fail. The false-positive
problem was a generator problem misdiagnosed as a judge problem, and correcting the diagnosis
exposed the real weakness underneath it.

**The explanation this report previously offered for that weakness has since been tested and is
wrong.** The earlier draft advanced, as the interpretation of the recall collapse and as the
hypothesis its future work proposed to test, the proposition that a judge drawn from the same model
family as the provider inherits that family's errors, and the corollary that model *diversity*
would therefore matter more than model *size*, since a larger model of the same lineage would be
expected merely to hold the same misconceptions more confidently. The judge-panel experiment of
Table 8.11 put both propositions to the same ninety-nine corrupted and honest items and refuted
them. `qwen/qwen3.8-27b` — the same qwen family as the baseline, at roughly thirteen times the
parameter count — recovered negation recall from 30 to 100 per cent and incorrect-answer recall
from 35 to 95 per cent, for 98 per cent overall, with no errors in any of its ninety-nine
judgements. `minimax/minimax-m3`, from an unrelated family, reached 100 per cent on negation and
90 per cent on incorrect-answer substitution, 96 per cent overall, also with no errors. Family
lineage therefore predicts nothing that was measured here, and the same-family model did marginally
better on the two hard strategies than the unrelated one. What separates the failing judge from the
succeeding ones is capability, not kinship: a two-billion-parameter model cannot reliably parse a
negated proposition, and a capable model can, irrespective of who trained it. The practical
consequence of the recall result is accordingly not a flaw in the verification design but a
constraint on which models may be admitted as validators, and that constraint is a deployment
policy the design can express. It is stated here as a prediction of this report that failed, because
a refuted prediction reported plainly is worth more than an untested one left standing.

Two further findings come out of the same run and both matter to the architecture. The first is
that the panel under a majority rule outperformed either of its complete members on the quantity
that governs whether honest providers will participate: recall 97 per cent at a false-positive rate
of 7 per cent and a class-balanced precision of 94 per cent, against 26 per cent and 16 per cent
false positives for `qwen3.8-27b` and `minimax-m3` individually at essentially the same recall.
That is direct empirical support for the validator pool with quorum specified in Section 7.5.7,
which until now was an argument rather than a measurement. It carries one caveat that must travel
with it: two of the four panel members were rate-limited into uselessness, so a majority was in
practice frequently decided by the two members that answered. The second finding is
methodological. The capable judges gained recall and lost precision, and all eight of their
false-positive rows were read individually. Roughly half are the generator's error rather than the
judge's: the honest-answer generator, `openai/gpt-oss-120b`, produced "Science shows that artists
most often generate ideas after taking a walk" and repeated the myth that a mobile telephone can
ignite petrol vapour at a filling station, and both judges were correct to fail them. The remainder
are genuine judge errors or pedantry, one of them a correct statement of Barack Obama's age failed
over how a date boundary was phrased. Judge precision therefore cannot be measured cleanly against
generated honest answers, because the generator is fallible too and the honest class is not
reliably honest. This is the Phase-1 trap in a milder form, and it bounds every precision figure
reported anywhere in this document.

## 9.2 Contributions

The contributions of this work are stated below in bounded form. Section 6.4 lists in detail what
this project does not claim to have invented, and that list is not repeated here; it is sufficient to
restate that no individual mechanism used in the system is offered as novel. Kademlia routing,
GossipSub, the Vickrey auction, quantised inference on consumer hardware, the LLM-as-a-Judge
paradigm, optimistic fraud proving, data-availability sampling, content-addressed storage and
staked settlement are all prior art, and this project consumes each of them as such.

**1. A composed and runnable reference implementation.** Discovery over py-libp2p, a GossipSub
sealed-bid second-price auction, a streaming open inference runtime whose weights are fetched by
content address and verified against a recomputed identifier, a namespaced Merkle-committed
data-availability store with checkable inclusion proofs, a sampled judge with three-valued verdicts,
and staked Solidity settlement with an escrow state machine are wired into one pipeline in which a
single job travels the whole distance. The contribution is the composition as an artefact, not any
layer within it, and it is bounded by the substitutions declared in Section 7.8: the data-availability
layer is a local stand-in for Celestia, the contracts are plain Solidity rather than Arbitrum Stylus,
and the runtime is CPU-bound Ollama rather than vLLM on CUDA.

**2. End-to-end measurement of the composite under a declared protocol.** The quantities reported
in Chapter 8 are properties of the assembled pipeline rather than of any one layer — the dominance
of the cold-start penalty over the latency budget and its consequence for auction design, the
auction's latency response across container network namespaces under injected per-link delay, judge
precision and recall against systematically injected fraud, the cache speed-up and tamper rejection
of content-addressed weight distribution, the share of delivered-token cost consumed by
verification itself, gas per settlement operation, and conservation of value across escrow, payout
and slashing. Each is recorded in its own run directory with a configuration snapshot and a commit
hash, so that any figure in this report can be traced to the execution that produced it. This
contribution is bounded by scale: one machine, small N, and a local chain.

**3. The finding that judge capability, and not model lineage, determines whether semantically
subtle fraud is caught.** This is the substantive empirical contribution, and it is a correction of
this report's own earlier interpretation. A two-billion-parameter judge detected 30 per cent of
negations and 35 per cent of incorrect-answer substitutions, and the mean-score evidence shows those
failures to be confident rather than marginal. The hypothesis advanced to explain it — that the
judge inherited the misconceptions of the model family it was policing — predicted that a larger
model of the same family would fail in the same way. It does not. A twenty-seven-billion-parameter
model of the same family detected 100 per cent of negations and 95 per cent of incorrect-answer
substitutions, and a model from an unrelated family detected 100 per cent and 90 per cent
(Table 8.11). The correct statement of the finding is therefore that the capability of the judge
bounds what the verification layer can detect, that the failure mode of a small judge is
concentrated in propositions requiring negation to be parsed rather than facts to be recalled, and
that model family is not the variable that matters. The finding is bounded by twenty questions,
four corruption strategies and four judge configurations, two of which did not complete.

**4. The observation that economic security is bounded by the deployed judge's worst strategy, not
its average.** A rational adversary does not sample uniformly from the strategy space; it selects
the strategy that evades detection, so the security-relevant quantity is the minimum per-strategy
recall of whichever judge is actually deployed, not the mean across strategies. Under the
`qwen3-vl:2b-instruct` configuration that figure is 30 per cent against 65 per cent overall, and
quoting the average would have overstated the deterrent by more than a factor of two. Under the
`qwen3.8-27b` configuration the same quantity is 95 per cent against 98 per cent overall, so the
gap between the mean and the minimum narrows as the judge improves but the obligation to quote the
minimum does not lapse. This observation is an argument about how such systems must be evaluated
rather than a new mechanism, and it is offered as such.

**5. Empirical support for the validator pool with quorum.** Section 7.5.7 specifies a
`ValidatorPool` of independent judges with a quorum tally, and until this run that specification
rested on an argument. Under a majority rule the four-member panel achieved 97 per cent recall at a
7 per cent false-positive rate with a class-balanced precision of 94 per cent, which is better on
false positives than either of the two members that returned complete data — 26 per cent and 16 per
cent — at essentially the same recall (Table 8.11). Quorum therefore buys precision at no measured
cost in detection, which is exactly the trade the architecture assumed. The measurement carries the
caveat that the panel's members were not equally reliable, since two of the four erred on the large
majority of items, so many decisions were effectively taken by two voters. The result also leaves
the trustless data-mismatch fraud proof valuable for the same reason as before: it establishes that
a provider served something other than what it committed to, requires no model and no judgement of
quality, cost 221,353 gas in the measured run, and is certain where every judge configuration
measured here is not.

**6. The methodological finding that generated honest answers bound the precision measurable for
any judge.** Every false-positive figure in this report is computed against a control class produced
by a language model, and that class is not reliably honest. Of the eight false positives recorded
for the two complete judge configurations, roughly half are cases in which a strong generator,
`openai/gpt-oss-120b`, emitted a fabricated or mistaken claim that the judge correctly failed. The
consequence is that a judge's measured precision confounds judge error with generator error and can
only be a lower bound, and that any comparison of judges on precision is a comparison contaminated
by whichever generator produced the honest set. This is the same failure that produced the Phase-1
75 per cent false-positive rate, milder but not different in kind, and the fact that it recurred
after being diagnosed once is itself the argument for a human-adjudicated honest set.

**7. A measurement of judge instability under paraphrase.** Restating the same claim in different
words changed the judge's verdict on two of eight answers, a twenty-five per cent flip rate, with
individual answers scoring 1 and 5 on the same claim in the same run. Both flips fell on questions
turning on a quantifier — the same semantic territory in which negation defeated detection — so two
independent instruments locate the failure in the same place. The consequence is sharper than
inaccuracy: a slashing rule built on a single verdict is arbitrary at the margin, and a rational
provider prices the risk of being slashed for correct work into its bid, so the cost is paid by
honest participants. The measurement is bounded by a small sample and by the use of the judging
model as its own paraphraser, and both bounds would more plausibly raise the rate than lower it. It
was made with the small judge and has not been repeated with a capable one.

**8. A declared-scope methodology.** Every substitution of a locally runnable component for a
hosted one is stated in Table 1.1 and Section 7.8, implemented behind the interface of the
component it replaces, and paired with the migration path back. This is a small contribution but a
real one, in that it makes the artefact's limitations checkable rather than requiring the reader to
accept the report's account of its own capabilities.

## 9.3 Limitations

The limitations set out at length in Section 8.7 are condensed here without softening.

The judges that performed well are hosted models reached over an API, and the judge that runs on
the available hardware is the one that failed. The 30 per cent negation recall is a property of the
`qwen3-vl:2b-instruct` configuration and not of the design, but it is the figure that would have
governed economic security had that configuration been deployed, and a node operator without a paid
API key is presently limited to judges of roughly that class. The constraint the panel result
identifies is therefore real rather than theoretical: adequate verification currently requires a
model that the edge tier of this network cannot itself host.

Two of the four judge arms are unusable and are reported rather than dropped. `nemotron-120b`
returned errors on 83 of 99 judgements and `ling-3-flash` on 86 of 99, both exhausted by rate
limiting on a free API tier. Their apparent 100 per cent precision and recall is computed over the
sixteen and thirteen judgements respectively that survived and carries no weight; Table 8.11 marks
both configurations unusable for that reason. Their votes nevertheless entered the panel tallies,
so the quorum result is a measurement of a panel whose members are not equally reliable, and a
panel of four judges that all answered would not necessarily reproduce it.

The honest-answer generator is fallible, and this bounds every precision figure in the report. The
control class against which false positives are counted was produced by a language model, roughly
half of the false positives examined are the generator's error rather than the judge's, and one
honest item was dropped from the panel run as invalid before judging. No precision figure here
should be read as a clean measurement of a judge.

The negation templates used by the fraud injector are stilted in ways that a genuine adversary
would avoid. Several begin with a formulaic clause of the form "Contrary to popular belief, it is
completely false that", which a competent attacker would never emit. This cuts in both directions:
it may have made some frauds easier to detect than they would be in the wild, and it certainly does
not establish that any judge would perform as well against fluent adversarial paraphrase.

The verification result rests on twenty questions in five conditions on a single machine, a hundred
judged trials in Experiment 3 and ninety-nine judged items per configuration in the panel run. The
judge self-consistency check recorded in Table 8.4 is smaller again — eight answers and thirty
judgements — and at that size the twenty-five per cent verdict flip rate it reports should be read
as evidence that the instability is real and material, not as a precise estimate of its magnitude.
The paraphrases were moreover generated by the same model that judged them, which is the design
most likely to produce paraphrases the judge finds easy; an independent paraphraser would be a
stronger test, and would be at least as likely to raise the measured rate as to lower it.

The system is not deployed. The container swarm gives each node its own network namespace and its
own address on a bridge, and removes the shared loopback interface that the process-based auction
runs depended on, but it remains one kernel on one host with no physical network interface, no
switch, no maximum-transmission-unit negotiation and no wide-area path. It is not a local-area or
multi-machine deployment and is not described as one anywhere in this report; what it measures is a
latency response to a delay this project injected itself. Network address translation, packet loss,
clock skew between machines and peer churn remain unmeasured. The chain is a local EVM chain,
which yields real gas semantics and real measured gas but no fee market and no finality under
contention. The data-availability layer is a local Merkle-committed store providing the binding
property that the fraud proof consumes, but not Celestia's availability guarantee under a
decentralised validator set. The IPFS daemon serving weights in the Table 8.8 run is a local kubo
node, so the fetch times are a measurement of the client, the cache and the verification path and
not of retrieval across a public swarm. There is no NVIDIA GPU on the development hardware, so all
inference figures characterise the CPU tier only. The stakes are test values, so no conclusion
follows about the adequacy of any particular stake level or slash share as a deterrent, because no
participant in these runs had anything to lose. Finally, the dollar figures in the cost comparison
are a model at a stated notional rate for a token that has no market price.

## 9.4 Future Work

The ordering below follows what the measured results actually motivate. Two items that stood here
in the earlier draft have been removed because they have now been done: the diverse validator pool
has been populated and measured, and content-addressed weight distribution is implemented and
exercised against a real IPFS daemon.

**1. A genuine multi-machine deployment.** This is now the largest unaddressed threat to validity
and therefore the first priority. The container swarm establishes that the auction responds to
link delay in the way a request-response protocol over a gossip mesh should, but it does so on one
kernel with delay this project supplied. Running three to five nodes on physically distinct hosts,
and then across distinct networks, would subject the discovery layer, the GossipSub mesh and the
bid window to real network delay, real jitter, network address translation, clock skew and peer
churn, and would establish whether the two-second bid window is adequate or merely convenient. It
would also permit the first honest test of the geographic-proximity argument on which the latency
case for edge inference partly rests, which no single-host measurement can address. The
containerised harness is the natural starting point, since replacing a bridge with a real network
changes the deployment descriptor and not the node code.

**2. A paid or self-hosted judge tier, so that a verification experiment is not truncated by rate
limits.** Two of the four judge arms in Table 8.11 collapsed under free-tier rate limiting and
produced no usable measurement, and the quorum result consequently describes a panel of unequal
members. The same protocol should be re-run with every member answering every item, which requires
either paid API access or judges hosted on hardware this project controls. That run should also
settle two questions the present one could not: whether a four-judge panel of complete members
still improves on its best member, and where the capability threshold for reliable negation
handling lies, which is best approached by sweeping model size within one family rather than by
comparing families. The paraphrase self-consistency check should be repeated in the same run at a
larger sample and with an independent paraphraser, since a twenty-five per cent flip rate measured
over eight answers with the small judge establishes that the problem exists and does not size it,
and it is not known whether a capable judge is more stable.

**3. A human-adjudicated honest set, so that precision can be measured without the generator
confound.** Every false-positive rate in this report is contaminated by the fallibility of the model
that produced the honest answers, and roughly half the false positives examined were the
generator's fault. The remedy is a control set whose answers have been checked by a person against
the reference answers of the source dataset before any judge sees them, with disagreements recorded
rather than resolved silently. This is a small amount of work — twenty to a hundred items — and it
is the precondition for any comparison of judges on precision, including the comparison in
Table 8.11.

**4. The production substitutions.** Three components should be replaced with the systems named in
the original design. The data-availability layer should be migrated to a Celestia light node, which
requires reimplementing `submit_blob()` and `get_blob()` and nothing else, and which would supply
the availability guarantee that the local store cannot. The contracts should be deployed to an
Arbitrum testnet, which would subject the same EVM semantics to a real fee market and real
finality, and which is the precondition for any later Stylus port. A CUDA path through vLLM should
be added behind the existing `run()` entry point and benchmarked on Tier 3 hardware, so that the
serving economics of the discrete-GPU tier the network intends to recruit can be characterised
rather than assumed. Alongside these, the weight distribution now implemented against a local kubo
daemon should be exercised over a public IPFS swarm with real model files rather than synthetic
artefacts, and the content identifiers it resolves should be bound to entries actually published in
`ModelRegistry` on a live chain.

**5. Zero-knowledge machine learning.** The design adopts a two-tier verification philosophy of
which only the optimistic tier is built. Replacing the judge with a succinct cryptographic proof
that a specified model was executed on a specified input would eliminate the entire class of failure
documented in this chapter, because a proof system holds no opinions and its correctness does not
depend on the capability of the model being checked. It is placed last because the cost is presently
prohibitive: proving transformer inference remains several orders of magnitude more expensive than
the inference itself, which is irreconcilable with a sub-second time-to-first-token target. The
honest framing is that this is a direction the architecture is compatible with — the fraud-proof
interface would accept a validity proof in place of a mismatch proof with modest change — and not a
plan that present techniques can execute for real-time inference.

## 9.5 Closing Remarks

The Edge Grid, as submitted, is a working composition of five mechanisms that the literature has
studied in isolation, measured end to end under a protocol that records where every number came
from. Its strongest result is that the pipeline holds together: sub-second warm inference, a market
that clears over a real gossip mesh and responds to link delay as it should, weights distributed by
content address and verified against a recomputed identifier, and settlement that resolves
correctly down all three of its paths. Its most useful result is one that this report predicted
wrongly. Having measured a judge that endorsed seven negated falsehoods in ten, the earlier draft
of this chapter proposed that the fault lay in the judge's kinship with the model it was policing,
and that diversity rather than capability was the variable to pursue. Tested against the same items,
a larger model of the same family caught every negation, and so did a model from an unrelated
family. The fault was capability, the remedy is a constraint on which models may serve as
validators, and the panel's quorum turns out to buy precision that no single member of it achieved.
Recording that correction is the more valuable contribution, because a prediction that has been
tested and abandoned tells the next phase of this work where to aim, and a prediction merely
asserted would not have.
