"""LLM-as-judge for the Edge Grid verification track.

A verdict from this module is the input to a slashing decision, so the design
rule is that *nothing may be inferred*. Three things went wrong in the previous
version and each is now structurally impossible:

  * a judge outage returned score=1 / verdict=FAIL, so an outage read as
    unanimous fraud detection. There is now a third outcome, `VerdictKind.ERROR`,
    and an exhausted retry budget produces it.
  * an unparseable response fell back to score=3, which is exactly the pass
    threshold, so garbage silently became a PASS. `_parse` now raises rather
    than guessing, and the caller turns that into ERROR.
  * a missing GROQ_API_KEY silently swapped in a mock whose keyword list was
    lifted from the project's own fixtures. The backend is now always explicit:
    a missing key raises `JudgeConfigError`, and the mock is only reachable by
    asking for it by name.

The model recorded on a `Verdict` is the one the *server* reports for the call
that produced it, not the string passed on the command line - the two differ
whenever a name is aliased or silently substituted.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Callable, Optional

import httpx

from edgegrid import config as C
from edgegrid.schemas import Verdict, VerdictKind

BACKENDS = ("groq", "openrouter", "ollama", "mock")

JUDGE_SYSTEM_PROMPT = """You are an expert fact-checker grading AI-generated answers for factual accuracy.

Scoring Rubric:
5 = Completely correct, accurate, and directly answers the question with no falsehoods.
4 = Mostly correct with minor omissions or slight imprecision, but fundamentally truthful.
3 = Partially correct, but contains notable inaccuracies, ambiguities, or misleading framing.
2 = Mostly incorrect, contains clear factual errors or hallucinations.
1 = Completely wrong, fabricated, nonsensical, or entirely off-topic.

Judge only factual accuracy. Do not reward or penalise style, length, or hedging.

You MUST respond ONLY with a valid JSON object in this exact format:
{"score": <integer 1-5>, "verdict": "<PASS or FAIL>", "reason": "<one sentence>"}"""

JUDGE_USER_PROMPT = """Question: {prompt}
AI-Generated Answer: {output}

Evaluate the answer now."""

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_OPEN_THINK_RE = re.compile(r"<think>.*\Z", re.DOTALL | re.IGNORECASE)
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


class JudgeConfigError(RuntimeError):
    """The judge cannot be constructed as asked. Never downgraded to a fallback."""


class JudgeParseError(ValueError):
    """The backend answered but the answer carries no usable score."""


def strip_think(text: str) -> str:
    """Remove qwen3-style reasoning blocks.

    qwen3 emits `<think>...</think>` before its JSON even when `format: json` is
    requested, and an unterminated block appears whenever the reply is cut off by
    the token budget. The previous judge was defeated by exactly this: the block
    made every response unparseable, which the old fallback turned into score=3,
    a silent PASS."""
    text = _THINK_RE.sub("", text)
    text = _OPEN_THINK_RE.sub("", text)
    return text.strip()


def _parse(text: str) -> tuple[int, str, str]:
    """(score, self_verdict, reason) or raise.

    `self_verdict` is the label the model wrote, kept only so the harness can
    measure how often the model contradicts its own rubric. It is never used to
    decide the verdict - that is derived from the score alone, so one threshold
    governs every backend."""
    cleaned = strip_think(text)
    if not cleaned:
        raise JudgeParseError("empty response after stripping reasoning block")

    data: Optional[dict] = None
    try:
        obj = json.loads(cleaned)
        if isinstance(obj, dict):
            data = obj
    except Exception:
        m = _JSON_RE.search(cleaned)
        if m:
            try:
                obj = json.loads(m.group(0))
                if isinstance(obj, dict):
                    data = obj
            except Exception:
                data = None

    if data is not None and "score" in data:
        try:
            score = int(float(str(data["score"]).strip()))
        except (TypeError, ValueError):
            raise JudgeParseError(f"score field is not a number: {data.get('score')!r}")
        if not 1 <= score <= 5:
            raise JudgeParseError(f"score {score} outside the 1-5 rubric")
        self_verdict = str(data.get("verdict", "")).strip().lower()
        if self_verdict not in ("pass", "fail"):
            self_verdict = ""
        return score, self_verdict, str(data.get("reason", "")).strip()[:400]

    # No JSON object: accept a bare "score: N" only, and nothing weaker. A
    # response with no recoverable score is an ERROR, never a default.
    m = re.search(r"\bscore\b\W{0,4}([1-5])\b", cleaned, re.IGNORECASE)
    if not m:
        raise JudgeParseError(f"no score in response: {cleaned[:160]!r}")
    up = cleaned.upper()
    self_verdict = ""
    if "PASS" in up and "FAIL" not in up:
        self_verdict = "pass"
    elif "FAIL" in up and "PASS" not in up:
        self_verdict = "fail"
    return int(m.group(1)), self_verdict, cleaned[:400].replace("\n", " ")


def _mock_raw(prompt: str, output: str) -> str:
    """Deterministic lexical stand-in used for offline smoke tests and unit tests.

    It is NOT a measurement instrument and every row it produces is tagged
    `judge_backend=mock` so it can be excluded from any reported figure. Its cues
    are generic English contradiction markers chosen without reference to any
    fixture in this repo - the previous mock keyed on strings copied out of the
    project's own test data, which made it look far more accurate than it was."""
    o = output.lower()
    contradiction = ("not ", "never", "no, ", "cannot", "false that",
                     "contrary to", "disproven")
    stop = {"what", "who", "when", "where", "why", "how", "is", "are", "was",
            "were", "the", "a", "an", "of", "to", "in", "on", "do", "does",
            "did", "you", "your", "it", "and", "or", "if", "that", "this"}
    p_words = {w for w in re.findall(r"[a-z]{3,}", prompt.lower())} - stop
    overlap = sum(1 for w in p_words if w in o)
    if any(c in o for c in contradiction) or overlap == 0:
        return json.dumps({"score": 1, "verdict": "FAIL",
                           "reason": "mock: contradiction cue or no topical overlap"})
    return json.dumps({"score": 5, "verdict": "PASS",
                       "reason": "mock: topically on-prompt, no contradiction cue"})


class Judge:
    """One judge backend. `backend` is always explicit; nothing is inferred.

    Args:
        backend: "groq" | "ollama" | "mock".
        model: model id to request. The id actually used is read back from the
            response and is what lands on the Verdict.
        mock_response: mock backend only - a raw response string, a callable
            (prompt, output) -> str, or an Exception instance to raise. Lets a
            test drive the outage and unparseable paths without a network.
    """

    def __init__(self, backend: str, model: Optional[str] = None,
                 api_key: Optional[str] = None,
                 pass_threshold: int = C.PASS_THRESHOLD,
                 max_retries: int = 2, timeout_s: float = 120.0,
                 base_url: Optional[str] = None, temperature: float = 0.0,
                 num_predict: int = 220,
                 json_mode: bool = True,
                 mock_response: Any = None):
        b = (backend or "").strip().lower()
        if b not in BACKENDS:
            raise JudgeConfigError(
                f"unknown judge backend {backend!r}; choose one of {list(BACKENDS)}. "
                "There is no 'auto' - the backend must be stated so it can be recorded.")
        self.backend = b
        self.pass_threshold = pass_threshold
        self.max_retries = max_retries
        self.timeout_s = timeout_s
        self.temperature = temperature
        self.num_predict = num_predict
        # Strict server-side JSON is a constraint some models simply fail: on Groq
        # the gpt-oss family returns 400 'Failed to validate JSON' on a large
        # fraction of calls. `_parse` extracts an object from prose anyway, so
        # the constraint can be dropped per backend without weakening parsing.
        self.json_mode = json_mode
        self._mock_response = mock_response
        self.client: Any = None

        if b == "groq":
            key = api_key if api_key is not None else C.GROQ_API_KEY
            if not key:
                raise JudgeConfigError(
                    "GROQ_API_KEY is not set. The groq backend is a hard requirement "
                    "once selected: it will not silently degrade to a mock. Set the key, "
                    "or pass --judge-backend ollama (real local judge) or "
                    "--judge-backend mock (tagged, non-reportable).")
            try:
                from groq import Groq
            except ImportError as e:
                raise JudgeConfigError(f"the 'groq' package is not installed: {e}") from e
            self.client = Groq(api_key=key)
            self.model_requested = model or C.GROQ_JUDGE_MODEL
            self.base_url = "https://api.groq.com"
        elif b == "openrouter":
            key = api_key if api_key is not None else C.OPENROUTER_API_KEY
            if not key:
                raise JudgeConfigError(
                    "OPENROUTER_API_KEY is not set. Like groq, the openrouter backend is a "
                    "hard requirement once selected and will not degrade to a mock.")
            self.model_requested = model or C.OPENROUTER_JUDGE_MODEL
            self.base_url = (base_url or C.OPENROUTER_BASE_URL).rstrip("/")
            self.client = httpx.Client(
                timeout=timeout_s,
                headers={"Authorization": f"Bearer {key}",
                         # OpenRouter asks callers to identify themselves; these
                         # are attribution headers, not credentials.
                         "HTTP-Referer": "https://github.com/chhhee10/edge-grid",
                         "X-Title": "The Edge Grid"})
        elif b == "ollama":
            self.model_requested = model or C.JUDGE_MODEL
            self.base_url = (base_url or C.OLLAMA_HOST).rstrip("/")
            self.client = httpx.Client(timeout=timeout_s)
        else:
            self.model_requested = model or "mock-lexical-v2"
            self.base_url = ""

        # Set from the first successful call; until then we have nothing to report.
        self.model_used: str = ""

    # -- provenance ------------------------------------------------------

    def _model_label(self, served: str) -> str:
        """What to record as `judge_model`.

        Only a name the server itself reported is recorded bare. A requested
        name that was never confirmed is marked, because the whole point of
        reading the model back is that the two differ whenever a name is aliased
        or substituted - backfilling the request silently erases that."""
        if served:
            return served
        return f"{self.model_requested} (requested; server reported no model)"

    # -- backends --------------------------------------------------------

    def _call_groq(self, prompt: str, output: str) -> tuple[str, str]:
        completion = self.client.chat.completions.create(
            model=self.model_requested,
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": JUDGE_USER_PROMPT.format(prompt=prompt, output=output)},
            ],
            **({"response_format": {"type": "json_object"}} if self.json_mode else {}),
            temperature=self.temperature,
            max_tokens=self.num_predict,
            timeout=self.timeout_s,
        )
        # An empty `served` is propagated as empty, not backfilled with the
        # requested name: `score` labels it so a reader can tell a model the
        # server confirmed from one we merely asked for.
        served = getattr(completion, "model", "") or ""
        return (completion.choices[0].message.content or ""), served

    def _call_openrouter(self, prompt: str, output: str) -> tuple[str, str]:
        """OpenAI-compatible chat completion.

        Reaches families Groq does not serve, which is what makes the diversity
        arm of the judge-panel experiment a real comparison rather than a
        two-point one. Errors are raised, never swallowed: an HTTP failure has
        to become VerdictKind.ERROR upstream and be counted."""
        payload = {
            "model": self.model_requested,
            "messages": [
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user",
                 "content": JUDGE_USER_PROMPT.format(prompt=prompt, output=output)},
            ],
            "temperature": self.temperature,
            "max_tokens": self.num_predict,
        }
        if self.json_mode:
            payload["response_format"] = {"type": "json_object"}
        r = self.client.post(f"{self.base_url}/chat/completions", json=payload)
        r.raise_for_status()
        body = r.json()
        if "choices" not in body:
            raise JudgeParseError(f"no choices in response: {str(body)[:200]}")
        return (body["choices"][0]["message"].get("content") or ""), body.get("model", "") or ""

    def _call_ollama(self, prompt: str, output: str) -> tuple[str, str]:
        r = self.client.post(
            f"{self.base_url}/api/generate",
            json={
                "model": self.model_requested,
                "system": JUDGE_SYSTEM_PROMPT,
                "prompt": JUDGE_USER_PROMPT.format(prompt=prompt, output=output),
                "stream": False,
                "format": "json",
                "options": {"temperature": self.temperature,
                            "num_predict": self.num_predict},
            },
            timeout=self.timeout_s,
        )
        r.raise_for_status()
        body = r.json()
        if "error" in body:
            raise RuntimeError(f"ollama error: {body['error']}")
        return body.get("response", ""), (body.get("model") or "")

    def _call_mock(self, prompt: str, output: str) -> tuple[str, str]:
        mr = self._mock_response
        if isinstance(mr, BaseException):
            raise mr
        if callable(mr):
            return str(mr(prompt, output)), self.model_requested
        if isinstance(mr, str):
            return mr, self.model_requested
        return _mock_raw(prompt, output), self.model_requested

    # -- api -------------------------------------------------------------

    def score(self, prompt: str, output: str, job_id: str = "",
              validator_peer_id: str = "", blob_verified: bool = False) -> Verdict:
        """Judge one answer. Always returns a Verdict; never raises for a
        backend failure - the failure becomes VerdictKind.ERROR so the caller
        counts it separately instead of mistaking it for fraud."""
        call = {"groq": self._call_groq, "openrouter": self._call_openrouter,
                "ollama": self._call_ollama,
                "mock": self._call_mock}[self.backend]
        t0 = time.monotonic()
        last_err = ""
        # Per call, not per instance: a Judge is reused across a whole run, so
        # reading `self.model_used` on an outage would report the model that
        # served some *earlier* item as though it had served this one.
        served_here = ""
        for attempt in range(self.max_retries + 1):
            try:
                raw, served = call(prompt, output)
                served = self._model_label(served)
                served_here = served
                self.model_used = served
                score, self_verdict, reason = _parse(raw)
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                if attempt < self.max_retries:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                return Verdict(
                    job_id=job_id, validator_peer_id=validator_peer_id,
                    verdict=VerdictKind.ERROR, quality_score=None, judge_score=None,
                    reason=f"judge unavailable after {self.max_retries + 1} attempts: {last_err}"[:400],
                    judge_backend=self.backend,
                    # No call succeeded, so no model served this. Marked, not
                    # backfilled: an ERROR row must not carry a model name a
                    # reader could take for the one that produced a judgement.
                    judge_model=(served_here or
                                 f"{self.model_requested} (requested; no successful call)"),
                    blob_verified=blob_verified,
                    latency_ms=(time.monotonic() - t0) * 1000.0)
            kind = VerdictKind.PASS if score >= self.pass_threshold else VerdictKind.FAIL
            if self_verdict and self_verdict != kind.value:
                # Recorded, not obeyed: the score is the single source of truth.
                reason = f"[self_verdict={self_verdict}] {reason}"
            return Verdict(
                job_id=job_id, validator_peer_id=validator_peer_id, verdict=kind,
                quality_score=score, judge_score=float(score), reason=reason[:400],
                judge_backend=self.backend, judge_model=served,
                blob_verified=blob_verified,
                latency_ms=(time.monotonic() - t0) * 1000.0)
        raise AssertionError("unreachable")  # pragma: no cover

    def close(self) -> None:
        if self.backend == "ollama" and self.client is not None:
            self.client.close()


def self_verdict_of(v: Verdict) -> str:
    """The label the model wrote, if it disagreed with the rubric. Empty when it
    agreed or when the verdict is an ERROR."""
    m = re.match(r"\[self_verdict=(pass|fail)\]", v.reason or "")
    return m.group(1) if m else ""


if __name__ == "__main__":
    import sys

    backend = sys.argv[1] if len(sys.argv) > 1 else "ollama"
    j = Judge(backend=backend)
    v = j.score(prompt="What causes ocean tides on Earth?",
                output="Ocean tides are caused mainly by the Moon's gravity.")
    print(json.dumps(json.loads(v.model_dump_json()), indent=2))
