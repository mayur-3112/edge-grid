"""Hardware benchmark and tier classifier for an edge node (Objective 7).

Two things live here, and they are related.

The first is the measurement that Objective 7 is graded on: TTFT, throughput and
total latency for a real model on this machine, with the **cold/warm split made
explicit**. That split is not a curiosity - it is the empirical justification for
the market protocol's warm-start bonus. If a warm node answers in well under a
second and a cold one takes ten, then paying a premium for warmth is not a
heuristic, it is arithmetic, and `C.WARM_START_BONUS` has a number behind it.
Cold is measured by actually evicting the model (`keep_alive: 0`) and verifying
through `/api/ps` that it is gone, not by hoping the first trial happens to be
cold.

The second is `classify_tier()`, the Tier 1/2/3 classifier Module 1's
`NodeRecord.tier` field promised and nobody wrote. It uses `nvidia-smi`,
`rocm-smi` and `platform` only - deliberately no torch, because a discovery node
that has to install CUDA userspace to announce "I am a CPU node" is not an edge
node.

Both write through `RunLog`, so every number in the report traces back to a run
directory with the config and git SHA that produced it.

Usage:
    python -m inference.benchmark --trials 8
    python -m inference.benchmark --baseline          # + hosted API comparison
    python -m inference.benchmark --profile-only      # just the hardware profile
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import shutil
import statistics
import subprocess
import time
from typing import Any, Optional

import httpx
import psutil

from edgegrid import config as C
from edgegrid.runlog import RunLog
from edgegrid.schemas import HardwareTier
from inference.engine import (
    GenerationStats,
    InferenceEngine,
    InferenceError,
)

DEFAULT_PROMPT = "Explain what gravity is in two sentences."

# A hosted OpenAI-compatible endpoint used only as a latency reference point.
# Groq is the one this project already has a client for; any /v1 base URL works.
BASELINE_BASE_URL = "https://api.groq.com/openai/v1"
BASELINE_MODEL = C.GROQ_JUDGE_MODEL


# --------------------------------------------------------------------------
# small stats helpers - no numpy dependency for a node-side script
# --------------------------------------------------------------------------

def percentile(xs: list[float], q: float) -> float:
    """Linear-interpolation percentile, matching numpy's default method.

    At n=5 a p95 is barely more than "the max"; that is a property of the sample
    size, not of the estimator, and the trial count is recorded next to it so a
    reader can see that.
    """
    if not xs:
        raise ValueError("percentile of an empty sample")
    s = sorted(xs)
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * q
    lo, hi = int(pos), min(int(pos) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def summarize(name: str, xs: list[float]) -> dict[str, Any]:
    if not xs:
        return {f"{name}_n": 0}
    return {
        f"{name}_n": len(xs),
        f"{name}_mean": round(statistics.fmean(xs), 2),
        f"{name}_median": round(statistics.median(xs), 2),
        f"{name}_p95": round(percentile(xs, 0.95), 2),
        f"{name}_min": round(min(xs), 2),
        f"{name}_max": round(max(xs), 2),
        f"{name}_stdev": round(statistics.stdev(xs), 2) if len(xs) > 1 else 0.0,
    }


# --------------------------------------------------------------------------
# hardware detection
# --------------------------------------------------------------------------

def _run(cmd: list[str], timeout: float = 5.0) -> Optional[str]:
    """Run a probe command, returning None if it is absent or fails."""
    if shutil.which(cmd[0]) is None:
        return None
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.SubprocessError, OSError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def detect_accelerator() -> dict[str, Any]:
    """What compute this node actually has, and how we found out.

    `detected_by` is returned so a tier in a NodeRecord can be audited rather
    than taken on faith.
    """
    nv = _run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"])
    if nv:
        names, vram = [], 0.0
        for line in nv.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                names.append(parts[0])
                try:
                    vram += float(parts[1]) / 1024.0  # MiB -> GiB
                except ValueError:
                    pass
        if names:
            return {"kind": "nvidia", "name": "; ".join(names), "n_devices": len(names),
                    "vram_gb": round(vram, 2), "detected_by": "nvidia-smi"}

    rocm = _run(["rocm-smi", "--showmeminfo", "vram", "--csv"])
    if rocm and "vram" in rocm.lower():
        total_bytes = sum(float(m) for m in re.findall(r"(\d{9,})", rocm)) or 0.0
        return {"kind": "amd", "name": "AMD ROCm device", "n_devices": 1,
                "vram_gb": round(total_bytes / 1024 ** 3, 2), "detected_by": "rocm-smi"}

    if platform.system() == "Darwin" and platform.machine() in ("arm64", "aarch64"):
        # Apple Silicon has no separate VRAM: the GPU addresses system RAM, so
        # unified memory is the right capacity number for "can this hold a model".
        unified = round(psutil.virtual_memory().total / 1024 ** 3, 2)
        return {"kind": "apple", "name": f"Apple Silicon ({platform.machine()})",
                "n_devices": 1, "vram_gb": unified, "detected_by": "platform.machine"}

    return {"kind": "none", "name": platform.processor() or platform.machine(),
            "n_devices": 0, "vram_gb": 0.0, "detected_by": "no accelerator probe matched"}


def classify_tier(accel: Optional[dict] = None) -> HardwareTier:
    """Map detected hardware onto the Module 1 tiers.

    Tier 3 is defined by capacity, not by bus: >= 16 GB of GPU-addressable memory
    is what lets a node hold a large model resident, which is the thing the
    market actually pays for. Apple Silicon's unified memory therefore counts.
    """
    a = accel or detect_accelerator()
    if a["kind"] == "none":
        return HardwareTier.CPU
    if a["vram_gb"] >= 16.0:
        return HardwareTier.DISCRETE_GPU
    return HardwareTier.LOW_GPU


def hardware_profile(
    model: Optional[str] = None,
    measure_tps: bool = True,
    host: str = C.OLLAMA_HOST,
    transport: Optional[httpx.BaseTransport] = None,
) -> dict[str, Any]:
    """The fields a `NodeRecord` needs, plus the evidence behind them.

    `tokens_per_sec` is measured, not guessed: a bid quoting a made-up throughput
    is a bid the node cannot honour. When the measurement cannot be taken the key
    is 0.0 and `tokens_per_sec_error` says why - it is never silently omitted.
    """
    want = model or C.OLLAMA_MODEL
    accel = detect_accelerator()
    vm = psutil.virtual_memory()
    prof: dict[str, Any] = {
        "backend": "ollama",
        "host": host,
        "benchmark_model": want,
        "tier": int(classify_tier(accel)),
        "tier_name": classify_tier(accel).name,
        "cpu_count": psutil.cpu_count(logical=True) or 0,
        "cpu_count_physical": psutil.cpu_count(logical=False) or 0,
        "ram_gb": round(vm.total / 1024 ** 3, 2),
        "ram_available_gb": round(vm.available / 1024 ** 3, 2),
        "vram_gb": accel["vram_gb"],
        "accelerator": accel["kind"],
        "accelerator_name": accel["name"],
        "detected_by": accel["detected_by"],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "tokens_per_sec": 0.0,
        "tokens_per_sec_error": "",
        # Read back from the runtime below, never asserted. These are the two
        # NodeRecord fields a peer prices a bid on, so declaring them from the
        # local config would be advertising capacity nobody verified.
        "models": [],
        "warm_models": [],
        "models_error": "",
    }
    if not measure_tps:
        reason = "not measured (measure_tps=False)"
        prof["tokens_per_sec_error"] = reason
        prof["models_error"] = reason
        return prof

    try:
        with InferenceEngine(model=want, host=host, transport=transport) as eng:
            prof["models"] = eng.available_models()
            eng.collect(DEFAULT_PROMPT, max_tokens=32)          # warm the model
            stats = eng.collect(DEFAULT_PROMPT, max_tokens=32)  # then measure
            prof["warm_models"] = eng.loaded_models()
        prof["tokens_per_sec"] = round(stats.tokens_per_sec, 2)
        prof["served_model"] = stats.served_model
        if stats.missing_counters:
            prof["tokens_per_sec_error"] = (
                "runtime did not report " + ", ".join(stats.missing_counters)
            )
    except (InferenceError, httpx.HTTPError) as e:
        prof["tokens_per_sec_error"] = f"{type(e).__name__}: {e}"
        if not prof["models"]:
            prof["models_error"] = f"{type(e).__name__}: {e}"
    return prof


# --------------------------------------------------------------------------
# trials
# --------------------------------------------------------------------------

def _trial(engine: InferenceEngine, prompt: str, max_tokens: int) -> tuple[GenerationStats, dict]:
    """One generation, with the CPU/RAM cost of that generation attached.

    `cpu_percent(None)` is reset immediately before the call so the value read
    afterwards is the average over exactly this trial's window, not since boot.
    """
    psutil.cpu_percent(interval=None)
    before_avail = psutil.virtual_memory().available
    stats = engine.collect(prompt, max_tokens=max_tokens)
    resources = {
        "cpu_percent": round(psutil.cpu_percent(interval=None), 2),
        "ram_used_delta_mb": round((before_avail - psutil.virtual_memory().available) / 1024 ** 2, 2),
        "ram_available_gb": round(psutil.virtual_memory().available / 1024 ** 3, 2),
        "load1": round(psutil.getloadavg()[0], 2),
    }
    return stats, resources


def benchmark(
    model: Optional[str] = None,
    n_trials: int = 8,
    max_tokens: int = 64,
    prompt: str = DEFAULT_PROMPT,
    host: str = C.OLLAMA_HOST,
    log: Optional[RunLog] = None,
    transport: Optional[httpx.BaseTransport] = None,
) -> dict[str, Any]:
    """Warm-path benchmark: one discarded warmup trial, then `n_trials` measured.

    The warmup is discarded rather than averaged in because it conflates model
    load time with generation time; the cold cost is measured separately and
    properly by `cold_vs_warm`.

    Every trial's `phase` is the warmth `/api/ps` actually reported for it, not
    the label this function would like it to have. Ollama can evict a model
    mid-run under memory pressure; a trial that came back cold is written to the
    CSV as cold, dropped from the warm summary with a reason, and counted in
    `n_excluded_not_warm`, because averaging an 8-second load into a
    sub-second-TTFT claim is how the headline number stops being true.
    """
    want = model or C.OLLAMA_MODEL
    rows: list[dict] = []
    with InferenceEngine(model=want, host=host, transport=transport) as eng:
        warm_stats, _ = _trial(eng, prompt, max_tokens)
        if log:
            log.note(f"warmup discarded: ttft={warm_stats.ttft_ms}ms warm={warm_stats.warm}")
            log.drop("trials", "warmup trial discarded by design (conflates load with generation)")

        for i in range(n_trials):
            stats, resources = _trial(eng, prompt, max_tokens)
            phase = "warm" if stats.warm else "cold"
            row = {"trial": i, "phase": phase} | stats.as_row() | resources
            rows.append(row)
            if log:
                log.append("trials", row)
                if phase != "warm":
                    log.drop("trials", f"trial {i} reported cold by /api/ps; "
                                       "excluded from the warm-path summary")

    warm_rows = [r for r in rows if r["phase"] == "warm"]
    excluded = len(rows) - len(warm_rows)
    if not warm_rows:
        raise InferenceError(
            f"none of the {len(rows)} trials was warm according to /api/ps; "
            "there is no warm-path measurement to report"
        )
    ttfts = [r["ttft_ms"] for r in warm_rows if r["ttft_ms"] is not None]
    if not ttfts:
        raise InferenceError(
            f"no trial produced a token, so there is no TTFT to summarise "
            f"(done_reasons: {sorted({r['done_reason'] for r in warm_rows})})"
        )
    summary = {
        "backend": "ollama",
        "host": host,
        "model": want,
        "served_models": sorted({r["served_model"] for r in warm_rows}),
        "n_trials": len(warm_rows),
        "n_trials_attempted": len(rows),
        "n_excluded_not_warm": excluded,
        "n_rows_missing_counters": sum(1 for r in warm_rows if r["missing_counters"]),
        "max_tokens": max_tokens,
        "prompt": prompt,
        "warmup_discarded": True,
        **summarize("ttft_ms", ttfts),
        **summarize("total_ms", [r["total_ms"] for r in warm_rows]),
        **summarize("tokens_per_sec", [r["tokens_per_sec"] for r in warm_rows]),
        **summarize("cpu_percent", [r["cpu_percent"] for r in warm_rows]),
        # Ollama's own load_duration on trials it reported as warm. It should be
        # near zero; when it is not, /api/ps listed a model whose runner had
        # already expired, and the "warm" TTFT silently contains a model load.
        # Summarising it here means that shows up next to the headline number
        # instead of only in the per-row CSV.
        **summarize("load_ms", [r["load_ms"] for r in warm_rows]),
        "eval_count_mean": round(statistics.fmean([r["eval_count"] for r in warm_rows]), 2),
        "all_warm": excluded == 0,
    }
    if log:
        log.write_json("warm_summary", summary)
    return summary


def cold_vs_warm(
    model: Optional[str] = None,
    n_pairs: int = 3,
    max_tokens: int = 64,
    prompt: str = DEFAULT_PROMPT,
    host: str = C.OLLAMA_HOST,
    log: Optional[RunLog] = None,
    transport: Optional[httpx.BaseTransport] = None,
    unload_wait_s: float = 10.0,
) -> dict[str, Any]:
    """The headline measurement: TTFT with the model evicted vs. resident.

    Each pair evicts the model, times the first request (cold), then times a
    second request immediately (warm). `engine.unload` raises if the model is
    still resident, so a pair labelled cold really was cold - and the second
    half of the pair is checked the same way, because a ratio is only a ratio if
    both of its terms were what they claim to be.
    """
    want = model or C.OLLAMA_MODEL
    rows: list[dict] = []
    with InferenceEngine(model=want, host=host, transport=transport) as eng:
        for i in range(n_pairs):
            eng.unload(want, wait_s=unload_wait_s)
            cold, cold_res = _trial(eng, prompt, max_tokens)
            if cold.warm:
                raise InferenceError(
                    f"pair {i}: model reported warm immediately after unload; "
                    "refusing to record it as a cold measurement"
                )
            warm, warm_res = _trial(eng, prompt, max_tokens)
            if not warm.warm:
                raise InferenceError(
                    f"pair {i}: model reported cold on the second request of the pair; "
                    "the warm half of a cold/warm ratio was never warm"
                )
            for phase, stats, res in (("cold", cold, cold_res), ("warm", warm, warm_res)):
                row = {"pair": i, "phase": phase} | stats.as_row() | res
                rows.append(row)
                if log:
                    log.append("cold_warm_trials", row)

    cold_ttft = [r["ttft_ms"] for r in rows if r["phase"] == "cold" and r["ttft_ms"] is not None]
    warm_ttft = [r["ttft_ms"] for r in rows if r["phase"] == "warm" and r["ttft_ms"] is not None]
    summary: dict[str, Any] = {
        "backend": "ollama",
        "host": host,
        "model": want,
        "served_models": sorted({r["served_model"] for r in rows}),
        "n_pairs": n_pairs,
        "max_tokens": max_tokens,
        **summarize("cold_ttft_ms", cold_ttft),
        **summarize("warm_ttft_ms", warm_ttft),
        "cold_load_ms_mean": round(
            statistics.fmean([r["load_ms"] for r in rows if r["phase"] == "cold"]), 2),
        "warm_load_ms_mean": round(
            statistics.fmean([r["load_ms"] for r in rows if r["phase"] == "warm"]), 2),
    }
    if not cold_ttft or not warm_ttft:
        # The ratio is the entire deliverable here. Returning a summary without
        # it and letting the caller print a NaN would look like a measurement.
        raise InferenceError(
            f"cold/warm ratio undefined: {len(cold_ttft)} cold and {len(warm_ttft)} warm "
            "trials produced a token; no pair yielded both halves"
        )
    ratio = statistics.fmean(cold_ttft) / statistics.fmean(warm_ttft)
    summary["cold_over_warm_ratio"] = round(ratio, 2)
    summary["ttft_penalty_ms"] = round(
        statistics.fmean(cold_ttft) - statistics.fmean(warm_ttft), 2)
    if log:
        log.write_json("cold_warm_summary", summary)
    return summary


# --------------------------------------------------------------------------
# hosted baseline
# --------------------------------------------------------------------------

def baseline(
    n_trials: int = 5,
    max_tokens: int = 64,
    prompt: str = DEFAULT_PROMPT,
    base_url: str = BASELINE_BASE_URL,
    model: str = BASELINE_MODEL,
    api_key: str = C.GROQ_API_KEY,
    log: Optional[RunLog] = None,
    transport: Optional[httpx.BaseTransport] = None,
) -> dict[str, Any]:
    """TTFT of a hosted OpenAI-compatible endpoint, for comparison.

    With no API key this returns `{"skipped": True, ...}` and records the reason.
    It must never invent a baseline: the edge-vs-cloud latency claim is the point
    of the experiment, and a fabricated cloud number would decide it by fiat.
    """
    if not api_key:
        reason = ("no API key configured (GROQ_API_KEY unset); "
                  "hosted baseline skipped, not estimated")
        if log:
            log.drop("baseline", reason)
            log.write_json("baseline_summary", {"skipped": True, "reason": reason})
        return {"skipped": True, "reason": reason, "base_url": base_url, "model": model}

    rows: list[dict] = []
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"model": model, "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens, "stream": True}

    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0), transport=transport) as client:
        for i in range(n_trials):
            t0 = time.perf_counter()
            ttft_ms: Optional[float] = None
            n_content = 0
            served_model = ""
            with client.stream("POST", url, headers=headers, json=body) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    raise InferenceError(
                        f"baseline endpoint HTTP {resp.status_code}: {resp.text[:300]}")
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        frame = json.loads(payload)
                    except json.JSONDecodeError as e:
                        raise InferenceError(
                            f"baseline endpoint sent an unparseable SSE frame: {payload[:200]}"
                        ) from e
                    if frame.get("error"):
                        raise InferenceError(f"baseline endpoint error frame: {frame['error']}")
                    served_model = str(frame.get("model") or served_model)
                    # A provider's terminal frame carries usage with an EMPTY
                    # `choices` list (Groq does exactly this), so indexing
                    # choices[0] unconditionally makes a real run die with an
                    # IndexError after the numbers were already measured.
                    choices = frame.get("choices") or []
                    if not choices:
                        continue
                    if (choices[0].get("delta") or {}).get("content"):
                        n_content += 1
                        if ttft_ms is None:
                            ttft_ms = (time.perf_counter() - t0) * 1000
            if ttft_ms is None:
                raise InferenceError(
                    f"baseline trial {i}: the endpoint streamed no content chunk, "
                    "so there is no TTFT to report"
                )
            row = {"trial": i, "backend": "openai-compatible", "endpoint": base_url,
                   "model": model, "served_model": served_model,
                   "ttft_ms": round(ttft_ms, 3),
                   "total_ms": round((time.perf_counter() - t0) * 1000, 3),
                   "chunks_with_content": n_content}
            rows.append(row)
            if log:
                log.append("baseline_trials", row)

    summary = {"skipped": False, "backend": "openai-compatible", "base_url": base_url,
               "model": model, "served_models": sorted({r["served_model"] for r in rows}),
               "n_trials": len(rows),
               **summarize("ttft_ms", [r["ttft_ms"] for r in rows]),
               **summarize("total_ms", [r["total_ms"] for r in rows])}
    if log:
        log.write_json("baseline_summary", summary)
    return summary


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="edge node inference benchmark")
    ap.add_argument("--model", default=C.OLLAMA_MODEL)
    ap.add_argument("--host", default=C.OLLAMA_HOST)
    ap.add_argument("--trials", type=int, default=8, help="measured warm trials")
    ap.add_argument("--max-tokens", type=int, default=64)
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    ap.add_argument("--cold-pairs", type=int, default=3, help="cold/warm pairs; 0 to skip")
    ap.add_argument("--unload-wait", type=float, default=10.0,
                    help="seconds to wait for /api/ps to confirm an eviction; raise it on a "
                         "loaded machine where the model runner is slow to exit")
    ap.add_argument("--baseline", action="store_true", help="also time a hosted endpoint")
    ap.add_argument("--baseline-url", default=BASELINE_BASE_URL)
    ap.add_argument("--baseline-model", default=BASELINE_MODEL)
    ap.add_argument("--profile-only", action="store_true", help="hardware profile, no trials")
    args = ap.parse_args(argv)

    params = {k: v for k, v in vars(args).items()}
    with RunLog("inference-benchmark", params=params) as log:
        prof = hardware_profile(args.model, measure_tps=not args.profile_only, host=args.host)
        log.write_json("hardware_profile", prof)
        print("hardware profile")
        for k, v in prof.items():
            if v != "":
                print(f"  {k:24s} {v}")
        if args.profile_only:
            print(f"\nresults -> {log.dir}")
            return 0

        warm = benchmark(args.model, args.trials, args.max_tokens, args.prompt, args.host, log)
        print(f"\nwarm path  ({warm['n_trials']} of {warm['n_trials_attempted']} trials warm, "
              f"{args.max_tokens} max tokens, served by {warm['served_models']} "
              f"on {warm['backend']} @ {warm['host']})")
        if warm["n_excluded_not_warm"]:
            print(f"  NOTE: {warm['n_excluded_not_warm']} trial(s) reported cold by /api/ps "
                  "and are excluded from these figures; see trials.csv")
        if warm["n_rows_missing_counters"]:
            print(f"  NOTE: {warm['n_rows_missing_counters']} trial(s) had counters the runtime "
                  "did not report; see the missing_counters column")
        print(f"  TTFT   mean {warm['ttft_ms_mean']:8.1f} ms   median {warm['ttft_ms_median']:8.1f} ms"
              f"   p95 {warm['ttft_ms_p95']:8.1f} ms")
        print(f"  total  mean {warm['total_ms_mean']:8.1f} ms   median {warm['total_ms_median']:8.1f} ms"
              f"   p95 {warm['total_ms_p95']:8.1f} ms")
        print(f"  tok/s  mean {warm['tokens_per_sec_mean']:8.2f}      "
              f"cpu {warm['cpu_percent_mean']:.1f}%")
        print(f"  load   mean {warm['load_ms_mean']:8.1f} ms   max    {warm['load_ms_max']:8.1f} ms"
              "   (should be ~0 on a genuinely warm path)")

        if args.cold_pairs > 0:
            cw = cold_vs_warm(args.model, args.cold_pairs, args.max_tokens,
                              args.prompt, args.host, log,
                              unload_wait_s=args.unload_wait)
            print(f"\ncold vs warm  ({cw['n_pairs']} pairs, model evicted before each cold trial)")
            print(f"  cold TTFT  mean {cw['cold_ttft_ms_mean']:9.1f} ms   "
                  f"median {cw['cold_ttft_ms_median']:9.1f} ms")
            print(f"  warm TTFT  mean {cw['warm_ttft_ms_mean']:9.1f} ms   "
                  f"median {cw['warm_ttft_ms_median']:9.1f} ms")
            print(f"  cold/warm ratio {cw['cold_over_warm_ratio']:.1f}x   "
                  f"penalty {cw['ttft_penalty_ms']:.0f} ms")

        if args.baseline:
            b = baseline(max_tokens=args.max_tokens, prompt=args.prompt,
                         base_url=args.baseline_url, model=args.baseline_model, log=log)
            print("\nhosted baseline")
            if b.get("skipped"):
                print(f"  SKIPPED: {b['reason']}")
            else:
                print(f"  {b['model']} @ {b['base_url']}")
                print(f"  TTFT   mean {b['ttft_ms_mean']:8.1f} ms   "
                      f"median {b['ttft_ms_median']:8.1f} ms   p95 {b['ttft_ms_p95']:8.1f} ms")

        print(f"\nresults -> {log.dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
