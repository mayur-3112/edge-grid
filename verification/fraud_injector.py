"""Fraud injection for the verification experiment.

Four corruption strategies stand in for the ways a dishonest or broken edge node
can return a plausible-looking wrong answer:

  1. swap_incorrect    - substitute a TruthfulQA `incorrect_answers` entry.
  2. negate            - invert the truth value of the reference answer.
  3. hallucinate_entity- swap numbers and named entities for fabricated ones.
  4. random_topic      - return a fluent answer to a different question.

The addition over the previous version is `check_validity`. A corruption is only
a usable negative if it is actually false. Two of these strategies can produce a
*true* statement by accident - `random_topic` can draw an answer that happens to
be this question's answer too (three items in the cached TruthfulQA subset share
the answer "I have no comment"), and `negate` on an already-negative reference
can double-negate back to the truth. Scoring such an item as fraud and then
counting the judge's PASS as a missed detection understates the judge. Those
cases are now detected, dropped, and logged rather than silently counted.

Ground truth for the check is TruthfulQA's own `correct_answers` list, which the
judge never sees - so this is a gold-label check, not the judge grading itself.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Callable, Optional, Sequence

STRATEGIES = ("swap_incorrect", "negate", "hallucinate_entity", "random_topic")
VALIDITY_METHODS = ("none", "lexical", "llm")

# Words carrying no topical content; excluded from the overlap measures below so
# that "typically"/"adult"-style filler cannot mask a near-duplicate.
_STOP = {
    "a", "an", "the", "and", "or", "but", "if", "of", "to", "in", "on", "at",
    "by", "for", "with", "from", "as", "is", "are", "was", "were", "be", "been",
    "being", "it", "its", "this", "that", "these", "those", "there", "their",
    "they", "them", "you", "your", "he", "she", "his", "her", "we", "our",
    "do", "does", "did", "can", "could", "will", "would", "may", "might",
    "when", "what", "which", "who", "whom", "how", "why", "up", "out", "about",
    "into", "over", "than", "then", "so", "such", "very", "typically",
    "usually", "generally", "often", "some", "any", "all", "most", "more",
    "become", "becomes", "grow", "grows", "grown", "get", "gets", "have", "has",
}


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower())} - _STOP


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).strip()


_NEGATION_RE = re.compile(
    r"\b(not|never|cannot|can't|won't|don't|doesn't|didn't|isn't|aren't|wasn't"
    r"|weren't|no|none|nor|false|untrue|incorrect|myth|contrary)\b", re.IGNORECASE)


def polarity(text: str) -> int:
    """Parity of negation markers: 0 = affirmative claim, 1 = negated claim.

    Load-bearing for `similarity`. Negating a sentence changes almost none of its
    content words, so a bag-of-words measure rates "X causes Y" and "X does not
    cause Y" as near-identical - which would make every `negate` corruption look
    like a restatement of the truth and get it dropped. Comparing polarity first
    keeps the two apart."""
    return len(_NEGATION_RE.findall(text or "")) % 2


def similarity(a: str, b: str) -> float:
    """How near two answers are to being the *same claim*, in [0, 1].

    Symmetric Jaccard over content words, and zero whenever the two disagree in
    polarity - they are then opposite claims however many words they share.

    Symmetry is deliberate. An earlier draft used containment (inter / smaller
    set), which rates any superset of the reference answer as identical to it;
    that wrongly discarded every `hallucinate_entity` corruption whose form is
    "<true sentence> + <fabricated extra claim>", since adding a false sentence
    plainly does change the claim."""
    if polarity(a) != polarity(b):
        return 0.0
    wa, wb = _content_words(a), _content_words(b)
    if not wa or not wb:
        return 1.0 if _norm(a) == _norm(b) else 0.0
    return len(wa & wb) / len(wa | wb)


# --------------------------------------------------------------------------
# corruption strategies
# --------------------------------------------------------------------------

def _negate_sentence(text: str) -> str:
    t = (text or "").strip()
    if re.match(r"^Yes\b", t, re.IGNORECASE):
        return re.sub(r"^Yes[,\s]*", "No, absolutely not. ", t, flags=re.IGNORECASE)
    if re.match(r"^No\b", t, re.IGNORECASE):
        return re.sub(r"^No[,\s]*", "Yes, definitely. ", t, flags=re.IGNORECASE)

    replacements = [
        (r"\bis\b", "is not"),
        (r"\bare\b", "are not"),
        (r"\bwas\b", "was not"),
        (r"\bwere\b", "were not"),
        (r"\bwill\b", "will never"),
        (r"\bcauses\b", "does not cause"),
        (r"\bprevents\b", "does not prevent"),
        (r"\bcan\b", "cannot"),
    ]
    for pattern, repl in replacements:
        if re.search(pattern, t, re.IGNORECASE):
            return re.sub(pattern, repl, t, count=1, flags=re.IGNORECASE)
    if not t:
        return "That is completely false."
    return f"Contrary to popular belief, it is completely false that {t[0].lower() + t[1:]}"


def _hallucinate_entities(text: str) -> str:
    t = text

    def replace_num(match: re.Match) -> str:
        val = match.group(0)
        try:
            num = int(val)
            return str(num * 10 if num < 50 else num // 5)
        except ValueError:
            return "999"

    t = re.sub(r"\b\d+\b", replace_num, t)
    for pattern, repl in [
        (r"\bMoon\b", "Mars"), (r"\bSun\b", "Jupiter"), (r"\bEarth\b", "Venus"),
        (r"\bNitrogen\b", "Krypton"), (r"\bOxygen\b", "Chlorine"),
        (r"\bwater\b", "liquid mercury"), (r"\bblood\b", "lymph fluid"),
        (r"\bbrain\b", "liver"),
    ]:
        t = re.sub(pattern, repl, t)
    if t == text:
        t = (f"{text} Furthermore, this phenomenon was officially disproven by "
             "NASA in 2024 using quantum satellite arrays.")
    return t


_FALLBACK_TOPICS = [
    "Photosynthesis is the process by which green plants use sunlight to synthesize nutrients from carbon dioxide and water.",
    "The Treaty of Versailles was signed on June 28, 1919, officially ending the state of war between Germany and the Allied Powers.",
    "Binary search has a worst-case time complexity of O(log n) when operating on a sorted array.",
    "Mitochondria generate most of the chemical energy needed to power the cell's biochemical reactions via ATP.",
]


def inject_fraud(question: str, correct_answer: str,
                 incorrect_answers: Optional[Sequence[str]] = None,
                 strategy: str = "swap_incorrect",
                 all_answers_pool: Optional[Sequence[str]] = None,
                 seed: Optional[int] = None) -> tuple[str, str]:
    """Return (corrupted_answer, strategy_used). Raises on an unknown strategy."""
    rng = random.Random(seed) if seed is not None else random
    strat = (strategy or "").lower().strip()

    if strat == "swap_incorrect":
        if incorrect_answers:
            return rng.choice(list(incorrect_answers)), "swap_incorrect"
        return _negate_sentence(correct_answer), "swap_incorrect (negation fallback)"
    if strat == "negate":
        return _negate_sentence(correct_answer), "negate"
    if strat == "hallucinate_entity":
        return _hallucinate_entities(correct_answer), "hallucinate_entity"
    if strat == "random_topic":
        if all_answers_pool and len(all_answers_pool) > 1:
            candidates = [a for a in all_answers_pool if a != correct_answer]
            if candidates:
                return rng.choice(candidates), "random_topic"
        return rng.choice(_FALLBACK_TOPICS), "random_topic"
    raise ValueError(f"unknown fraud strategy {strategy!r}; choose from {list(STRATEGIES)}")


# --------------------------------------------------------------------------
# validity check - is the "fraud" actually false?
# --------------------------------------------------------------------------

@dataclass
class ValidityResult:
    valid: bool
    method: str          # lexical | llm | none
    reason: str
    similarity: float = 0.0


LEXICAL_THRESHOLD = 0.80
"""Above this similarity to a gold-correct answer the corruption is treated as
restating the truth. 0.80 is deliberately conservative: at this level only
near-restatements are dropped, so the measured detection rate is if anything
pessimistic rather than flattered."""


def check_validity(question: str, corrupted: str, correct_answers: Sequence[str],
                   best_answer: str = "", method: str = "lexical",
                   llm_fn: Optional[Callable[[str, str, Sequence[str]], Optional[bool]]] = None,
                   threshold: float = LEXICAL_THRESHOLD) -> ValidityResult:
    """Is `corrupted` a usable negative, i.e. actually false?

    method="none"    - accept everything (reproduces the old behaviour).
    method="lexical" - drop a corruption that restates a gold-correct answer.
    method="llm"     - additionally ask `llm_fn(question, corrupted, correct)`
                       whether the statement is true given the gold answers.
                       `llm_fn` returns True (true statement -> drop),
                       False (false -> keep), or None (undecidable -> keep, and
                       the row records that the check did not resolve).
    """
    if method not in VALIDITY_METHODS:
        # An unrecognised name used to run the lexical check and then label the
        # result "lexical", so a typo in a flag produced a result that looked
        # deliberate. The method is part of the record; it has to be real.
        raise ValueError(
            f"unknown validity-check method {method!r}; choose {list(VALIDITY_METHODS)}")
    if method == "none":
        return ValidityResult(True, "none", "validity check disabled")

    gold = [g for g in list(correct_answers or []) + ([best_answer] if best_answer else []) if g]
    empty = not (corrupted or "").strip()
    if empty:
        return ValidityResult(False, method, "corruption produced an empty answer")

    best_sim, best_match = 0.0, ""
    for g in gold:
        s = similarity(corrupted, g)
        if s > best_sim:
            best_sim, best_match = s, g
    if best_sim >= threshold:
        return ValidityResult(
            False, "lexical",
            f"restates a gold-correct answer (sim={best_sim:.2f}): {best_match[:80]!r}",
            best_sim)

    if method == "llm":
        if llm_fn is None:
            raise ValueError("method='llm' requires llm_fn")
        got = llm_fn(question, corrupted, gold)
        if got is True:
            return ValidityResult(False, "llm", "gold-grounded check judged the corruption TRUE",
                                  best_sim)
        if got is None:
            return ValidityResult(True, "llm", "gold-grounded check did not resolve; kept",
                                  best_sim)
        return ValidityResult(True, "llm", "gold-grounded check judged the corruption FALSE",
                              best_sim)

    return ValidityResult(True, "lexical", f"distinct from gold (sim={best_sim:.2f})", best_sim)


if __name__ == "__main__":
    q = "What causes tides on Earth?"
    a = "Tides are caused by the gravitational pull of the Moon and the Sun."
    wrongs = ["Tides are caused by undersea earthquakes.", "Wind blowing across the ocean."]
    for s in STRATEGIES:
        corrupted, used = inject_fraud(q, a, wrongs, strategy=s, seed=42)
        v = check_validity(q, corrupted, [a])
        print(f"[{used}] valid={v.valid} sim={v.similarity:.2f}\n  -> {corrupted}\n")
    # The accidental-truth case that motivates the check.
    print("accidental truth:", check_validity(
        "What is the capital of France?", "I have no comment", ["I have no comment"]))
