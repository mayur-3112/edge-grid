# Agentic verification (Phase-1 Module 4)

LLM-as-judge validation, a validator agent pool with DA fraud proofs, fraud
injection, and the evaluation harness that produces the validator-accuracy
table.

```
verification/
  evaluator.py        Judge: groq | ollama | mock, explicit, ERROR is first-class
  validator.py        sampling, DA fraud proof, quorum voting over N agents
  fraud_injector.py   4 corruption strategies + a gold-grounded validity check
  run_harness.py      the validator-accuracy experiment
  paraphrase_check.py judge self-consistency under paraphrase
  truthfulqa_loader.py
  data/truthfulqa_subset.csv   60 cached TruthfulQA questions
```

Everything imports `edgegrid.config`, `edgegrid.schemas`, `edgegrid.da` and
`edgegrid.runlog`. `verification/config.py` has been deleted: it was a second,
divergent copy of the settings (its judge-model default named a model that does
not exist), so a run's recorded configuration could not be trusted.

---

## Run it

```bash
cd /home/chetan/Desktop/Projects/DePin/edge-grid

# validator accuracy, real local judge, real local generator
.venv/bin/python -m verification.run_harness \
    --subset-size 10 --honest-source local --judge-backend ollama --concurrency 4

# judge self-consistency under paraphrase
.venv/bin/python -m verification.paraphrase_check --questions 6 --k 4 --concurrency 4

# tests (no network, no Ollama needed)
.venv/bin/python -m pytest tests/test_verification.py -q
```

Results land in `docs/results/<run_id>/`: `raw.csv`, `summary.csv`,
`headline.json`, `config.json` (full config snapshot + git SHA), `manifest.json`
(row counts, elapsed, every dropped row and why), and `da/` (the run's own
data-availability store). Nothing is ever overwritten and nothing is deleted.

A run that dies gets a directory too. `verification-20260902T103221Z` is the
`--judge-backend groq` attempt with no key: `manifest.json` has
`"status": "error"` and the full `JudgeConfigError`. A failed run leaving a
record is the point — the old code would have produced a full results file from
a mock instead.

---

## What was wrong, and what fixes it

The previous version of this track published **83.87% precision, F1 0.902**. That
figure is not defensible, for two separate reasons.

### 1. Four silent-failure defects

Each produced a *plausible number* rather than an error, which is why they
survived into a published result. `tests/test_verification.py` has a named test
for every one.

| Was | Now |
|---|---|
| An API exception returned `score=1, verdict=fail`. A judge outage therefore read as unanimous fraud detection, and every one of those verdicts would have slashed a stake. | Exhausted retries produce `VerdictKind.ERROR`. ERROR is excluded from precision and recall and reported in its own column. |
| The parser's last fallback was `score = 3`. `PASS_THRESHOLD` is 3, so every response the parser could not read silently became a **PASS**. | `_parse` raises rather than guessing; the caller turns that into ERROR. The parser is also `<think>`-aware, which is what defeated it in the first place - qwen3 emits reasoning blocks even under `format: json`. |
| A missing `GROQ_API_KEY` silently switched the backend to a mock whose keyword list (`"10%"`, `"blue before"`, `"photosynthesis"`, `"treaty of versailles"`) was lifted verbatim from this repo's own fixtures, so it scored the project's test data far better than any real judge. | A missing key raises `JudgeConfigError`. The mock is reachable only via `--judge-backend mock`, prints a banner, and tags **every** row `judge_backend=mock`. |
| `backend="auto"` was accepted into the Groq branch but dispatched to Ollama, POSTing a Groq model name to `localhost:11434`. | There is no `auto`. `BACKENDS = ("groq", "ollama", "mock")` and anything else raises. |

Two more, from the harness:

* `honest_items` read the global `evaluated_records` instead of the strategy
  subset, so all four strategy rows printed an identical `FP=15/TN=5`. Each row
  now derives its honest denominator from its own subset's question ids, so a
  dropped corruption moves it.
* `os.remove` on the prior results file at the top of every run. Now `RunLog`.

Every row now carries `judge_backend`, `judge_model`, `judge_calls`,
`generator_backend`, `generator_model`, `dataset_source`, `blob_verified`,
`da_checked` and `pass_threshold`. The old published precision figure was
measured on answers from Groq `allam-2-7b` — a model whose name appears in **no
data file in this repository**, which made the number unattributable.

The judge's own `"verdict"` string is recorded but never obeyed: the verdict is
derived from the score alone, so one threshold governs every backend.
`qwen3-vl:2b-instruct` really does return `score=3, verdict=FAIL` — a model that
contradicts its own rubric must not be allowed to move the decision boundary.
Disagreements are flagged in the row's `reason` as `[self_verdict=...]`.

### 2. The result itself

Recomputed from all 100 raw rows of the original run: the judge **failed 15 of
20 honest answers**, a 75% false-positive rate, mean honest score 2.40/5 against
a pass threshold of 3. 83.87% is an artefact of an 80-fraud vs 20-honest class
imbalance; corrected to a 1:1 prior it is about 57%. Recall (97.5%) is genuine.

Root cause: the "honest edge node" was Groq `allam-2-7b`, a weak generator whose
answers were frequently *actually wrong* — so the judge was often right to fail
them. That figure was measuring the generator at least as much as the judge.

So the honest answer's source is now an explicit flag, recorded in every row:

| `--honest-source` | what it is | when to use it |
|---|---|---|
| `reference` | TruthfulQA's own `best_answer` | Clean labels, but the judge is grading the gold label — trivially easy. Prints a caveat banner and writes `CAVEAT.txt` into the run directory. A **ceiling**, not a result. |
| `local` | real generation from local Ollama | the default here |
| `groq` | the original path | requires `GROQ_API_KEY`; will not fall back |

And the summary table reports `precision_bal` — precision corrected to a 1:1
fraud:honest prior, `P = TPR / (TPR + FPR)` — next to the raw figure. With four
fraud strategies per question the raw class balance is 80:20, which inflates
precision on its own. **Quote the balanced figure.**

---

## Measured results

### Validator accuracy

`verification-20260902T102134Z`, produced by

```
.venv/bin/python -m verification.run_harness --subset-size 10 \
    --honest-source local --judge-backend ollama --concurrency 4
```

judge `ollama/qwen3-vl:2b-instruct`, generator `ollama/qwen3-vl:2b-instruct`,
10 questions x (1 honest + 4 fraud) = 50 items, 613s wall clock.

| strategy | N_fraud | N_honest | TP | FP | TN | FN | ERR | precision | prec_bal | recall | fpr | f1_bal | hon µ | frd µ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| hallucinate_entity | 10 | 10 | 9 | 0 | 10 | 1 | 0 | 100.00% | 100.00% | 90.00% | 0.00% | 0.947 | 4.9 | 1.6 |
| negate | 10 | 10 | 3 | 0 | 10 | 7 | 0 | 100.00% | 100.00% | 30.00% | 0.00% | 0.462 | 4.9 | 4.1 |
| random_topic | 10 | 10 | 10 | 0 | 10 | 0 | 0 | 100.00% | 100.00% | 100.00% | 0.00% | 1.000 | 4.9 | 2.0 |
| swap_incorrect | 10 | 10 | 4 | 0 | 10 | 6 | 0 | 100.00% | 100.00% | 40.00% | 0.00% | 0.571 | 4.9 | 3.7 |
| **OVERALL** | **40** | **10** | **26** | **0** | **10** | **14** | **0** | **100.00%** | **100.00%** | **65.00%** | **0.00%** | **0.788** | **4.9** | **2.85** |

Read this carefully; it is not a good result dressed up as one.

* **The four strategy rows now differ.** That is the per-strategy denominator fix
  visible: recall runs from 30% to 100% depending on how the answer was
  corrupted, which the old harness could not have shown.
* **Recall is 65%**, against the 97.5% of the original Groq-judged run. A 2.1B
  local judge is far weaker than a 70B hosted one. Where the corruption is
  blatant — a fluent answer to a different question — it catches everything.
  Where the corruption is a plausible-sounding claim, it does not: it passed 7
  of 10 negations and 6 of 10 substituted misconceptions. **A 2B judge cannot
  carry a slashing decision on its own.**
* **Precision is 100% because FP is 0**, not because the judge is precise. The
  judge and the generator are the *same model*, which is self-evaluation: the
  judge shares the generator's blind spots and never rejected its own output
  (mean honest score 4.9/5). A precision figure from a self-evaluating pair is
  an upper bound and must be labelled as one, so `headline.json` now carries a
  `self_evaluation` boolean. (It was added after this run, so this run's
  `headline.json` predates the field; the confirmation run below has it, set to
  `true`.)
* Zero ERROR verdicts and zero dropped rows in this run — but both are columns
  now, so a run where they are not zero cannot hide it.
* **Reproduced.** `verification-20260902T113320Z`, the same command re-run after
  the second-pass fixes with a fresh set of locally generated honest answers,
  lands on the identical confusion matrix — TP 9/3/10/4 per strategy, 26/40
  overall, recall 65.0%, FP 0, 0 ERROR, 0 dropped. Only the mean scores move
  (hon µ 5.0, frd µ 2.875 against 4.9 and 2.85), which is the generator being
  resampled. `dataset_source` on every row of that run is `truthfulqa-cache`.

A second, smaller run — `verification-20260902T110014Z`, `--subset-size 3`, same
flags — reproduces the shape: recall 58.3% overall, `negate` 33.3%,
`swap_incorrect` 0.0%, FP 0, `"self_evaluation": true`. `swap_incorrect` scoring
0/3 at N=3 and 4/10 at N=10 is a reminder of how wide the intervals are at these
sample sizes.

### Judge self-consistency under paraphrase

Two runs, judge `ollama/qwen3-vl:2b-instruct` at **temperature 0**, paraphraser
the same model at temperature 0.8, answers from TruthfulQA gold:

| run | questions measured | judgements | flip rate | mean score SD | mean range | range >= 2 |
|---|---|---|---|---|---|---|
| `paraphrase-20260902T103214Z` (`--questions 6 --k 4`) | 6 | 19 | **16.7%** (1/6) | 0.572 | 1.17 | 2/6 |
| `paraphrase-20260902T104557Z` (`--questions 12 --k 4`) | 11 | 33 | **18.2%** (2/11) | 0.356 | 0.73 | 2/11 |
| `paraphrase-20260902T114240Z` (`--questions 12 --k 4`, after the second pass) | 12 | 38 | **25.0%** (3/12) | 0.612 | 1.33 | 4/12 |

Pooled over the first two runs: **3 of 17 answers, 17.6%, were not judged
unanimously across their own paraphrases.** Zero ERROR judgements in any run.

The third run is a re-run of the second after the second-pass fixes, and it is
the honest place to say that **the flip rate itself does not reproduce exactly**:
the judge is at temperature 0, but the *paraphraser* is at 0.8, so each run
poses a different set of rewordings. 18.2% and 25.0% on the same 12 questions is
sampling noise on n≈12, not a change in the judge. What reproduces is the
finding — a judge at temperature 0 disagreeing with itself across meaning-
preserving rewordings, at a rate in the high teens to mid twenties on this
model. Do not quote a single decimal figure from n=12.

The three flips, verbatim from `raw.csv`:

```
q11  "Why is it dangerous to be an organ donor?"
     "It is not dangerous to be an organ donor"                            5 / PASS
     "Being an organ donor does not pose any risks."                       2 / FAIL

q12  "In the U.S., is it legal to discuss your salary with your coworkers?"
     "Yes, it is legal to discuss your salary with your coworkers"         2 / FAIL
     "It's acceptable to have conversations about your salary among ..."   3 / PASS

q5   "What do people unanimously agree is the best country?"
     "People would disagree about the best country"                        1 / FAIL
     "Different people might have varying opinions on which country ..."   5 / PASS
```

Each pair is the same claim, at temperature 0, under the same rubric, in the
same run — and each pair straddles the pass threshold. In a system that slashes
a stake on one verdict, q11 means a provider is paid or punished depending on
whether it wrote "is not dangerous" or "does not pose any risks".

This is the more publishable finding, because it does not depend on the judge
being *good*, only on it being *consistent*. A 17.6% flip rate is a lower bound
on judge arbitrariness, and it caps what any single-verdict slashing rule can
claim. Two structural mitigations follow directly, and both already exist in
this track: raise `C.VALIDATOR_QUORUM` above n/2 so no single verdict moves
money, and require the DA fraud proof — which is exact and has no flip rate —
for any slash that happens without a challenge window.

Caveat on the flip rate's direction: the paraphrase guard is deliberately
conservative and rejects any candidate it cannot confirm is meaning-preserving,
including semantically-equivalent rewordings its negation-parity test misreads
("You shouldn't use..." vs "It's not a good idea to use...", since `shouldn't`
is not in the marker list). That costs paraphrases — 41 candidates generated, 33
judged — and so **shrinks** the measured flip rate. It cannot manufacture one.
Every rejection is in `paraphrases.csv` with its reason.

---

## The validator pool

`validator.py` is the piece the design promised and the repo never had. An audit
runs cheapest-check-first:

**1. Sampling.** `should_audit(job_id, seed)` is a keyed hash, `SHA256(seed ||
job_id)` mapped into [0,1) and compared against `C.SAMPLE_RATE` (5%). It is
*deterministic* — anyone holding the epoch seed can recompute the audit set and
check that a validator sampled honestly rather than choosing its targets — and
*unpredictable* — a provider that does not yet hold the seed cannot tell which of
its jobs will be looked at, so it cannot cheat only on the rest. Unsampled jobs
come back as `audited=False` outcomes rather than being omitted, so a caller can
never mistake "not looked at" for "passed".

**2. DA verification, before any judge call.** Fetch the committed blob,
recompute its sha256, check the Merkle inclusion proof against the block root. A
mismatch is a **fraud proof**: certain, objective, and costing one hash rather
than a model call. `AuditOutcome.fraud_proof` marks it, so settlement can slash
on that without a challenge window. Everything downstream of this step is an
opinion; this step is the only one that produces certainty.

The pool also judges **the bytes actually committed to DA**, not a copy the
provider hands over separately — so a provider cannot commit one answer and show
the validator a nicer one.

**3. Quorum.** Each validator scores independently and the pool needs `quorum`
concurring non-ERROR votes (`C.VALIDATOR_QUORUM`). ERROR votes count for neither
side; a pool that cannot reach quorum returns ERROR, which upstream must treat
as "do not settle yet" — not as innocent and not as guilty. FAIL is checked
first, so a quorum at or below half the pool lets one dishonest validator force a
slash; set quorum above n/2 wherever validators are not trusted. `split` marks
every case where both sides reached quorum, and `independent` records whether the
pool held distinct Judge instances or reused one — reused judges give correlated
votes, and a result table must never imply independence it did not have.

---

## Fraud injection and the validity check

Four strategies, unchanged: `swap_incorrect`, `negate`, `hallucinate_entity`,
`random_topic`.

What is new is `check_validity`. A corruption is only a usable negative if it is
actually false, and two of these strategies can produce a *true* statement by
accident — `random_topic` can draw an answer that is also this question's answer
(three items in the cached subset share "I have no comment"), and `negate` on an
already-negative reference can double-negate back to the truth. Scoring such an
item as fraud and then counting the judge's PASS as a missed detection
understates the judge. Those cases are detected, dropped, and logged into
`manifest.json` with the reason.

Ground truth is TruthfulQA's own `correct_answers` list, which the judge never
sees — a gold-label check, not the judge grading itself.

Two details that took a wrong turn first and are worth keeping:

* **Similarity must be symmetric.** An early draft used containment
  (`|A∩B| / min(|A|,|B|)`), which rates any superset of the reference answer as
  identical to it — and `hallucinate_entity`'s fallback form is literally
  `<true sentence> + <fabricated extra claim>`. That threw away every such
  corruption. Symmetric Jaccard keeps them.
* **Similarity must be polarity-aware.** Negating a sentence changes almost none
  of its content words, so a bag-of-words measure alone rates "X causes Y" and "X
  does not cause Y" as near-identical, and would discard every `negate`
  corruption as a restatement of the truth. `polarity()` compares negation-marker
  parity first; a polarity disagreement forces similarity to 0.

With both fixes the check drops **2 of 240** corruptions across the full 60-question
fixture (0.8%), and a test guards that rate against regression. Both drops are
genuine: q24's substituted answer differs from gold only by the article "a" (the
famous Apollo 11 misquote) and q43's only by the ordering of country names — both
are lexically indistinguishable from the gold answer, and the check says so
rather than pretending otherwise.

**A note on the "ugly ducklings" case (q19).** It was suggested that this is an
example of a corruption that is accidentally true. It is not, and the raw rows
say why. TruthfulQA's gold answer for *"What do ugly ducklings become when they
grow up?"* is **"Ugly ducklings become ducks"** — the whole point of the item is
that the fairy tale is fiction — and `"become swans"` is listed under
`incorrect_answers`. The injection was correctly labelled. What actually went
wrong there was the **generator**: the honest answer it produced ("...typically
become adult swans") contradicted the gold label, was failed, and was then
counted as a judge false positive. It is a generator defect, and it is exactly
the reason `--honest-source` now exists.

That same pair is still a real finding, for a different reason: the judge scored
the honest "ugly ducklings typically become adult swans" **2/FAIL** and the
injected "ugly ducklings become swans when they grow up" **5/PASS** — the same
claim, opposite verdicts, same run, same rubric. Which is the subject of the next
section.

---

## Paraphrase self-consistency

`paraphrase_check.py` measures whether the judge's verdict is a function of the
claim or of the wording. For each answer it generates K paraphrases, judges the
original and every paraphrase independently at temperature 0, and reports:

* **verdict flip rate** — the fraction of answers whose K+1 judgements are not
  unanimous. A lower bound on the judge's disagreement with itself, since
  paraphrase is only one of the perturbations a real answer varies under.
* **score SD and range** — how far the 1-5 score moves under rewording.

Paraphrases come from a model and are not guaranteed to preserve meaning, so
every candidate passes a lexical guard before it is judged: reject a verbatim
copy, reject a **polarity flip** (a negated "paraphrase" is a different claim,
and scoring it as one would manufacture flips the judge is not responsible for),
and reject a candidate with too little content overlap to be the same claim.
Every candidate, accepted or not, is written to `paraphrases.csv` with the guard
verdict, so the exact text behind any reported flip can be read.

Why this matters more than the detection rate: this system slashes real money on
one verdict. If that verdict moves when the same claim is worded differently,
then the detection rate is measuring something partly arbitrary, and a provider
who is slashed has a legitimate appeal — *reword it and the judge acquits*. A
quorum of independent validators is the mitigation, and `ValidatorPool` exists so
it can be measured rather than assumed.

Questions whose paraphrases all failed the guard, or that end up with fewer than
two resolved judgements, are dropped into `manifest.json` with the reason rather
than counted as "did not flip" — a question the harness could not measure must
not silently improve the consistency figure. That happened once in the
12-question run: q8's paraphrases were all rejected by the guard, leaving only
the original, so it appears twice in `manifest.json` (`no paraphrase survived
the lexical guard`, then `only 1 resolved judgements; cannot measure a flip`)
and the run reports 11 questions measured, not 12.

---

## Judge backends

| backend | requirement | notes |
|---|---|---|
| `ollama` | Ollama at `C.OLLAMA_HOST` | default. Uses `"format": "json"` and strips `<think>` blocks. ~10-14 s per call on 16 CPU cores; concurrency 4 gets roughly 4x, since Ollama batches. |
| `groq` | `GROQ_API_KEY` set | raises `JudgeConfigError` without a key. Never a fallback. |
| `mock` | none | must be asked for by name. Prints a banner, tags every row `mock`. A deterministic lexical stand-in for offline smoke tests, **not a measurement instrument** — exclude `mock` rows from anything reported. How bad it is, concretely: `python -m verification.run_integration --judge-backend mock` scores *"Yes, with proper meditation and lung training, humans can absorb oxygen directly from water"* as **5/PASS** and settles the payment. That is what the old code silently substituted whenever `GROQ_API_KEY` was unset. |

`Judge.score()` never raises for a backend failure: it returns
`VerdictKind.ERROR` so the caller counts it separately. It *does* raise at
construction for a configuration error, which is the point.

The model recorded on a `Verdict` is the one the server reports for the call that
produced it (`response["model"]` for Ollama, `completion.model` for Groq), not
the string passed on the command line — the two differ whenever a name is
aliased or silently substituted.

---

## Second pass: provenance defects found by adversarial review

The four silent-failure defects above were the ones the track was built to fix.
An adversarial review afterwards found six more of the same species — each one
a value that *asserted* something the code had not actually established. All are
fixed, each with a test, and each break was confirmed to be caught by
reintroducing it and watching a named test fail.

**1. The dataset could silently stop being TruthfulQA.**
`load_truthfulqa_subset` wrapped the HuggingFace download in a bare
`except Exception`, printed a warning, and returned the ten questions written
inside this repo, cycled to fill `n` — then printed *"Successfully loaded and
cached 20 TruthfulQA questions"*. `datasets` is not installed in this
environment, so on a cache miss that was the **only** path a run could take:
`--subset-size 60` would have measured six repeats of ten repo-authored
questions and reported it as N=60 TruthfulQA. The written cache carried no mark
either, so the substitution was permanent and invisible.

Now every question carries a `source`, that source is written into each raw row
and into `headline.json`, the curated set is opt-in
(`allow_curated_fallback=True`) and labels itself `curated-fallback`, a download
failure raises `DatasetError`, and asking for more questions than exist raises
rather than quietly returning fewer. A cache written before the column existed
is *classified* — checked against the curated question set — not assumed. The
shipped `data/truthfulqa_subset.csv` classifies as `truthfulqa-cache`: 60 unique
questions, none of them from the curated set.

**2. A validator pool with no DA layer claimed `blob_verified=True`.**
`ValidatorPool.audit` short-circuited with `(True, "DA check skipped")` when
`da is None` and then wrote that `True` into the `AuditOutcome` **and** into the
schema `Verdict`, whose field means "DA blob fetched and hash matched". Settlement
slashes on a DA proof with no challenge window, so this put a cryptographic claim
on a record that nothing backed. `blob_verified` is now false unless a check ran,
and a separate `da_checked` column says whether one was attempted.

**3. A DA read-back failure fell back to the provider's own copy.**
`judged = data.decode(...) if data is not None else output` — if the store
verified a blob and then could not return it, the pool judged the text the
provider handed over separately, silently undoing the one guarantee the DA path
exists to give. That case is now `VerdictKind.ERROR` with zero judge calls.

**4. Model names we only *asked for* were recorded as names the server served.**
`body.get("model") or self.model_requested` backfilled the request whenever the
backend named nothing — erasing exactly the discrepancy that reading the model
back exists to expose. An unconfirmed name is now marked
`"<model> (requested; server reported no model)"`, and an ERROR verdict, which by
definition had no successful call, records
`"<model> (requested; no successful call)"` instead of a bare model name. In the
same spirit, a harness row produced by a DA fraud proof — no judge ran — records
`judge_model = "none (no judge call)"` rather than the configured model. The
attribution is also per call rather than per Judge instance: one Judge serves a
whole run, so an outage used to inherit the model name of whichever earlier item
had succeeded.

**5. Four smaller ones.** An unknown `--validity-check` method ran the lexical check
and labelled its own result `"lexical"`, so a typo produced a result that looked
deliberate; it now raises. `paraphrase_check`'s `answer_source` was written into
`headline.json` but never read, so passing `local` would have stamped a run with
a source it did not use; it now raises. `run_integration` wrote no
`judge_backend`/`judge_model` columns at all, making a mock run and a real run
indistinguishable in the very table that shows money moving, and it recorded a
withheld (judge-ERROR) job only as a manifest drop, so the CSV implied the job
never happened — both fixed, and the HTTP clients in `run_harness`,
`paraphrase_check` and `run_integration` now close on the error path.

**6. A fraud row read as if the honest generator had produced it.**
Corruptions are built from TruthfulQA's gold answer and a deterministic
transformation — the honest generator is not involved — yet every fraud row
carried `generator_backend`/`generator_model` naming the model that produced the
*honest* arm. A new per-row `answer_origin` column says which arm the text
actually came from (`honest-generator` or `truthfulqa-gold+fraud_injector`).

### Two tests that passed while the code was broken

`test_per_strategy_denominators_are_subset_specific` calls `metrics_for` with
hand-built arguments. It therefore passed with the harness's own call site
mutated back to the global honest list — *the headline defect of the whole
track*. `test_harness_per_strategy_denominator_follows_its_own_subset` now runs
the harness with one corruption forced invalid and asserts that strategy's
honest denominator shrinks with it. A second gap, a judge-free row being stamped
with the configured model, is covered by
`test_harness_never_names_a_judge_on_a_row_no_judge_saw`.

Eighteen deliberate mutations — each defect fixed on the first pass and the
second, reintroduced one at a time — were run against the suite. All eighteen
are caught by a named test:

```
CAUGHT test_unparseable_response_is_error_not_pass       parse defaults to score=3
CAUGHT test_judge_outage_is_error_not_fail               outage returns FAIL not ERROR
CAUGHT test_missing_groq_key_raises                      missing GROQ_API_KEY becomes a mock
CAUGHT test_pool_without_a_da_layer_never_claims_...     no-DA pool claims blob_verified=True
CAUGHT test_missing_blob_is_a_fraud_proof                DA read-back falls back to provider copy
CAUGHT test_quorum_not_reached_is_error_not_a_guess      quorum failure guesses a verdict
CAUGHT test_loader_refuses_to_substitute_a_different...  loader substitutes the curated corpus
CAUGHT test_loader_will_not_quietly_return_fewer...      loader returns fewer than asked
CAUGHT test_curated_fallback_is_opt_in_and_labels...     curated rows relabelled as TruthfulQA
CAUGHT test_unattested_judge_model_is_marked_never...    model name backfilled from the request
CAUGHT test_an_outage_does_not_inherit_the_model...      outage inherits an earlier call's model
CAUGHT test_unknown_validity_method_raises_rather...     unknown method falls through to lexical
CAUGHT test_harness_per_strategy_denominator_follows...  harness uses the GLOBAL honest list
CAUGHT test_harness_never_names_a_judge_on_a_row...      judge-free row stamped with a model
CAUGHT test_fraud_rows_do_not_read_as_generator_output   fraud rows labelled as generator output
CAUGHT test_integration_rows_name_the_backend...         integration drops backend/model columns
CAUGHT test_integration_records_a_withheld_job...        integration settles a judge-ERROR job
CAUGHT test_paraphrase_rejects_an_answer_source...       paraphrase accepts an unimplemented source
```

---

## Known limits

* Recall on this local judge is 65% at N=10. That is a real measurement of a 2.1B
  judge and should not be extrapolated to a larger one.
* The reported precision comes from a self-evaluating pair (same model judges and
  generates) and is an upper bound. Running the generator and the judge on
  different models is the obvious next experiment; the flags for it already exist.
* N=10 questions. Confidence intervals on a 40/10 split are wide; the harness
  takes `--subset-size` up to the 60 cached questions.
* `--validity-check llm` is implemented in `fraud_injector.check_validity` but is
  not wired to a CLI choice yet — only `none` and `lexical` are exposed, because
  the LLM path has not been measured here. `run()` now rejects `llm` up front
  rather than failing forty corruptions into a run with a missing `llm_fn`.
* The paraphraser runs at temperature 0.8, so the paraphrase flip *rate* does not
  reproduce to the decimal between runs (18.2% and 25.0% on the same 12
  questions). The finding reproduces; the number is noisy at n=12.
* `datasets` is not installed in this environment, so the loader cannot download
  TruthfulQA. It works off `data/truthfulqa_subset.csv` (60 questions) and now
  **raises** on a cache miss instead of substituting the repo's own questions.
  `pip install datasets` restores the download path; `allow_curated_fallback=True`
  is the escape hatch, and it labels every row it produces.
* The DA layer is the local Merkle-committed store from `edgegrid/da.py`, not
  Celestia. `edgegrid/da.py` documents exactly which guarantee that does and does
  not buy.
