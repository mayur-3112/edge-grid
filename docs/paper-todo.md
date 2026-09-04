# The Edge Grid -- Paper Draft: Open Items for the Human Author

*Moved out of `docs/paper.md` so the manuscript itself reads as publication-ready. This file is internal tracking, not part of the paper.*

## TODO for the human author

**Missing experiments / data needed before this draft is submission-ready:**

1. **Judge-panel experiment is incomplete.** Two of the four panel members
   (`nemotron-120b`, `ling-3-flash`) returned usable data on under 20% of items due to free-tier
   API rate limiting. Section 5.4's "capability, not lineage" finding rests on only the two
   complete members (one same-family, one unrelated-family), not the four originally intended.
   Re-run with paid API access or self-hosted judges before this finding is cited without the
   caveat currently attached to it.
2. **No human-adjudicated honest control set exists.** Every precision/false-positive figure in
   Section 5.4 and Section 6.2 is confounded by the fallibility of a model-generated honest
   answer. This is flagged throughout the draft but not fixed; Future Work item #3 names the
   remedy.
3. **No multi-machine deployment.** Every network measurement, including the container-topology
   auction (Section 5.3), shares one kernel. This is the single largest unaddressed threat to
   validity per Section 6.1 and Future Work item #1.
4. **Auction convergence "without the bid window"** — `docs/paper-factsheet.md` open question #6
   notes this derived figure (which `docs/EXPERIMENTS.md`'s protocol requires alongside the raw,
   window-inclusive figure) was not found as a separate computed column in the summary CSV. This
   draft reports only the bid-arrival times, which carry the scaling signal, but a reviewer may
   ask for the explicit without-window figure.
5. **No adaptive-adversary / red-team experiment exists** (Future Work item #5) — every fraud
   strategy in Section 5.4 is fixed and non-adaptive; this is stated as an open limitation, not
   fixed here.
6. **Files not independently opened during fact-sheet construction**: `edgegrid/chain.py`,
   `verification/evaluator.py` (the `Judge` base class implementation and exact judge-prompt
   wording), `gateway/`, `sdk/`. No claim in this draft depends on their exact contents beyond
   what call sites already established, but a claim about exact prompt wording or chain-backend
   selection semantics should be checked against these files directly before publication.
7. **`docs/EXPERIMENTS.md` does not document the judge-panel, weights, or netem-swarm
   experiments** that this paper's Section 5.3-5.5 rely on — it should be updated to match, or the
   paper should cite `ch8_results.md`/`docs/paper-factsheet.md` directly as the methodology
   source for those three experiments instead.
8. **Citation numbers [3]-[27] above are reproduced from `docs/REFERENCES.md` but were not
   independently re-verified against primary sources in this drafting pass** — that document's
   own audit trail (§5 of `docs/REFERENCES.md`) is the verification record; a final author pass
   should re-check that every in-text citation number in Section 2 actually matches the claim it
   is attached to, since this draft assembled the citations from the reference list's stated
   subject matter rather than by re-reading each source.
9. **Figures are referenced by placeholder** (`\ref{fig:architecture}`, `\ref{fig:sequence}`, and
   the named `docs/figures/fig_*.png` files) but have not been re-typeset or captioned for this
   paper's specific figure numbering; `docs/figures/` already contains rendered PNGs for every
   experiment referenced in Section 5 and should be wired in directly.
10. **Venue formatting** (page/word limits, citation style, anonymization if required) has not
    been applied — this draft is content-complete but not typeset to any specific venue's
    template.
