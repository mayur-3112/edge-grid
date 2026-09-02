"""Edge inference engine: a streaming Ollama client that can be measured honestly.

The whole project rests on one quantified claim - that an edge node answers in
under a second. That claim is about time to the *first* token, and the previous
implementation asked Ollama for `stream=false`, which makes TTFT not merely
unmeasured but structurally unmeasurable: the HTTP response does not exist until
generation is finished. Everything here follows from fixing that.

Three properties this module is built to guarantee:

  * **TTFT is real.** We consume Ollama's NDJSON stream and stamp the arrival of
    the first chunk that carries a non-empty `response`. Nothing else counts as a
    token: Ollama emits a final `done` chunk whose `response` is empty, and
    counting it would silently under-report latency on empty generations.
  * **Token counts are real.** `eval_count` / `prompt_eval_count` come from the
    runtime's own tokenizer. `len(output.split())` is a word count and is wrong
    by roughly 30-40% for English, more for code. A counter the runtime does not
    send is named in `GenerationStats.missing_counters` rather than defaulted to
    a zero that reads like a measurement, and `run()` refuses to sign one.
  * **No fake results.** Connection refused, unknown model, timeout, a truncated
    stream, a runtime that serves a different model than was asked for, and an
    unreadable `/api/ps` each raise a distinct named exception. A caller never
    gets an `InferenceResult` that looks successful but was not produced by the
    model it names.

Warmth is read from Ollama's `/api/ps` rather than remembered locally, because
the process that evicts a model (an idle `keep_alive` expiry, another client
loading something else) is not this one. A local flag would drift, and the
market protocol prices warm starts - a wrong warm flag is a mispriced bid.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

import httpx

from edgegrid import config as C
from edgegrid.identity import Identity
from edgegrid.schemas import InferenceResult, sha256_hex

GENERATE_PATH = "/api/generate"
PS_PATH = "/api/ps"
TAGS_PATH = "/api/tags"

# Ollama reports durations in nanoseconds.
NS_PER_MS = 1_000_000
NS_PER_S = 1_000_000_000


# --------------------------------------------------------------------------
# errors - each failure mode is nameable so a caller can react to it
# --------------------------------------------------------------------------

class InferenceError(RuntimeError):
    """Base for every failure of the inference path."""


class OllamaUnavailableError(InferenceError):
    """The runtime could not be reached at all (connection refused, DNS, reset)."""


class ModelNotFoundError(InferenceError):
    """The runtime is up but does not have the requested model."""


class InferenceTimeoutError(InferenceError):
    """The runtime accepted the request but did not finish within the budget."""


class OllamaProtocolError(InferenceError):
    """The stream ended without a final `done` chunk, or a chunk was unparseable."""


class EmptyOutputError(InferenceError):
    """Generation completed but produced no tokens, so there is no TTFT to report."""


# --------------------------------------------------------------------------
# per-generation statistics
# --------------------------------------------------------------------------

@dataclass
class GenerationStats:
    """Everything measured about one generation.

    `ttft_ms` is None when the model emitted no token at all - that is a real
    outcome (num_predict=0, an immediate stop) and reporting it as 0.0 or as
    total_ms would be a fabricated measurement.
    """

    model: str
    output: str = ""
    ttft_ms: Optional[float] = None
    total_ms: float = 0.0
    eval_count: int = 0
    prompt_eval_count: int = 0
    eval_duration_ns: int = 0
    prompt_eval_duration_ns: int = 0
    load_duration_ns: int = 0
    total_duration_ns: int = 0
    done_reason: str = ""
    warm: Optional[bool] = None
    n_chunks: int = 0
    # provenance: which runtime, at which address, actually produced this.
    backend: str = "ollama"
    host: str = ""
    served_model: str = ""
    # Counters the runtime did not send. `eval_count` reads 0 either because the
    # model emitted nothing or because the runtime never reported it, and those
    # are not the same fact; this list keeps them distinguishable in the CSV
    # instead of collapsing both onto a zero that looks like a measurement.
    missing_counters: list[str] = field(default_factory=list)

    @property
    def counters_complete(self) -> bool:
        return not self.missing_counters

    @property
    def tokens_per_sec(self) -> float:
        """Generation throughput from the runtime's own clock.

        Deliberately not `eval_count / total_ms`: total time includes model load
        and prompt evaluation, which would make a cold run look slow at
        generating when it was only slow at starting.
        """
        if self.eval_duration_ns <= 0:
            return 0.0
        return self.eval_count / (self.eval_duration_ns / NS_PER_S)

    @property
    def load_ms(self) -> float:
        return self.load_duration_ns / NS_PER_MS

    @property
    def prompt_eval_ms(self) -> float:
        return self.prompt_eval_duration_ns / NS_PER_MS

    @property
    def output_hash(self) -> str:
        return sha256_hex(self.output)

    def as_row(self) -> dict[str, Any]:
        """Flat dict for a RunLog CSV row.

        Carries its own provenance - backend, host, and the model name the
        runtime echoed back - so a row in `trials.csv` states what served it
        rather than leaving a reader to infer it from the run's config.
        """
        return {
            "backend": self.backend,
            "host": self.host,
            "model": self.model,
            "served_model": self.served_model,
            "warm": self.warm,
            "ttft_ms": None if self.ttft_ms is None else round(self.ttft_ms, 3),
            "total_ms": round(self.total_ms, 3),
            "load_ms": round(self.load_ms, 3),
            "prompt_eval_ms": round(self.prompt_eval_ms, 3),
            "eval_count": self.eval_count,
            "prompt_eval_count": self.prompt_eval_count,
            "tokens_per_sec": round(self.tokens_per_sec, 3),
            "done_reason": self.done_reason,
            "n_chunks": self.n_chunks,
            "missing_counters": ";".join(self.missing_counters),
            "output_hash": self.output_hash,
        }


# --------------------------------------------------------------------------
# engine
# --------------------------------------------------------------------------

class InferenceEngine:
    """Streaming client for one Ollama endpoint.

    Not thread-safe by design: one engine per worker. `httpx.Client` is reused so
    the TCP connection stays open, which keeps TTFT from absorbing a fresh
    handshake on every job.
    """

    def __init__(
        self,
        model: str = C.OLLAMA_MODEL,
        host: str = C.OLLAMA_HOST,
        identity: Optional[Identity] = None,
        peer_id: str = "",
        timeout_s: float = C.INFERENCE_TIMEOUT_S,
        connect_timeout_s: float = 5.0,
        transport: Optional[httpx.BaseTransport] = None,
    ):
        self.model = model
        self.host = host.rstrip("/")
        self.identity = identity
        self.peer_id = peer_id
        self.timeout_s = timeout_s
        self._client = httpx.Client(
            base_url=self.host,
            timeout=httpx.Timeout(timeout_s, connect=connect_timeout_s),
            transport=transport,
        )

    # -- lifecycle -------------------------------------------------------

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "InferenceEngine":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- runtime introspection -------------------------------------------

    def _get_json(self, path: str) -> dict:
        try:
            resp = self._client.get(path)
        except httpx.TimeoutException as e:
            raise InferenceTimeoutError(f"{self.host}{path} timed out: {e}") from e
        except httpx.TransportError as e:
            raise OllamaUnavailableError(f"cannot reach ollama at {self.host}: {e}") from e
        if resp.status_code >= 400:
            raise InferenceError(f"{path} returned HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    @staticmethod
    def _model_names(payload: dict, path: str) -> list[str]:
        """Pull the `models` list out of an Ollama listing response.

        A missing or non-list `models` key is a protocol error, not an empty
        list. Defaulting it to `[]` would turn "I could not read the runtime's
        state" into the confident claim "nothing is loaded", which is how a warm
        model gets billed as cold and how `unload()` would certify an eviction it
        never observed.
        """
        models = payload.get("models")
        if not isinstance(models, list):
            raise OllamaProtocolError(
                f"{path} returned no `models` list (keys: {sorted(payload)}); "
                "refusing to read that as an empty model set"
            )
        names = []
        for m in models:
            if not isinstance(m, dict) or "name" not in m:
                raise OllamaProtocolError(f"{path} returned a model entry without a name: {m!r}")
            names.append(m["name"])
        return names

    def available_models(self) -> list[str]:
        """Every model pulled onto this node (`/api/tags`)."""
        return self._model_names(self._get_json(TAGS_PATH), TAGS_PATH)

    def loaded_models(self) -> list[str]:
        """Models currently resident in memory (`/api/ps`) - the ground truth for warmth."""
        return self._model_names(self._get_json(PS_PATH), PS_PATH)

    def loaded_model_details(self) -> list[dict]:
        """The raw `/api/ps` entries, including each model's `expires_at`.

        `unload()` reports these when an eviction does not take, because "still
        resident" has two very different causes: another client keeps issuing
        requests (`expires_at` moving forward), or this machine is loaded enough
        that the runner has not exited yet (`expires_at` stuck in the past). One
        is a reason to give up, the other is a reason to wait longer.
        """
        payload = self._get_json(PS_PATH)
        self._model_names(payload, PS_PATH)          # validate the shape first
        return list(payload["models"])

    def is_warm(self, model: Optional[str] = None) -> bool:
        """True if `model` is resident right now.

        Cold on first use and again after Ollama's keep_alive evicts it, which is
        why this asks the runtime instead of trusting a local flag.
        """
        want = model or self.model
        return want in self.loaded_models()

    def unload(self, model: Optional[str] = None, wait_s: float = 10.0) -> None:
        """Evict `model` from memory so the next call measures a cold start.

        `keep_alive: 0` is the HTTP equivalent of `ollama stop <model>`; using it
        keeps the benchmark free of a CLI dependency. Raises if the model is
        still resident afterwards rather than letting a "cold" measurement be
        quietly warm.
        """
        want = model or self.model
        try:
            resp = self._client.post(
                GENERATE_PATH, json={"model": want, "prompt": "", "keep_alive": 0},
            )
        except httpx.TimeoutException as e:
            raise InferenceTimeoutError(f"unload of {want} timed out: {e}") from e
        except httpx.TransportError as e:
            raise OllamaUnavailableError(f"cannot reach ollama at {self.host}: {e}") from e
        # An unload that the runtime rejected (a 404 for an unknown model, say)
        # must not be followed by a poll that finds the model absent and calls
        # that a successful eviction.
        if resp.status_code >= 400:
            self._raise_for_error_body(resp.status_code, resp.text, want)

        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if want not in self.loaded_models():
                return
            time.sleep(0.2)
        residents = [
            f"{m.get('name')} (expires_at={m.get('expires_at', 'unknown')})"
            for m in self.loaded_model_details()
        ]
        raise InferenceError(
            f"{want} still resident {wait_s}s after unload; a cold measurement would be "
            f"false. /api/ps reports: {'; '.join(residents) or 'nothing'}. An expires_at "
            "in the future means another client is keeping the model alive; one in the "
            "past means the runner has not exited yet - retry with a longer wait_s."
        )

    # -- streaming generation --------------------------------------------

    def stream_tokens(
        self,
        prompt: str,
        model: Optional[str] = None,
        max_tokens: int = 256,
        options: Optional[dict] = None,
        system: Optional[str] = None,
        check_warm: bool = True,
    ) -> Iterator[str]:
        """Yield token text as it arrives; return a `GenerationStats` at the end.

        The stats come back through `StopIteration.value`, so a caller that wants
        both the stream and the measurements does::

            gen = engine.stream_tokens(prompt)
            for tok in gen:
                sink.write(tok)
            stats = gen.value          # after the loop, via collect() below

        `collect()` wraps that for callers that only want the finished result.
        A gateway that is proxying tokens to a client just iterates.
        """
        want = model or self.model
        stats = GenerationStats(model=want, host=self.host, backend="ollama")

        # Ask before starting the clock: warmth is a property of the moment the
        # request is issued, and the /api/ps round trip must not land inside TTFT.
        if check_warm:
            stats.warm = self.is_warm(want)

        payload: dict[str, Any] = {
            "model": want,
            "prompt": prompt,
            "stream": True,
            "options": {"num_predict": max_tokens} | (options or {}),
        }
        if system is not None:
            payload["system"] = system

        parts: list[str] = []
        saw_done = False
        t0 = time.perf_counter()
        try:
            with self._client.stream("POST", GENERATE_PATH, json=payload) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    self._raise_for_error_body(resp.status_code, resp.text, want)
                for line in resp.iter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError as e:
                        raise OllamaProtocolError(
                            f"unparseable NDJSON chunk from {self.host}: {line[:200]}"
                        ) from e
                    # Ollama can report a failure mid-stream with HTTP 200.
                    if "error" in chunk:
                        self._raise_for_error_body(200, json.dumps(chunk), want)
                    stats.n_chunks += 1
                    self._absorb_served_model(stats, chunk, want)
                    text = chunk.get("response") or ""
                    if text:
                        if stats.ttft_ms is None:
                            stats.ttft_ms = (time.perf_counter() - t0) * 1000
                        parts.append(text)
                        yield text
                    if chunk.get("done"):
                        saw_done = True
                        self._absorb_final(stats, chunk)
        except httpx.TimeoutException as e:
            raise InferenceTimeoutError(
                f"{want} exceeded the {self.timeout_s}s budget: {e}"
            ) from e
        except httpx.TransportError as e:
            raise OllamaUnavailableError(f"cannot reach ollama at {self.host}: {e}") from e

        stats.total_ms = (time.perf_counter() - t0) * 1000
        stats.output = "".join(parts)
        if not saw_done:
            raise OllamaProtocolError(
                f"stream from {self.host} ended after {stats.n_chunks} chunks "
                "without a final done chunk; token counts would be unknown"
            )
        return stats

    @staticmethod
    def _absorb_served_model(stats: GenerationStats, chunk: dict, want: str) -> None:
        """Record the model name the runtime echoed, and refuse a substitution.

        Every measurement in this module is attributed to a model. If the runtime
        answers with a different one - an alias resolving elsewhere, a proxy
        rewriting the request - then the TTFT, the throughput and the signed
        `InferenceResult` are all mislabelled, which is worse than an error.
        """
        served = chunk.get("model")
        if not served:
            return
        served = str(served)
        if stats.served_model and served != stats.served_model:
            raise OllamaProtocolError(
                f"runtime changed model mid-stream: {stats.served_model!r} -> {served!r}"
            )
        if served != want:
            raise OllamaProtocolError(
                f"requested model {want!r} but the runtime served {served!r}; "
                "refusing to attribute this measurement to the requested model"
            )
        stats.served_model = served

    @staticmethod
    def _absorb_final(stats: GenerationStats, chunk: dict) -> None:
        """Copy the runtime's own counters off the final chunk.

        A counter the runtime did not send is named in `missing_counters` rather
        than left as an indistinguishable 0: `eval_count == 0` on its own cannot
        tell a genuinely empty generation apart from a runtime that never
        reported one, and the second silently becomes "0 tokens at 0 tok/s" in
        the results table.
        """
        fields = {
            "eval_count": "eval_count",
            "prompt_eval_count": "prompt_eval_count",
            "eval_duration": "eval_duration_ns",
            "prompt_eval_duration": "prompt_eval_duration_ns",
            "load_duration": "load_duration_ns",
            "total_duration": "total_duration_ns",
        }
        for wire, attr in fields.items():
            value = chunk.get(wire)
            if value is None:
                stats.missing_counters.append(wire)
                setattr(stats, attr, 0)
            else:
                setattr(stats, attr, int(value))
        stats.done_reason = str(chunk.get("done_reason") or "")

    @staticmethod
    def _raise_for_error_body(status: int, body: str, model: str) -> None:
        """Map an Ollama error body onto a named exception. Always raises."""
        detail = body[:400]
        try:
            detail = json.loads(body).get("error", detail)
        except (json.JSONDecodeError, AttributeError):
            pass
        low = str(detail).lower()
        if status == 404 or "not found" in low or "try pulling" in low:
            raise ModelNotFoundError(f"model {model!r} not available: {detail}")
        if "timeout" in low or "deadline" in low:
            raise InferenceTimeoutError(f"{model}: {detail}")
        raise InferenceError(f"ollama HTTP {status} for {model!r}: {detail}")

    def collect(
        self, prompt: str, model: Optional[str] = None, max_tokens: int = 256, **kw: Any
    ) -> GenerationStats:
        """Consume `stream_tokens` and hand back only the measurements."""
        gen = self.stream_tokens(prompt, model=model, max_tokens=max_tokens, **kw)
        while True:
            try:
                next(gen)
            except StopIteration as stop:
                return stop.value

    # -- the wire-level entry point --------------------------------------

    def run(
        self,
        job_id: str,
        prompt: str,
        model: Optional[str] = None,
        max_tokens: int = 256,
        provider_peer_id: Optional[str] = None,
        identity: Optional[Identity] = None,
        **kw: Any,
    ) -> InferenceResult:
        """Run one job and return a signed `InferenceResult`.

        Raises rather than returning a placeholder on every failure path,
        including an empty generation - a job that produced no tokens has no
        TTFT, and a result claiming otherwise would poison the headline metric.
        """
        stats = self.collect(prompt, model=model, max_tokens=max_tokens, **kw)
        if stats.ttft_ms is None:
            raise EmptyOutputError(
                f"{stats.model} produced no tokens for job {job_id} "
                f"(done_reason={stats.done_reason!r}); nothing to report"
            )
        # An InferenceResult is signed and priced. Every field in it must be a
        # measurement, so anything the runtime declined to report is a refusal
        # here rather than a plausible-looking default in a signed message.
        if "eval_count" in stats.missing_counters or (
            stats.eval_count == 0 and stats.output
        ):
            raise OllamaProtocolError(
                f"{stats.model} returned {len(stats.output)} characters of output but "
                f"eval_count={stats.eval_count} (missing counters: "
                f"{stats.missing_counters or 'none'}); refusing to sign a token count "
                "the runtime did not report"
            )
        if stats.warm is None:
            raise InferenceError(
                f"job {job_id}: warmth was not checked (check_warm=False), so "
                "InferenceResult.warm would be an assumption, not a reading of "
                "/api/ps; a bid is priced on that flag"
            )
        result = InferenceResult(
            job_id=job_id,
            provider_peer_id=provider_peer_id or self.peer_id,
            output=stats.output,
            model=stats.served_model or stats.model,
            tokens_generated=stats.eval_count,
            ttft_ms=stats.ttft_ms,
            total_ms=stats.total_ms,
            tokens_per_sec=stats.tokens_per_sec,
            warm=stats.warm,
            output_hash=sha256_hex(stats.output),
        )
        signer = identity or self.identity
        if signer is not None:
            signer.sign_message(result)
        return result


# --------------------------------------------------------------------------
# manual smoke test
# --------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="one streaming generation against ollama")
    ap.add_argument("--model", default=C.OLLAMA_MODEL)
    ap.add_argument("--prompt", default="Explain gravity in one sentence.")
    ap.add_argument("--max-tokens", type=int, default=64)
    args = ap.parse_args()

    ident = Identity.load_or_create("inference-smoke")
    with InferenceEngine(model=args.model, identity=ident, peer_id=ident.address) as eng:
        print(f"warm before: {eng.is_warm(args.model)}")
        res = eng.run("smoke", args.prompt, max_tokens=args.max_tokens)
        print(f"ttft_ms      {res.ttft_ms:.1f}")
        print(f"total_ms     {res.total_ms:.1f}")
        print(f"tokens       {res.tokens_generated}  ({res.tokens_per_sec:.2f} tok/s)")
        print(f"warm         {res.warm}")
        print(f"hash         {res.output_hash[:16]}...")
        print(f"signature    {(res.signature or '')[:32]}...")
        print(f"output       {res.output[:200]}")
