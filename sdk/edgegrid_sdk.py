"""A small client for the Edge Grid gateway.

The gateway is OpenAI-compatible on purpose - the `openai` package works against
it unchanged, and that is the migration story. This SDK exists for the other half:
it returns the pipeline evidence alongside the text, so a caller can see which node
served the request, what it was paid, whether the output was verified, and where
the commitment landed in the DA layer.

    from sdk import EdgeGrid

    grid = EdgeGrid("http://localhost:8000")
    c = grid.complete("why is TTFT the metric that matters?")
    print(c.text)
    print(c.provider, c.clearing_price, c.verdict, c.mode)

Streaming yields token deltas as they come off the winning node's runtime:

    for piece in grid.stream("count to five"):
        print(piece, end="", flush=True)

Every response reports the mode that served it (`p2p` or `local`). A `Completion`
never hides a degraded run: `notes` carries whatever the pipeline recorded.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

import httpx

DEFAULT_BASE_URL = "http://localhost:8000"


class EdgeGridError(RuntimeError):
    """The gateway refused or failed a request. Carries the server's own message."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


@dataclass
class Completion:
    """One completion plus the pipeline evidence behind it."""

    text: str
    model: str
    mode: str
    raw: dict = field(repr=False, default_factory=dict)

    @property
    def _eg(self) -> dict:
        return self.raw.get("edgegrid") or {}

    @property
    def job_id(self) -> Optional[str]:
        return self._eg.get("job_id")

    @property
    def provider(self) -> Optional[str]:
        return self._eg.get("provider_peer_id")

    @property
    def clearing_price(self) -> Optional[float]:
        """What the winner is actually paid - the runner-up's bid (second price)."""
        return self._eg.get("clearing_price_grid")

    @property
    def n_bids(self) -> Optional[int]:
        return self._eg.get("n_bids")

    @property
    def ttft_ms(self) -> Optional[float]:
        return self._eg.get("ttft_ms")

    @property
    def tokens_per_sec(self) -> Optional[float]:
        return self._eg.get("tokens_per_sec")

    @property
    def output_hash(self) -> Optional[str]:
        return self._eg.get("output_hash")

    @property
    def verdict(self) -> Optional[str]:
        """`pass`, `fail`, `error`, or None when the job was not sampled."""
        return self._eg.get("verdict")

    @property
    def sampled(self) -> bool:
        return bool(self._eg.get("sampled"))

    @property
    def settlement_state(self) -> Optional[str]:
        return self._eg.get("settlement_state")

    @property
    def usage(self) -> dict:
        return self.raw.get("usage") or {}

    @property
    def notes(self) -> list[str]:
        """Anything the pipeline degraded on. Empty is the clean case."""
        return list(self._eg.get("notes") or [])

    def summary(self) -> str:
        return (f"job {self.job_id} · mode {self.mode} · provider {str(self.provider)[-12:]} "
                f"· {self.n_bids} bids · clearing {self.clearing_price} GRID "
                f"· ttft {self.ttft_ms} ms · verdict {self.verdict or 'unsampled'} "
                f"· escrow {self.settlement_state}")


class EdgeGrid:
    """Client for one gateway."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = 300.0,
                 api_key: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        headers = {"accept": "application/json"}
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"
        self._http = httpx.Client(timeout=timeout, headers=headers)

    # -- lifecycle -------------------------------------------------------

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "EdgeGrid":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- internals -------------------------------------------------------

    def _payload(self, prompt: str, model: Optional[str], max_tokens: int,
                 temperature: float, verify: bool, stream: bool,
                 messages: Optional[list[dict]]) -> dict:
        return {
            "model": model or "default",
            "messages": messages or [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "verify": verify,
            "stream": stream,
        }

    @staticmethod
    def _raise(resp: httpx.Response) -> None:
        try:
            body = resp.json()
            msg = (body.get("error") or {}).get("message") or body.get("detail") or resp.text
        except Exception:
            msg = resp.text
        raise EdgeGridError(f"gateway returned {resp.status_code}: {msg}", resp.status_code)

    # -- completions -----------------------------------------------------

    def complete(self, prompt: str = "", *, model: Optional[str] = None,
                 max_tokens: int = 256, temperature: float = 0.7, verify: bool = False,
                 messages: Optional[list[dict]] = None) -> Completion:
        """Run one job to completion. `verify=True` forces verification instead of
        leaving it to the network's sampling rate."""
        r = self._http.post(f"{self.base_url}/v1/chat/completions",
                            json=self._payload(prompt, model, max_tokens, temperature,
                                               verify, False, messages))
        if r.status_code != 200:
            self._raise(r)
        body = r.json()
        text = ((body.get("choices") or [{}])[0].get("message") or {}).get("content", "")
        return Completion(text=text, model=body.get("model", ""),
                          mode=r.headers.get("x-edgegrid-mode", "unknown"), raw=body)

    def stream(self, prompt: str = "", *, model: Optional[str] = None,
               max_tokens: int = 256, temperature: float = 0.7, verify: bool = False,
               messages: Optional[list[dict]] = None) -> Iterator[str]:
        """Yield token deltas as the winning node produces them.

        After the iterator is exhausted, `last_completion` holds the final
        `Completion` with the pipeline evidence for the same job.
        """
        self.last_completion: Optional[Completion] = None
        payload = self._payload(prompt, model, max_tokens, temperature, verify, True, messages)
        with self._http.stream("POST", f"{self.base_url}/v1/chat/completions",
                               json=payload) as r:
            if r.status_code != 200:
                r.read()
                self._raise(r)
            mode = r.headers.get("x-edgegrid-mode", "unknown")
            pieces: list[str] = []
            final: dict = {}
            for line in r.iter_lines():
                if not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                obj = json.loads(data)
                if "error" in obj:
                    raise EdgeGridError(f"pipeline error: {obj['error'].get('message')}")
                choice = (obj.get("choices") or [{}])[0]
                piece = (choice.get("delta") or {}).get("content") or ""
                if piece:
                    pieces.append(piece)
                    yield piece
                if obj.get("edgegrid"):
                    final = obj
            self.last_completion = Completion(
                text="".join(pieces), model=final.get("model", payload["model"]),
                mode=mode, raw=final)

    # -- operator surface ------------------------------------------------

    def health(self) -> dict:
        """The gateway's own health. Deliberately does NOT raise on a non-200:
        /health answers 503 when the grid is degraded, and that body is the
        answer, not an error to hide."""
        return self._http.get(f"{self.base_url}/health").json()

    def models(self) -> list[str]:
        r = self._http.get(f"{self.base_url}/v1/models")
        if r.status_code != 200:
            self._raise(r)
        return [m["id"] for m in r.json().get("data", [])]

    def nodes(self) -> list[dict]:
        r = self._http.get(f"{self.base_url}/api/nodes")
        if r.status_code != 200:
            self._raise(r)
        return r.json().get("nodes", [])

    def jobs(self, limit: int = 50) -> list[dict]:
        r = self._http.get(f"{self.base_url}/api/jobs", params={"limit": limit})
        if r.status_code != 200:
            self._raise(r)
        return r.json().get("jobs", [])

    def job(self, job_id: str) -> dict:
        r = self._http.get(f"{self.base_url}/api/jobs/{job_id}")
        if r.status_code != 200:
            self._raise(r)
        return r.json()

    def audit(self, job_id: str) -> dict:
        """Force verification of a job sampling did not pick, and re-settle it."""
        r = self._http.post(f"{self.base_url}/api/jobs/{job_id}/verify")
        if r.status_code != 200:
            self._raise(r)
        return r.json()

    def stats(self) -> dict:
        # These two used to return `.json()` unconditionally, so a 503 body came
        # back looking like a stats dict with every metric missing, and a caller
        # reading `stats()["jobs_complete"]` got a KeyError far from the cause.
        r = self._http.get(f"{self.base_url}/api/stats")
        if r.status_code != 200:
            self._raise(r)
        return r.json()

    def settlements(self) -> dict:
        r = self._http.get(f"{self.base_url}/api/settlements")
        if r.status_code != 200:
            self._raise(r)
        return r.json()


# --------------------------------------------------------------------------
# demo
# --------------------------------------------------------------------------

def _demo(base_url: str = DEFAULT_BASE_URL) -> int:
    grid = EdgeGrid(base_url)
    try:
        health = grid.health()
    except Exception as exc:
        print(f"cannot reach the gateway at {base_url}: {type(exc).__name__}: {exc}")
        print("start it with: .venv/bin/python -m uvicorn gateway.app:app --port 8000")
        return 1

    rt = health["inference_runtime"]
    print(f"gateway {health['version']}  status={health['status']}  mode={health['mode']}")
    print(f"  reason        {health['mode_reason']}")
    print(f"  runtime       {rt['endpoint']} reachable={rt['reachable']}")
    print(f"  judge         {health['judge']['backend']} / {health['judge']['model']}")
    print(f"  models        {', '.join(grid.models()) or '(none)'}")

    print("\nnodes")
    for n in grid.nodes():
        print(f"  {n['label']:<7} {n['peer_id'][-12:]}  T{n['tier']}  "
              f"stake {n['stake']:>8.2f}  px/1k {n['price_per_1k']:.2f}  "
              f"warm {len(n['warm_models'])}  {'runtime' if n['executes'] else 'modelled'}")

    print("\nstreaming a job through the full pipeline")
    print("  > ", end="", flush=True)
    for piece in grid.stream("In one sentence: why does time-to-first-token matter?",
                             max_tokens=80, temperature=0.0, verify=True):
        print(piece, end="", flush=True)
    print()
    c = grid.last_completion
    if c is not None:
        print(f"  {c.summary()}")
        for note in c.notes:
            print(f"  note: {note}")

    print("\nnon-streaming job")
    c2 = grid.complete("What is 7 times 6? Answer with the number only.",
                       max_tokens=16, temperature=0.0)
    print(f"  text: {c2.text.strip()!r}")
    print(f"  {c2.summary()}")
    print(f"  usage: {c2.usage}")

    s = grid.stats()
    print(f"\ngrid: {s['jobs_complete']} complete, ttft mean {s['ttft_ms_mean']} ms, "
          f"{s['grid_escrowed']} GRID escrowed, verdicts "
          f"{s['verdict_pass']}/{s['verdict_fail']}/{s['verdict_error']} (pass/fail/error)")
    grid.close()
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_demo(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE_URL))
