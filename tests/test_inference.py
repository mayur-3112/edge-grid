"""Tests for the edge inference engine and hardware benchmark.

The HTTP layer is mocked with `httpx.MockTransport` so that timing assertions are
deterministic: the fake transport emits NDJSON chunks with known sleeps between
them, which lets us assert that TTFT is measured at the first chunk carrying
*text* rather than at the first byte of the response or at the end of the stream.
Only one test talks to a real Ollama, and it is marked `live`.
"""

from __future__ import annotations

import hashlib
import json
import time
from typing import Iterator, Optional

import httpx
import pytest

from edgegrid.identity import Identity, verify_message
from edgegrid.schemas import HardwareTier
from inference import benchmark as B
from inference.engine import (
    EmptyOutputError,
    InferenceEngine,
    InferenceTimeoutError,
    ModelNotFoundError,
    OllamaProtocolError,
    OllamaUnavailableError,
)

CHUNK_DELAY_S = 0.05


# --------------------------------------------------------------------------
# fake ollama
# --------------------------------------------------------------------------

def ndjson_stream(chunks: list[dict], delay_s: float = CHUNK_DELAY_S) -> Iterator[bytes]:
    """Yield NDJSON lines with a pause before each, imitating token-by-token output."""
    for chunk in chunks:
        time.sleep(delay_s)
        yield (json.dumps(chunk) + "\n").encode()


def final_chunk(eval_count: int = 4, eval_duration_ns: int = 500_000_000, **kw) -> dict:
    return {
        "model": "fake:1b", "response": "", "done": True, "done_reason": "stop",
        "total_duration": 900_000_000, "load_duration": 100_000_000,
        "prompt_eval_count": 7, "prompt_eval_duration": 80_000_000,
        "eval_count": eval_count, "eval_duration": eval_duration_ns,
    } | kw


def token_chunk(text: str) -> dict:
    return {"model": "fake:1b", "response": text, "done": False}


def make_engine(
    handler,
    identity: Optional[Identity] = None,
    model: str = "fake:1b",
    **kw,
) -> InferenceEngine:
    return InferenceEngine(
        model=model, host="http://ollama.invalid:11434", identity=identity,
        peer_id="peer-under-test", transport=httpx.MockTransport(handler), **kw,
    )


def generate_handler(chunks: list[dict], loaded: Optional[list[str]] = None,
                     delay_s: float = CHUNK_DELAY_S):
    """A handler that streams `chunks` for /api/generate and reports `loaded` for /api/ps."""
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/ps":
            names = [{"name": n} for n in (loaded or [])]
            return httpx.Response(200, json={"models": names})
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "fake:1b"}]})
        return httpx.Response(200, content=ndjson_stream(chunks, delay_s))
    return handler


# --------------------------------------------------------------------------
# TTFT
# --------------------------------------------------------------------------

def test_ttft_is_stamped_at_the_first_chunk_carrying_text():
    """A leading empty-response chunk must not be mistaken for the first token."""
    chunks = [token_chunk(""), token_chunk("Grav"), token_chunk("ity"), final_chunk()]
    with make_engine(generate_handler(chunks)) as eng:
        stats = eng.collect("why do things fall?", max_tokens=8)

    # Two 50 ms pauses precede the first *text* chunk; four precede the end.
    assert stats.ttft_ms >= 2 * CHUNK_DELAY_S * 1000 * 0.9
    assert stats.ttft_ms < stats.total_ms
    assert stats.total_ms >= 4 * CHUNK_DELAY_S * 1000 * 0.9
    assert stats.output == "Gravity"
    assert stats.n_chunks == 4


def test_stream_tokens_yields_text_as_it_arrives():
    chunks = [token_chunk("a"), token_chunk("b"), token_chunk("c"), final_chunk()]
    with make_engine(generate_handler(chunks)) as eng:
        seen, arrival = [], []
        t0 = time.perf_counter()
        for tok in eng.stream_tokens("go", max_tokens=8):
            seen.append(tok)
            arrival.append(time.perf_counter() - t0)

    assert seen == ["a", "b", "c"]
    # Tokens arrive spread out, not all at once when the response closes.
    assert arrival[-1] - arrival[0] >= 2 * CHUNK_DELAY_S * 0.9


def test_stats_returned_through_stop_iteration():
    chunks = [token_chunk("x"), final_chunk(eval_count=11)]
    with make_engine(generate_handler(chunks)) as eng:
        gen = eng.stream_tokens("go")
        with pytest.raises(StopIteration) as stop:
            while True:
                next(gen)
    assert stop.value.value.eval_count == 11


# --------------------------------------------------------------------------
# real counts, not word counts
# --------------------------------------------------------------------------

def test_token_count_comes_from_the_runtime_not_from_splitting_words():
    """The old engine reported len(output.split()); these must differ here."""
    chunks = [token_chunk("one two three four five"), final_chunk(eval_count=9)]
    with make_engine(generate_handler(chunks)) as eng:
        result = eng.run("job-1", "go", max_tokens=32)

    assert result.tokens_generated == 9
    assert result.tokens_generated != len(result.output.split())


def test_tokens_per_sec_uses_eval_duration_not_wall_clock():
    """Throughput must exclude model load and prompt evaluation."""
    chunks = [token_chunk("hi"), final_chunk(eval_count=50, eval_duration_ns=2_000_000_000)]
    with make_engine(generate_handler(chunks)) as eng:
        stats = eng.collect("go")

    assert stats.tokens_per_sec == pytest.approx(25.0)
    assert stats.prompt_eval_count == 7
    assert stats.load_ms == pytest.approx(100.0)


def test_tokens_per_sec_is_zero_when_the_runtime_reports_no_eval_duration():
    chunks = [token_chunk("hi"), final_chunk(eval_count=3, eval_duration_ns=0)]
    with make_engine(generate_handler(chunks)) as eng:
        assert eng.collect("go").tokens_per_sec == 0.0


# --------------------------------------------------------------------------
# the InferenceResult contract
# --------------------------------------------------------------------------

def test_run_returns_a_signed_result_that_verifies():
    ident = Identity.generate()
    chunks = [token_chunk("Gravity bends spacetime."), final_chunk(eval_count=5)]
    with make_engine(generate_handler(chunks), identity=ident) as eng:
        result = eng.run("job-42", "why?", max_tokens=16)

    assert result.job_id == "job-42"
    assert result.provider_peer_id == "peer-under-test"
    assert result.output_hash == hashlib.sha256(result.output.encode()).hexdigest()
    assert verify_message(result, ident.address) is True

    tampered = result.model_copy(update={"output": "Gravity is a hoax."})
    assert verify_message(tampered, ident.address) is False


def test_run_round_trips_through_the_wire_schema():
    chunks = [token_chunk("ok"), final_chunk()]
    with make_engine(generate_handler(chunks), identity=Identity.generate()) as eng:
        result = eng.run("job-7", "go")
    assert type(result).from_bytes(result.to_bytes()) == result


def test_warm_flag_comes_from_api_ps():
    chunks = [token_chunk("ok"), final_chunk()]
    with make_engine(generate_handler(chunks, loaded=["fake:1b"])) as eng:
        assert eng.is_warm() is True
        assert eng.collect("go").warm is True

    with make_engine(generate_handler(chunks, loaded=["other:7b"])) as eng:
        assert eng.is_warm() is False
        assert eng.collect("go").warm is False


# --------------------------------------------------------------------------
# failure modes: each one distinct, none of them a fake result
# --------------------------------------------------------------------------

def test_connection_refused_raises_ollama_unavailable():
    def handler(request):
        raise httpx.ConnectError("Connection refused", request=request)

    with make_engine(handler) as eng:
        with pytest.raises(OllamaUnavailableError):
            eng.run("job", "go")
        with pytest.raises(OllamaUnavailableError):
            eng.is_warm()


def test_unknown_model_raises_model_not_found():
    def handler(request):
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(404, json={"error": "model 'ghost:9b' not found"})

    with make_engine(handler, model="ghost:9b") as eng:
        with pytest.raises(ModelNotFoundError, match="ghost:9b"):
            eng.run("job", "go")


def test_timeout_raises_inference_timeout():
    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    with make_engine(handler) as eng:
        with pytest.raises(InferenceTimeoutError):
            eng.run("job", "go")


def test_error_chunk_inside_a_200_stream_raises():
    """Ollama can report failure mid-stream with HTTP 200; that is not a result."""
    chunks = [token_chunk("partial"), {"error": "model runner has crashed"}]
    with make_engine(generate_handler(chunks)) as eng:
        with pytest.raises(Exception) as exc:
            eng.run("job", "go")
    assert "crashed" in str(exc.value)


def test_truncated_stream_raises_protocol_error():
    """No final done chunk means no token counts; refuse rather than guess."""
    chunks = [token_chunk("half a "), token_chunk("sentence")]
    with make_engine(generate_handler(chunks)) as eng:
        with pytest.raises(OllamaProtocolError, match="done chunk"):
            eng.run("job", "go")


def test_unparseable_chunk_raises_protocol_error():
    def handler(request):
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(200, content=iter([b"{not json}\n"]))

    with make_engine(handler) as eng:
        with pytest.raises(OllamaProtocolError):
            eng.run("job", "go")


def test_generation_with_no_tokens_raises_rather_than_reporting_zero_ttft():
    with make_engine(generate_handler([final_chunk(eval_count=0)])) as eng:
        stats = eng.collect("go", max_tokens=0)
        assert stats.ttft_ms is None          # the stream is reported faithfully
        with pytest.raises(EmptyOutputError):  # but a job result is refused
            eng.run("job", "go", max_tokens=0)


def test_unload_raises_if_the_model_is_still_resident():
    """A cold measurement that was secretly warm is worse than no measurement."""
    def handler(request):
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": "fake:1b"}]})
        return httpx.Response(200, json={"done": True})

    with make_engine(handler) as eng:
        with pytest.raises(Exception, match="still resident"):
            eng.unload(wait_s=0.5)


# --------------------------------------------------------------------------
# benchmark helpers
# --------------------------------------------------------------------------

def test_percentile_matches_linear_interpolation():
    assert B.percentile([1, 2, 3, 4], 0.5) == pytest.approx(2.5)
    assert B.percentile([10], 0.95) == 10
    assert B.percentile([1, 2, 3, 4, 5], 0.95) == pytest.approx(4.8)
    with pytest.raises(ValueError):
        B.percentile([], 0.5)


def test_summarize_reports_n_even_for_an_empty_sample():
    assert B.summarize("ttft_ms", []) == {"ttft_ms_n": 0}
    s = B.summarize("ttft_ms", [100.0, 200.0, 300.0])
    assert s["ttft_ms_n"] == 3 and s["ttft_ms_mean"] == 200.0 and s["ttft_ms_max"] == 300.0


@pytest.mark.parametrize("accel,expected", [
    ({"kind": "none", "vram_gb": 0.0}, HardwareTier.CPU),
    ({"kind": "nvidia", "vram_gb": 8.0}, HardwareTier.LOW_GPU),
    ({"kind": "nvidia", "vram_gb": 24.0}, HardwareTier.DISCRETE_GPU),
    ({"kind": "apple", "vram_gb": 32.0}, HardwareTier.DISCRETE_GPU),
    ({"kind": "apple", "vram_gb": 8.0}, HardwareTier.LOW_GPU),
])
def test_classify_tier_maps_capacity_onto_tiers(accel, expected):
    assert B.classify_tier(accel) is expected


def test_detect_accelerator_parses_nvidia_smi(monkeypatch):
    monkeypatch.setattr(B, "_run", lambda cmd, timeout=5.0: (
        "NVIDIA A10G, 23028\nNVIDIA A10G, 23028" if cmd[0] == "nvidia-smi" else None))
    accel = B.detect_accelerator()
    assert accel["kind"] == "nvidia" and accel["n_devices"] == 2
    assert accel["vram_gb"] == pytest.approx(44.98, abs=0.1)
    assert B.classify_tier(accel) is HardwareTier.DISCRETE_GPU


def test_detect_accelerator_finds_nothing_on_this_cpu_only_machine():
    accel = B.detect_accelerator()
    if accel["kind"] != "none":
        pytest.skip(f"this machine has an accelerator: {accel}")
    assert B.classify_tier() is HardwareTier.CPU
    assert accel["detected_by"] == "no accelerator probe matched"


def test_hardware_profile_records_a_measurement_failure_instead_of_hiding_it():
    prof = B.hardware_profile(model="fake:1b", host="http://127.0.0.1:1")
    assert prof["tokens_per_sec"] == 0.0
    assert "OllamaUnavailableError" in prof["tokens_per_sec_error"]
    assert prof["cpu_count"] > 0 and prof["ram_gb"] > 0


def test_baseline_skips_loudly_without_an_api_key():
    out = B.baseline(api_key="")
    assert out["skipped"] is True
    assert "no API key" in out["reason"]
    assert "ttft_ms_mean" not in out          # never a fabricated number


# --------------------------------------------------------------------------
# live
# --------------------------------------------------------------------------

@pytest.mark.live
@pytest.mark.slow
def test_live_ollama_round_trip():
    from edgegrid import config as C

    ident = Identity.generate()
    with InferenceEngine(identity=ident, peer_id=ident.address) as eng:
        if C.OLLAMA_MODEL not in eng.available_models():
            pytest.skip(f"{C.OLLAMA_MODEL} not pulled on this machine")
        eng.collect("warm up", max_tokens=8)
        assert eng.is_warm() is True
        result = eng.run("live-job", "Explain gravity in one sentence.", max_tokens=32)

    assert result.output.strip()
    assert result.tokens_generated > 0
    assert 0 < result.ttft_ms < result.total_ms
    assert result.warm is True
    assert verify_message(result, ident.address) is True


# --------------------------------------------------------------------------
# stream-level failures
#
# The two tests above that raise from the transport raise on the FIRST request
# the engine makes, which is the `/api/ps` warmth check - so they never reach
# `stream_tokens`'s own error handling. Deleting that handling entirely left all
# 29 original tests green. These reach it: `/api/ps` answers normally and the
# failure happens on `/api/generate`.
# --------------------------------------------------------------------------

def _ps_ok_then(fail: Exception):
    def handler(request: httpx.Request):
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": []})
        raise fail.__class__(str(fail), request=request)
    return handler


def test_timeout_during_the_generate_stream_raises_inference_timeout():
    with make_engine(_ps_ok_then(httpx.ReadTimeout("slow"))) as eng:
        with pytest.raises(InferenceTimeoutError, match="budget"):
            eng.run("job", "go")


def test_transport_failure_during_the_generate_stream_raises_unavailable():
    with make_engine(_ps_ok_then(httpx.ConnectError("refused"))) as eng:
        with pytest.raises(OllamaUnavailableError, match="cannot reach ollama"):
            eng.run("job", "go")


# --------------------------------------------------------------------------
# counters the runtime did not send are named, not defaulted to zero
# --------------------------------------------------------------------------

def test_a_final_chunk_without_counters_is_recorded_as_missing_not_as_zero():
    bare = {"model": "fake:1b", "response": "", "done": True, "done_reason": "stop"}
    with make_engine(generate_handler([token_chunk("Gravity pulls."), bare])) as eng:
        stats = eng.collect("go")

    assert stats.eval_count == 0
    assert stats.counters_complete is False
    assert "eval_count" in stats.missing_counters
    assert "eval_duration" in stats.missing_counters
    assert "eval_count" in stats.as_row()["missing_counters"]


def test_run_refuses_to_sign_a_token_count_the_runtime_never_reported():
    """0 tokens next to 14 characters of output is a missing counter, not a result."""
    bare = {"model": "fake:1b", "response": "", "done": True, "done_reason": "stop"}
    with make_engine(generate_handler([token_chunk("Gravity pulls."), bare])) as eng:
        with pytest.raises(OllamaProtocolError, match="refusing to sign"):
            eng.run("job", "go")


def test_a_complete_final_chunk_reports_no_missing_counters():
    with make_engine(generate_handler([token_chunk("hi"), final_chunk()])) as eng:
        stats = eng.collect("go")
    assert stats.missing_counters == [] and stats.counters_complete is True


# --------------------------------------------------------------------------
# provenance: what actually served this
# --------------------------------------------------------------------------

def test_stats_and_rows_carry_the_backend_host_and_served_model():
    with make_engine(generate_handler([token_chunk("hi"), final_chunk()])) as eng:
        stats = eng.collect("go")
    assert stats.backend == "ollama"
    assert stats.host == "http://ollama.invalid:11434"
    assert stats.served_model == "fake:1b"
    row = stats.as_row()
    assert row["backend"] == "ollama" and row["served_model"] == "fake:1b"
    assert row["host"] == "http://ollama.invalid:11434"


def test_a_runtime_serving_a_different_model_raises_rather_than_mislabelling():
    """Every number here is attributed to a model; a substitution invalidates all of them."""
    chunks = [{"model": "other:70b", "response": "hi", "done": False},
              final_chunk() | {"model": "other:70b"}]
    with make_engine(generate_handler(chunks)) as eng:
        with pytest.raises(OllamaProtocolError, match="but the runtime served"):
            eng.collect("go")


def test_a_model_switch_mid_stream_raises():
    chunks = [token_chunk("hi"), {"model": "other:70b", "response": "there", "done": False},
              final_chunk()]
    with make_engine(generate_handler(chunks)) as eng:
        with pytest.raises(OllamaProtocolError):
            eng.collect("go")


def test_run_refuses_to_stamp_a_warm_flag_it_never_read():
    """check_warm=False leaves warmth unknown; a bid is priced on that flag."""
    with make_engine(generate_handler([token_chunk("hi"), final_chunk()])) as eng:
        stats = eng.collect("go", check_warm=False)
        assert stats.warm is None                      # unknown, not False
        with pytest.raises(Exception, match="warmth was not checked"):
            eng.run("job", "go", check_warm=False)


# --------------------------------------------------------------------------
# an unreadable /api/ps is not "nothing is loaded"
# --------------------------------------------------------------------------

def _ps_payload(payload):
    def handler(request: httpx.Request):
        if request.url.path == "/api/ps":
            return httpx.Response(200, json=payload)
        return httpx.Response(200, json={"done": True})
    return handler


@pytest.mark.parametrize("payload", [{"error": "unsupported"}, {}, {"models": "nope"},
                                     {"models": [{"id": "fake:1b"}]}])
def test_an_unreadable_api_ps_raises_instead_of_reporting_cold(payload):
    with make_engine(_ps_payload(payload)) as eng:
        with pytest.raises(OllamaProtocolError):
            eng.is_warm()


def test_unload_does_not_certify_an_eviction_it_could_not_observe():
    """`/api/ps` unreadable means unknown, and unknown must not become 'evicted'."""
    with make_engine(_ps_payload({"error": "unsupported"})) as eng:
        with pytest.raises(OllamaProtocolError):
            eng.unload(wait_s=0.3)


def test_unload_raises_when_the_runtime_rejects_the_request():
    """A 404 unload followed by an absent model is not a successful eviction."""
    def handler(request):
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(404, json={"error": "model 'ghost:9b' not found"})

    with make_engine(handler, model="ghost:9b") as eng:
        with pytest.raises(ModelNotFoundError):
            eng.unload(wait_s=0.3)


def test_unload_returns_once_the_model_is_gone():
    def handler(request):
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": "other:7b"}]})
        return httpx.Response(200, json={"done": True})

    with make_engine(handler) as eng:
        eng.unload(wait_s=0.5)          # no exception: the model really is absent


# --------------------------------------------------------------------------
# benchmark(), cold_vs_warm() and _trial()
#
# These produce every number in the README and had no coverage at all: pinning a
# fabricated `tokens_per_sec` into each row left the whole suite green. The fake
# below is stateful - it loads and evicts models the way Ollama does - so the
# cold/warm logic is exercised rather than stubbed.
# --------------------------------------------------------------------------

class FakeOllama:
    """A minimal stateful Ollama: models load on first use and evict on keep_alive 0."""

    def __init__(self, model: str = "fake:1b", cold_delay_s: float = 0.12,
                 warm_delay_s: float = 0.01, eval_count: int = 20,
                 evictable: bool = True, evict_before_ps_call: Optional[int] = None):
        self.model = model
        self.cold_delay_s = cold_delay_s
        self.warm_delay_s = warm_delay_s
        self.eval_count = eval_count
        self.evictable = evictable
        self.evict_before_ps_call = evict_before_ps_call
        self.loaded: set[str] = set()
        self.generate_calls = 0
        self.ps_calls = 0

    def _stream(self, cold: bool) -> Iterator[bytes]:
        delay = self.cold_delay_s if cold else self.warm_delay_s
        time.sleep(delay)
        yield (json.dumps({"model": self.model, "response": "Gravity ", "done": False}) + "\n").encode()
        time.sleep(self.warm_delay_s)
        yield (json.dumps({"model": self.model, "response": "pulls.", "done": False}) + "\n").encode()
        yield (json.dumps({
            "model": self.model, "response": "", "done": True, "done_reason": "stop",
            "total_duration": int(delay * 1e9), "load_duration": int(delay * 1e9) if cold else 5_000_000,
            "prompt_eval_count": 9, "prompt_eval_duration": 10_000_000,
            "eval_count": self.eval_count, "eval_duration": 1_000_000_000,
        }) + "\n").encode()

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/api/ps":
            self.ps_calls += 1
            # Ollama can evict under memory pressure between two trials; this
            # reproduces that so the benchmark's phase label is really tested.
            if self.ps_calls == self.evict_before_ps_call:
                self.loaded.clear()
            return httpx.Response(200, json={"models": [{"name": n} for n in sorted(self.loaded)]})
        if path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": self.model}, {"name": "other:7b"}]})
        body = json.loads(request.content)
        if body.get("keep_alive") == 0:
            if self.evictable:
                self.loaded.discard(body["model"])
            return httpx.Response(200, json={"model": body["model"], "done": True})
        cold = body["model"] not in self.loaded
        self.generate_calls += 1
        self.loaded.add(body["model"])
        return httpx.Response(200, content=self._stream(cold))

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def test_benchmark_measures_warm_trials_and_records_its_provenance():
    fake = FakeOllama()
    out = B.benchmark(model="fake:1b", n_trials=3, max_tokens=16,
                      host="http://ollama.invalid:11434", transport=fake.transport)

    assert out["n_trials"] == 3 and out["n_trials_attempted"] == 3
    assert out["n_excluded_not_warm"] == 0 and out["all_warm"] is True
    assert out["backend"] == "ollama" and out["served_models"] == ["fake:1b"]
    assert out["n_rows_missing_counters"] == 0
    assert out["ttft_ms_n"] == 3
    # The measurement is the fake's warm delay, not a constant this test supplied.
    assert 5.0 < out["ttft_ms_mean"] < 200.0
    assert out["tokens_per_sec_mean"] == pytest.approx(20.0)   # 20 tokens / 1.0 s eval


def test_benchmark_rows_are_labelled_by_measured_warmth_not_by_intent(tmp_path):
    """A trial that came back cold must not be averaged into a 'warm path' figure."""
    from edgegrid.runlog import RunLog

    # /api/ps calls run 1=warmup, 2=trial 0, 3=trial 1, 4=trial 2; evict before #3.
    fake = FakeOllama(evict_before_ps_call=3)
    with RunLog("test-benchmark-phase", results_dir=tmp_path) as log:
        out = B.benchmark(model="fake:1b", n_trials=3, max_tokens=16,
                          host="http://ollama.invalid:11434", transport=fake.transport, log=log)
        run_dir = log.dir

    assert out["n_trials_attempted"] == 3
    assert out["n_excluded_not_warm"] == 1
    assert out["n_trials"] == 2
    assert out["all_warm"] is False
    assert out["ttft_ms_n"] == 2               # the cold trial is not in the warm stats

    rows = (run_dir / "trials.csv").read_text().splitlines()
    assert sum(1 for r in rows[1:] if ",cold," in r) == 1
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert any("reported cold" in d["why"] for d in manifest["dropped"])


def test_benchmark_raises_when_no_trial_was_warm():
    fake = FakeOllama(evictable=True)

    class NeverWarm(FakeOllama):
        def handler(self, request):
            resp = super().handler(request)
            self.loaded.clear()                # evicted immediately after every call
            return resp

    never = NeverWarm()
    with pytest.raises(Exception, match="none of the"):
        B.benchmark(model="fake:1b", n_trials=2, max_tokens=16,
                    host="http://ollama.invalid:11434", transport=never.transport)


def test_cold_vs_warm_actually_evicts_and_the_cold_half_is_slower():
    fake = FakeOllama(cold_delay_s=0.20, warm_delay_s=0.01)
    out = B.cold_vs_warm(model="fake:1b", n_pairs=2, max_tokens=16,
                         host="http://ollama.invalid:11434", transport=fake.transport)

    assert out["cold_ttft_ms_n"] == 2 and out["warm_ttft_ms_n"] == 2
    assert out["cold_ttft_ms_mean"] > out["warm_ttft_ms_mean"]
    assert out["cold_over_warm_ratio"] > 2.0
    assert out["ttft_penalty_ms"] > 0
    assert out["backend"] == "ollama" and out["served_models"] == ["fake:1b"]


def test_cold_vs_warm_refuses_when_the_model_cannot_be_evicted():
    """An unload that does not evict must abort, not yield a 1.0x ratio."""
    fake = FakeOllama(evictable=False)
    fake.loaded.add("fake:1b")
    with pytest.raises(Exception, match="still resident"):
        B.cold_vs_warm(model="fake:1b", n_pairs=1, max_tokens=16,
                       host="http://ollama.invalid:11434", transport=fake.transport)


def test_cold_vs_warm_refuses_when_the_warm_half_was_not_warm():
    class EvictsAfterEveryCall(FakeOllama):
        def handler(self, request):
            resp = super().handler(request)
            if request.url.path == "/api/generate":
                self.loaded.clear()
            return resp

    with pytest.raises(Exception, match="warm half"):
        B.cold_vs_warm(model="fake:1b", n_pairs=1, max_tokens=16,
                       host="http://ollama.invalid:11434",
                       transport=EvictsAfterEveryCall().transport)


def test_hardware_profile_reads_the_model_lists_off_the_runtime():
    """models/warm_models feed a NodeRecord; declaring them locally advertises
    capacity nobody verified."""
    fake = FakeOllama()
    prof = B.hardware_profile(model="fake:1b", host="http://ollama.invalid:11434",
                              transport=fake.transport)

    assert prof["backend"] == "ollama" and prof["host"] == "http://ollama.invalid:11434"
    assert prof["models"] == ["fake:1b", "other:7b"]      # from /api/tags, not from config
    assert prof["warm_models"] == ["fake:1b"]             # from /api/ps
    assert prof["tokens_per_sec"] == pytest.approx(20.0)
    assert prof["tokens_per_sec_error"] == "" and prof["models_error"] == ""
    assert prof["served_model"] == "fake:1b"


def test_hardware_profile_keeps_both_list_keys_when_it_cannot_measure():
    prof = B.hardware_profile(model="fake:1b", measure_tps=False)
    assert prof["models"] == [] and prof["warm_models"] == []
    assert prof["models_error"] and prof["tokens_per_sec_error"]


# --------------------------------------------------------------------------
# hosted baseline
#
# No GROQ_API_KEY exists in this environment, so the SSE parser can only be
# exercised against a recorded shape. The shape that matters is the terminal
# usage frame, which real providers send with an EMPTY `choices` list - the
# original parser indexed `choices[0]` unconditionally and died with an
# IndexError after the timings had already been taken.
# --------------------------------------------------------------------------

GROQ_STYLE_SSE = b"".join([
    b'data: {"model":"llama-3.3-70b-versatile","choices":[{"index":0,'
    b'"delta":{"role":"assistant","content":""}}]}\n\n',
    b'data: {"model":"llama-3.3-70b-versatile","choices":[{"index":0,'
    b'"delta":{"content":"Gravity"}}]}\n\n',
    b'data: {"model":"llama-3.3-70b-versatile","choices":[{"index":0,'
    b'"delta":{"content":" is a force."},"finish_reason":null}]}\n\n',
    b'data: {"model":"llama-3.3-70b-versatile","choices":[{"index":0,'
    b'"delta":{},"finish_reason":"stop"}]}\n\n',
    # the terminal usage frame: choices is empty
    b'data: {"model":"llama-3.3-70b-versatile","choices":[],'
    b'"x_groq":{"usage":{"completion_tokens":9}}}\n\n',
    b'data: [DONE]\n\n',
])


def _sse_transport(body: bytes, status: int = 200) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(
        status, content=body, headers={"content-type": "text/event-stream"}))


def test_baseline_parses_a_providers_terminal_usage_frame():
    out = B.baseline(n_trials=2, api_key="test-key", transport=_sse_transport(GROQ_STYLE_SSE))

    assert out["skipped"] is False
    assert out["backend"] == "openai-compatible"
    assert out["served_models"] == ["llama-3.3-70b-versatile"]
    assert out["ttft_ms_n"] == 2 and out["ttft_ms_mean"] > 0


def test_baseline_raises_when_the_endpoint_streams_no_content():
    empty = b'data: {"model":"m","choices":[]}\n\ndata: [DONE]\n\n'
    with pytest.raises(Exception, match="no TTFT to report"):
        B.baseline(n_trials=1, api_key="test-key", transport=_sse_transport(empty))


def test_baseline_raises_on_an_error_frame_rather_than_reporting_a_latency():
    err = b'data: {"error":{"message":"rate limited"}}\n\n'
    with pytest.raises(Exception, match="rate limited"):
        B.baseline(n_trials=1, api_key="test-key", transport=_sse_transport(err))


def test_baseline_raises_on_an_http_error():
    with pytest.raises(Exception, match="HTTP 401"):
        B.baseline(n_trials=1, api_key="bad",
                   transport=_sse_transport(b'{"error":"invalid key"}', status=401))
