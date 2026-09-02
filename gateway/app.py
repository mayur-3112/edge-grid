"""The Edge Grid gateway: an OpenAI-compatible front door to a decentralised grid.

The migration story in the Phase-1 design is "change the base URL". That only
means anything if the endpoint is genuinely the OpenAI chat-completions contract,
so `/v1/chat/completions` here is the real thing - streaming and non-streaming,
`chat.completion` / `chat.completion.chunk` objects, `usage`, `data: [DONE]` - and
the official `openai` python client can point at it unchanged.

Behind the endpoint every request runs the full pipeline: a signed JobRequest, a
sealed-bid second-price auction, streaming inference with a real TTFT measurement,
a DA commitment with a Merkle proof, sampled LLM-judge verification, and
settlement against real stake balances. Which backend served the request is
reported on every response as `x-edgegrid-mode` and in the `edgegrid` block of the
JSON body. A local run is never presented as a network run.

Run it:
    .venv/bin/python -m uvicorn gateway.app:app --port 8000
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, Union

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from edgegrid import config as C
from edgegrid.schemas import new_id, now_ms
from gateway import __version__
from gateway.events import STAGES, EventBus
from gateway.grid import GridError, LocalGrid, open_grid

STATIC_DIR = Path(__file__).parent / "static"

# Model aliases a client may send instead of a concrete Ollama tag. Resolution is
# reported back in the response `model` field, so it is never a silent swap.
MODEL_ALIASES = {"default", "auto", "edgegrid"}


# --------------------------------------------------------------------------
# OpenAI-compatible request types
# --------------------------------------------------------------------------

class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str = "user"
    content: Union[str, list, None] = None

    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, list):  # OpenAI content parts
            return "".join(p.get("text", "") for p in self.content
                           if isinstance(p, dict) and p.get("type") == "text")
        return ""


class ChatCompletionRequest(BaseModel):
    """Accepts extra fields because real OpenAI clients send plenty of them; the
    ones the grid honours are listed explicitly."""

    model_config = ConfigDict(extra="allow")

    model: str = Field(default_factory=lambda: C.OLLAMA_MODEL)
    messages: list[ChatMessage]
    stream: bool = False
    max_tokens: Optional[int] = None
    max_completion_tokens: Optional[int] = None
    temperature: float = 0.7
    # Edge Grid extensions - ignored by an OpenAI client, honoured here.
    max_price: float = 1.0
    max_latency_ms: int = 30_000
    verify: bool = Field(default=False, description="force verification of this job "
                                                    "instead of leaving it to sampling")

    def token_budget(self) -> int:
        return int(self.max_tokens or self.max_completion_tokens or 256)


# --------------------------------------------------------------------------
# app wiring
# --------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    bus = EventBus()
    grid, mode, reason = await open_grid(bus)
    app.state.bus = bus
    app.state.grid = grid
    app.state.mode = mode
    app.state.mode_reason = reason
    app.state.started_ms = now_ms()
    app.state.refresher = asyncio.create_task(_refresh_loop(grid))
    try:
        yield
    finally:
        app.state.refresher.cancel()
        try:
            await app.state.refresher
        except (asyncio.CancelledError, Exception):
            pass
        await grid.close()


async def _refresh_loop(grid: LocalGrid, interval_s: float = 10.0) -> None:
    """Keep node telemetry (warm models, health, load) current for the dashboard."""
    while True:
        await asyncio.sleep(interval_s)
        try:
            await grid.refresh_nodes()
        except Exception as exc:
            grid.bus.publish("log", level="error",
                             message=f"node refresh failed: {type(exc).__name__}: {exc}")


app = FastAPI(title="Edge Grid gateway", version=__version__, lifespan=lifespan,
              docs_url="/docs", openapi_url="/openapi.json")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])


@app.middleware("http")
async def stamp_mode(request: Request, call_next):
    """Label EVERY response with the backend that served it.

    The handlers set these headers on their success paths, but a 404, a 422 from
    request validation, or an unhandled 500 never reached that code and went out
    unlabelled - so exactly the responses an operator is most likely to be reading
    carried no statement of which backend produced them. Set here so there is no
    path out of the app that omits it.
    """
    response = await call_next(request)
    response.headers.setdefault("x-edgegrid-mode",
                                getattr(request.app.state, "mode", "unknown"))
    response.headers.setdefault("x-edgegrid-version", __version__)
    return response


def _grid(request: Request) -> LocalGrid:
    grid = getattr(request.app.state, "grid", None)
    if grid is None:
        raise HTTPException(503, detail="grid backend not started")
    return grid


def _mode_headers(request: Request) -> dict[str, str]:
    return {
        "x-edgegrid-mode": request.app.state.mode,
        "x-edgegrid-version": __version__,
    }


def _openai_error(message: str, type_: str, code: int) -> JSONResponse:
    return JSONResponse(status_code=code,
                        content={"error": {"message": message, "type": type_,
                                           "param": None, "code": None}})


def _resolve_model(grid: LocalGrid, requested: str) -> tuple[str, Optional[str]]:
    """Return (concrete model, note). Raises if the model cannot be served."""
    if requested in MODEL_ALIASES:
        return C.OLLAMA_MODEL, f"alias {requested!r} resolved to {C.OLLAMA_MODEL!r}"
    if requested in grid.available_models:
        return requested, None
    available = grid.available_models or ["(none - inference runtime unreachable)"]
    raise HTTPException(
        404, detail=f"model {requested!r} is not served by any node on this grid. "
                    f"available: {available}")


# --------------------------------------------------------------------------
# OpenAI-compatible surface
# --------------------------------------------------------------------------

@app.post("/v1/chat/completions")
async def chat_completions(body: ChatCompletionRequest, request: Request):
    grid = _grid(request)
    if not body.messages:
        return _openai_error("messages must not be empty", "invalid_request_error", 400)
    try:
        model, alias_note = _resolve_model(grid, body.model)
    except HTTPException as exc:
        return _openai_error(str(exc.detail), "invalid_request_error", exc.status_code)
    if not grid.ollama_ok:
        return _openai_error(
            f"no inference runtime reachable: {grid.ollama_error}", "api_error", 503)

    messages = [{"role": m.role, "content": m.text()} for m in body.messages]
    completion_id = f"chatcmpl-{new_id().replace('-', '')[:24]}"
    created = int(time.time())
    kwargs = dict(messages=messages, model=model, max_tokens=body.token_budget(),
                  temperature=body.temperature, max_price=body.max_price,
                  max_latency_ms=body.max_latency_ms, force_verify=body.verify,
                  client_label=request.headers.get("user-agent", "")[:80])

    if body.stream:
        return StreamingResponse(
            _sse_completion(grid, completion_id, created, model, alias_note, kwargs),
            media_type="text/event-stream",
            headers=_mode_headers(request) | {"cache-control": "no-store",
                                              "x-accel-buffering": "no"})

    text_parts: list[str] = []
    record: Optional[dict] = None
    async for kind, data in grid.run_job(**kwargs):
        if kind == "delta":
            text_parts.append(data)
        else:
            record = data
    if record is None or record["status"] == "error":
        detail = (record or {}).get("error", "pipeline produced no record")
        return _openai_error(detail, "api_error", 502)

    usage = record.get("usage", {})
    payload = {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": "".join(text_parts)},
                     "logprobs": None}],
        "usage": {"prompt_tokens": usage.get("prompt_tokens", 0),
                  "completion_tokens": usage.get("completion_tokens", 0),
                  "total_tokens": usage.get("total_tokens", 0)},
        "edgegrid": _summary(record, request.app.state.mode, alias_note),
    }
    return JSONResponse(payload, headers=_mode_headers(request))


async def _sse_completion(grid: LocalGrid, completion_id: str, created: int, model: str,
                          alias_note: Optional[str], kwargs: dict):
    """OpenAI `chat.completion.chunk` stream. Ends with `data: [DONE]`."""

    def chunk(delta: dict, finish: Optional[str] = None, **extra) -> str:
        obj = {"id": completion_id, "object": "chat.completion.chunk", "created": created,
               "model": model,
               "choices": [{"index": 0, "delta": delta, "finish_reason": finish,
                            "logprobs": None}], **extra}
        return f"data: {json.dumps(obj)}\n\n"

    yield chunk({"role": "assistant", "content": ""})
    record: Optional[dict] = None
    try:
        async for kind, data in grid.run_job(**kwargs):
            if kind == "delta":
                yield chunk({"content": data})
            else:
                record = data
    except Exception as exc:  # the pipeline itself blew up mid-stream
        yield _error_chunk(f"{type(exc).__name__}: {exc}")
        yield "data: [DONE]\n\n"
        return

    if record is not None and record["status"] == "error":
        # Headers are already on the wire, so a mid-stream failure is reported as an
        # error chunk rather than being swallowed to keep the stream looking clean.
        yield _error_chunk(record["error"])
        yield "data: [DONE]\n\n"
        return

    usage = (record or {}).get("usage", {})
    yield chunk({}, "stop",
                usage={"prompt_tokens": usage.get("prompt_tokens", 0),
                       "completion_tokens": usage.get("completion_tokens", 0),
                       "total_tokens": usage.get("total_tokens", 0)},
                edgegrid=_summary(record or {}, grid.mode, alias_note))
    yield "data: [DONE]\n\n"


def _error_chunk(message: str) -> str:
    return "data: " + json.dumps({"error": {"message": message, "type": "api_error"}}) + "\n\n"


def _summary(record: dict, mode: str, alias_note: Optional[str] = None) -> dict:
    """The `edgegrid` block attached to a completion: what the pipeline actually did."""
    award = record.get("award") or {}
    settlement = record.get("settlement") or {}
    verdict = record.get("verdict") or {}
    notes = [n["message"] for n in record.get("notes", [])]
    if alias_note:
        notes = [alias_note] + notes
    return {
        "mode": mode,
        "job_id": record.get("job_id"),
        "stages": {k: v["state"] for k, v in (record.get("stages") or {}).items()},
        "provider_peer_id": award.get("winner_peer_id"),
        # `n_bids` is the JobAward field, and it counts ELIGIBLE bids, not bids
        # received - a job that priced four bidders out reports n_bids=1. The full
        # auction census goes alongside it so the two cannot be confused.
        "n_bids": award.get("n_bids"),
        "auction": record.get("auction"),
        "clearing_price_grid": award.get("clearing_price"),
        "winning_bid_grid": award.get("winning_bid_price"),
        "auction_ms": award.get("auction_ms"),
        "ttft_ms": (record.get("result") or {}).get("ttft_ms"),
        "tokens_per_sec": (record.get("result") or {}).get("tokens_per_sec"),
        "output_hash": (record.get("result") or {}).get("output_hash"),
        "da": record.get("da"),
        "sampled": record.get("sampled"),
        "verdict": verdict.get("verdict"),
        "judge_backend": verdict.get("judge_backend"),
        "settlement_state": settlement.get("state"),
        "total_ms": record.get("total_ms"),
        "execution": record.get("execution"),
        "notes": notes,
        "detail_url": f"/api/jobs/{record.get('job_id')}",
    }


@app.get("/v1/models")
async def list_models(request: Request):
    grid = _grid(request)
    data = [{"id": m, "object": "model", "created": int(time.time()),
             "owned_by": "edgegrid",
             "edgegrid_providers": sum(1 for n in grid.nodes if m in n.record.models),
             "edgegrid_warm_providers": sum(1 for n in grid.nodes
                                            if m in n.record.warm_models)}
            for m in grid.available_models]
    return JSONResponse({"object": "list", "data": data}, headers=_mode_headers(request))


# --------------------------------------------------------------------------
# operator surface
# --------------------------------------------------------------------------

@app.get("/health")
async def health(request: Request):
    grid = getattr(request.app.state, "grid", None)
    ok = grid is not None and grid.ollama_ok
    body = {
        "status": "ok" if ok else "degraded",
        "version": __version__,
        "mode": getattr(request.app.state, "mode", "unknown"),
        "mode_reason": getattr(request.app.state, "mode_reason", ""),
        "uptime_ms": now_ms() - getattr(request.app.state, "started_ms", now_ms()),
        "inference_runtime": {
            "endpoint": grid.ollama_host if grid else None,
            "reachable": bool(grid and grid.ollama_ok),
            "error": grid.ollama_error if grid else "grid not started",
            "models": grid.available_models if grid else [],
        },
        "judge": {"backend": C.JUDGE_BACKEND, "model": C.JUDGE_MODEL,
                  "groq_key_set": bool(C.GROQ_API_KEY),
                  "pass_threshold": C.PASS_THRESHOLD},
        "nodes": len(grid.nodes) if grid else 0,
        "sample_rate": grid.sample_rate if grid else None,
        "stages": list(STAGES),
    }
    return JSONResponse(body, status_code=200 if ok else 503,
                        headers=_mode_headers(request))


@app.get("/api/nodes")
async def api_nodes(request: Request):
    grid = _grid(request)
    return JSONResponse({"mode": request.app.state.mode, "nodes": grid.node_views()},
                        headers=_mode_headers(request))


@app.get("/api/jobs")
async def api_jobs(request: Request, limit: int = Query(50, ge=1, le=500)):
    grid = _grid(request)
    return JSONResponse({"mode": request.app.state.mode, "jobs": grid.job_list(limit)},
                        headers=_mode_headers(request))


@app.get("/api/jobs/{job_id}")
async def api_job(job_id: str, request: Request):
    grid = _grid(request)
    rec = grid.jobs.get(job_id)
    if rec is None:
        raise HTTPException(404, detail=f"unknown job {job_id}")
    return JSONResponse(rec, headers=_mode_headers(request))


@app.post("/api/jobs/{job_id}/verify")
async def api_reverify(job_id: str, request: Request):
    """Operator audit: force verification of a job that sampling did not pick."""
    grid = _grid(request)
    try:
        rec = await grid.reverify(job_id)
    except GridError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    return JSONResponse(rec, headers=_mode_headers(request))


@app.get("/api/stats")
async def api_stats(request: Request):
    grid = _grid(request)
    return JSONResponse(grid.stats() | {"mode_reason": request.app.state.mode_reason},
                        headers=_mode_headers(request))


@app.get("/api/settlements")
async def api_settlements(request: Request, limit: int = Query(100, ge=1, le=1000)):
    grid = _grid(request)
    return JSONResponse({
        "mode": request.app.state.mode,
        "settlements": grid.settlements[-limit:][::-1],
        # Totals over rows an audit has not reversed. Without this a reader summing
        # the rows counts a reversed payout as money that moved.
        "totals": grid.ledger_totals(),
        "stakes": [{"peer_id": n.peer_id, "label": n.profile.label,
                    "short_id": n.peer_id[-12:],
                    "stake": round(grid.stakes.get(n.peer_id, 0.0), 6),
                    "earned": round(grid.earnings.get(n.peer_id, 0.0), 6),
                    "opening_stake": n.profile.stake} for n in grid.nodes],
        "treasury": round(grid.treasury, 6),
        "validator_earnings": round(grid.validator_earnings, 6),
    }, headers=_mode_headers(request))


@app.get("/api/events")
async def api_events(request: Request, replay: int = Query(60, ge=0, le=400),
                     follow: bool = Query(True)):
    """Server-sent stream of pipeline events. The dashboard's only data feed.

    `follow=false` sends the snapshot and the replayed history and then closes,
    which is what a script - or a test - wants: an unbounded SSE response cannot
    be closed from the client side without waiting on the server's next write.
    The dashboard uses the default, which streams until the client goes away.
    """
    bus: EventBus = request.app.state.bus
    grid = _grid(request)

    async def gen():
        yield ("event: hello\ndata: " + json.dumps({
            "mode": request.app.state.mode,
            "mode_reason": request.app.state.mode_reason,
            "stages": list(STAGES),
            "nodes": grid.node_views(),
            "stats": grid.stats(),
            "seq": bus.seq,
        }) + "\n\n")
        for ev in bus.replay(replay):
            yield f"data: {json.dumps(ev)}\n\n"
        if not follow:
            return
        async with bus.subscribe() as q:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    ev = await asyncio.wait_for(q.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(ev)}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers=_mode_headers(request) |
                             {"cache-control": "no-store", "x-accel-buffering": "no"})


@app.get("/api/config")
async def api_config(request: Request):
    """The full config snapshot the run is using. Nothing about a demo should have
    to be taken on trust."""
    return JSONResponse(C.snapshot(), headers=_mode_headers(request))


# --------------------------------------------------------------------------
# dashboard
# --------------------------------------------------------------------------

@app.get("/")
async def dashboard():
    index = STATIC_DIR / "index.html"
    if not index.exists():
        raise HTTPException(404, detail="dashboard assets missing")
    return FileResponse(index, media_type="text/html")


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
