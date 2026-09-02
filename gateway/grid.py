"""Grid backends behind the gateway, and the in-process `LocalGrid`.

WHY THIS FILE EXISTS
--------------------
The gateway must serve an OpenAI-compatible request by running the *real* Edge
Grid pipeline, not a scripted imitation of one. On a machine where a live P2P
swarm is running, that means talking to it. On a single laptop - which is where
this is developed, demoed and examined - there is no swarm, and the honest move
is not to pretend there is one.

So there are two backends and the gateway always says which one served a request:

  p2p   - a real libp2p swarm is reachable. Selected only when a transport module
          (`TRANSPORT_MODULES`) exposes `open_grid(bus)` AND it connects. No such
          module exists yet, so this path is currently never taken and `/health`
          reports EVERY attempt it made and how each one failed - a module that
          exists and crashes on import is named as a crashed transport, not
          silently masked by a later one that merely has no `open_grid`.
  local - `LocalGrid`, below. Same code paths, one process.

The auction is `edgegrid.market` in BOTH modes. What differs between them is the
transport - where the bids come from - not the clearing rule.

WHAT `LocalGrid` ACTUALLY DOES (and what it does not)
----------------------------------------------------
Real, not simulated:
  * every node has a real secp256k1 identity and a real libp2p PeerID derived
    from it, so a node's id here is the id it would have on the wire;
  * every JobRequest / Bid / JobAward / InferenceResult / Commitment / Verdict is
    a real `edgegrid.schemas` object, signed, and signature-checked before use.
    A bid is additionally bound to the node registry (`admission_reason`): a
    signature over a self-asserted `bidder_wallet` proves who owns that wallet
    and nothing about which peer bid, so the requester checks the claimed peer id
    against the wallet it registered. Every refusal is reported;
  * the auction is `edgegrid.market` itself - the gateway gathers signed bids and
    the market module owns eligibility, ranking and the threshold clearing price.
    There is no gateway-local copy of the clearing rule;
  * inference is a real streaming call to Ollama - TTFT is measured at the first
    token off the socket and token counts come from the runtime's `eval_count`,
    never from `len(output.split())`. If no token ever arrives there is no TTFT
    and the job fails rather than reporting total wall-clock in its place; if the
    runtime did not time the generation (see MIN_EVAL_DURATION_NS) throughput is
    recorded as 0 with a note, never divided out of a clock artefact;
  * the commitment is a real DA blob with a real Merkle inclusion proof, and the
    verifier re-fetches the blob and checks the proof before judging. A store
    that cannot answer is an outage (ERROR, escrow held); a store that answers
    with bytes that do not match the commitment is evidence (FAIL, slash). See
    `check_da` - collapsing those two is how an outage slashes an honest node;
  * the judge is a real LLM call. If the configured judge backend is unreachable
    or unparseable the verdict is ERROR. It never silently becomes a pass, a
    fail, or a mock;
  * settlement conserves value and slashes against real per-node stake balances.
    An operator audit reverses the prior settlement's value movement AND marks
    its ledger row reversed, so summing the ledger cannot double-count it.

Modelled, and labelled as such on every row:
  * there is one machine, so there is one inference runtime. All five nodes bid
    with distinct hardware profiles and prices, but whichever node wins, the
    tokens are physically produced by this host's Ollama. Every job record
    carries `execution.attributed_to_winner` and the gateway emits a `log` event
    whenever the winner is not the host node. The TTFT and token counts reported
    are true measurements of this machine, attributed to the winning node.
  * the four non-host nodes cannot execute; their `estimated_ttft_ms` is a
    hardware model, which is why bids are compared but only the host's real
    measurements are ever reported as `InferenceResult`.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Iterable, Optional

import httpx

from edgegrid import config as C
from edgegrid import market
from edgegrid.da import NAMESPACE_INFERENCE, DALayer, verify_proof
from edgegrid.identity import Identity, verify_message
from edgegrid.schemas import (
    Bid,
    Commitment,
    EscrowState,
    HardwareTier,
    InferenceResult,
    JobAward,
    JobRequest,
    NodeRecord,
    SettlementRecord,
    Verdict,
    VerdictKind,
    now_ms,
    sha256_hex,
)
from gateway.events import STAGES, EventBus

try:  # real PeerIDs when py-libp2p is present; a labelled fallback when it is not
    from libp2p.crypto.secp256k1 import Secp256k1PrivateKey
    from libp2p.peer.id import ID as PeerID
    import coincurve

    _LIBP2P_ERR: Optional[str] = None
except Exception as exc:  # pragma: no cover - exercised only without py-libp2p
    Secp256k1PrivateKey = None  # type: ignore[assignment]
    _LIBP2P_ERR = f"{type(exc).__name__}: {exc}"


JUDGE_SYSTEM_PROMPT = """You are an expert fact-checker grading an AI-generated answer.

Scoring rubric:
5 = completely correct and directly answers the question, no falsehoods.
4 = mostly correct, minor omissions or imprecision, fundamentally truthful.
3 = partially correct but with notable inaccuracies or misleading framing.
2 = mostly incorrect, clear factual errors or hallucination.
1 = completely wrong, fabricated, nonsensical, or off-topic.

Respond with ONLY a JSON object:
{"score": <1-5 integer>, "reason": "<one or two sentences>"}"""

# Judge-model sentinels. `Verdict.judge_model` is documented as "the model actually
# used, read back from the client". When no judge ran there is no such model, and
# naming the configured one would put a model on the row that was never called.
NO_JUDGE_CALLED = "(no judge called)"
JUDGE_NEVER_RESPONDED = "(judge never responded)"

# Outcomes of the data-availability check, kept apart because they mean opposite
# things. A store that cannot answer is an outage; a store that answers with the
# wrong bytes is evidence. Collapsing the two is how an infrastructure failure
# gets recorded as fraud detection and slashes an honest provider.
DA_OK = "ok"
DA_UNAVAILABLE = "unavailable"
DA_MISMATCH = "mismatch"

# A throughput figure is only a measurement if the runtime actually timed the
# generation. Ollama reports `eval_duration` = 1000 ns for a single-token
# response, which divides out to 1e6 tokens/sec - a clock-resolution artefact,
# not a rate. Below this the number is refused rather than recorded.
MIN_EVAL_DURATION_NS = 1_000_000  # 1 ms


def _peer_id_for(identity: Identity) -> str:
    """The libp2p PeerID this identity's key would produce on a real host."""
    if Secp256k1PrivateKey is None:
        return "nolibp2p-" + identity.pubkey_hex[:24]
    sk = Secp256k1PrivateKey(coincurve.PrivateKey(identity.seed_bytes))
    return PeerID.from_pubkey(sk.get_public_key()).to_string()


def _derived_identity(label: str) -> Identity:
    """Deterministic development key for a LocalGrid participant.

    Deterministic so that peer ids, wallets and stake balances are stable across
    gateway restarts. These are NOT production keys and are not written to disk;
    a real node uses `Identity.load_or_create`.
    """
    return Identity.from_hex(sha256_hex(f"edgegrid/localgrid/{label}"))


@dataclass
class NodeProfile:
    """The hardware and pricing model a LocalGrid node bids from."""

    label: str
    tier: HardwareTier
    cpu_count: int
    ram_gb: float
    vram_gb: float
    tokens_per_sec: float
    base_ttft_ms: float
    price_per_1k: float          # GRID per 1000 generated tokens
    stake: float
    warm: list[str] = field(default_factory=list)
    executes: bool = False       # only the host node owns a real runtime


# One host node (this machine) plus four modelled peers with different economics.
NODE_PROFILES: list[NodeProfile] = [
    NodeProfile("host", HardwareTier.CPU, 16, 30.0, 0.0, 12.0, 900.0, 0.60, 120.0,
                executes=True),
    NodeProfile("peer-a", HardwareTier.CPU, 8, 16.0, 0.0, 7.5, 1600.0, 0.42, 60.0),
    NodeProfile("peer-b", HardwareTier.LOW_GPU, 12, 32.0, 8.0, 28.0, 620.0, 0.78, 80.0),
    NodeProfile("peer-c", HardwareTier.DISCRETE_GPU, 24, 64.0, 24.0, 74.0, 240.0, 1.35, 250.0),
    NodeProfile("peer-d", HardwareTier.CPU, 4, 8.0, 0.0, 4.0, 2600.0, 0.31, 15.0),
]


@dataclass
class GridNode:
    """A node's identity and its current signed record.

    Deliberately holds no counters. `refresh_nodes()` rebuilds these objects every
    few seconds, and a job in flight holds a reference to the object that existed
    when it was awarded - so any counter living here would be incremented on an
    orphan and lost. Per-node counters live in `LocalGrid.node_stats`, keyed by
    peer id, which survives a rebuild.
    """

    profile: NodeProfile
    identity: Identity
    record: NodeRecord
    last_seen_ms: int = field(default_factory=now_ms)
    healthy: bool = True

    @property
    def peer_id(self) -> str:
        return self.record.peer_id


class GridError(RuntimeError):
    """A pipeline step failed in a way the caller must surface, never paper over."""


class LocalGrid:
    """The in-process Edge Grid. See the module docstring for exactly what is real."""

    mode = "local"

    def __init__(self, bus: EventBus, ollama_host: Optional[str] = None,
                 da: Optional[DALayer] = None, sample_rate: Optional[float] = None,
                 max_jobs: int = 500):
        self.bus = bus
        self.ollama_host = (ollama_host or C.OLLAMA_HOST).rstrip("/")
        self.da = da if da is not None else DALayer()
        self.sample_rate = C.SAMPLE_RATE if sample_rate is None else sample_rate
        self.max_jobs = max_jobs

        self.requester = _derived_identity("gateway-requester")
        self.requester_peer_id = _peer_id_for(self.requester)
        self.validator = _derived_identity("validator-0")
        self.validator_peer_id = _peer_id_for(self.validator)

        self.nodes: list[GridNode] = []
        # Stake is collateral and only ever moves on a slash. Payouts are revenue and
        # are tracked separately, so a well-paid node cannot look over-collateralised.
        self.stakes: dict[str, float] = {}
        self.earnings: dict[str, float] = {}
        self.node_stats: dict[str, dict] = {}
        self.treasury: float = 0.0
        self.validator_earnings: float = 0.0

        self.jobs: dict[str, dict] = {}
        self.job_order: list[str] = []
        self.settlements: list[dict] = []

        self.ollama_ok = False
        self.ollama_error: Optional[str] = None
        self.host_hardware_error: Optional[str] = None
        self.available_models: list[str] = []
        self._client: Optional[httpx.AsyncClient] = None
        self._started_ms = now_ms()

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(C.INFERENCE_TIMEOUT_S,
                                                               connect=5.0))
        await self.refresh_nodes()

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise GridError("LocalGrid.start() was never awaited")
        return self._client

    def _node(self, peer_id: str) -> GridNode:
        """The registered node for a peer id. A bare `next(...)` here raises
        StopIteration, which inside an async generator surfaces as an unrelated
        RuntimeError and loses the reason."""
        node = next((n for n in self.nodes if n.peer_id == peer_id), None)
        if node is None:
            raise GridError(f"peer {peer_id} is no longer in the node registry")
        return node

    def _ns(self, peer_id: str) -> dict:
        """Per-node counters, keyed by peer id so they outlive a roster rebuild."""
        return self.node_stats.setdefault(
            peer_id, {"inflight": 0, "jobs_served": 0, "last_ttft_ms": 0.0})

    # -- node roster -----------------------------------------------------

    async def refresh_nodes(self) -> list[dict]:
        """Rebuild the roster. The host node's capabilities are read live from
        Ollama (`/api/tags`, `/api/ps`) and psutil; modelled peers derive theirs
        from the host's model list so the auction is over a shared catalogue."""
        tags, warm = await self._probe_ollama()
        self.available_models = tags

        host_cpu, host_ram = self._host_hardware()
        first = not self.nodes
        nodes: list[GridNode] = []
        for i, prof in enumerate(NODE_PROFILES):
            ident = _derived_identity(prof.label)
            peer_id = _peer_id_for(ident)
            if prof.executes:
                cpu_count, ram_gb = host_cpu, host_ram
                models, warm_models = tags, warm
            else:
                cpu_count, ram_gb = prof.cpu_count, prof.ram_gb
                models = tags
                # A modelled peer keeps a deterministic subset of the catalogue warm.
                warm_models = [m for j, m in enumerate(tags) if (i + j) % 2 == 0]
            rec = NodeRecord(
                peer_id=peer_id,
                wallet_address=ident.address,
                pubkey_hex=ident.pubkey_hex,
                multiaddrs=[f"/ip4/127.0.0.1/tcp/{9000 + i}/p2p/{peer_id}"],
                tier=prof.tier,
                models=models,
                warm_models=warm_models,
                cpu_count=cpu_count,
                ram_gb=round(ram_gb, 2),
                vram_gb=prof.vram_gb,
                tokens_per_sec=prof.tokens_per_sec,
                stake=self.stakes.get(peer_id, prof.stake),
                updated_ms=now_ms(),
            )
            ident.sign_message(rec)
            node = GridNode(profile=prof, identity=ident, record=rec)
            node.healthy = self.ollama_ok if prof.executes else True
            nodes.append(node)
            self.stakes.setdefault(peer_id, prof.stake)

        self.nodes = nodes
        views = self.node_views()
        self.bus.publish("node", {"nodes": views, "first": first,
                                  "ollama_ok": self.ollama_ok,
                                  "ollama_error": self.ollama_error})
        return views

    def _host_hardware(self) -> tuple[int, float]:
        """The host's real cpu/ram, or the schema's unset values plus a recorded error.

        It used to fall back to `NODE_PROFILES[0]`, which meant a failed measurement
        was silently replaced by the modelled peer profile - 16 cores and 30 GB
        published in a signed NodeRecord as though psutil had reported them. When
        the measurement fails the record carries 0, which is what the schema uses
        for "not known", and `host_hardware_error` says why.
        """
        try:
            import psutil

            cpu = psutil.cpu_count(logical=True)
            ram = psutil.virtual_memory().total / 1e9
        except Exception as exc:
            self.host_hardware_error = f"{type(exc).__name__}: {exc}"
            self.bus.publish("log", level="error",
                             message=f"host hardware could not be measured "
                                     f"({self.host_hardware_error}); the host NodeRecord "
                                     f"reports 0 cores / 0 GB rather than a modelled value")
            return 0, 0.0
        self.host_hardware_error = None
        return cpu or 0, ram

    async def _probe_ollama(self) -> tuple[list[str], list[str]]:
        tags: list[str] = []
        warm: list[str] = []
        try:
            r = await self.client.get(f"{self.ollama_host}/api/tags", timeout=5.0)
            r.raise_for_status()
            tags = sorted(m["name"] for m in r.json().get("models", []))
            p = await self.client.get(f"{self.ollama_host}/api/ps", timeout=5.0)
            p.raise_for_status()
            warm = sorted(m["name"] for m in p.json().get("models", []))
            self.ollama_ok, self.ollama_error = True, None
        except Exception as exc:
            self.ollama_ok = False
            self.ollama_error = f"{type(exc).__name__}: {exc}"
            self.bus.publish("log", level="error",
                             message=f"ollama unreachable at {self.ollama_host}: "
                                     f"{self.ollama_error}")
        return tags, warm

    def node_views(self) -> list[dict]:
        """Dashboard projection: NodeRecord fields plus live telemetry. Kept
        separate from NodeRecord because that schema forbids extra fields."""
        # A failed telemetry read is reported as null, never as 0.0 - a dashboard
        # cannot tell "idle" from "we could not look" if both render as zero.
        cpu_percent: Optional[float]
        ram_free: Optional[float]
        try:
            import psutil

            cpu_percent = psutil.cpu_percent(interval=None)
            ram_free = psutil.virtual_memory().available / 1e9
            telemetry_error = None
        except Exception as exc:
            cpu_percent, ram_free = None, None
            telemetry_error = f"{type(exc).__name__}: {exc}"
        out = []
        for n in self.nodes:
            r = n.record
            out.append({
                "peer_id": r.peer_id,
                "short_id": r.peer_id[-12:],
                "label": n.profile.label,
                "wallet": r.wallet_address,
                "tier": int(r.tier),
                "tier_name": r.tier.name,
                "models": r.models,
                "warm_models": r.warm_models,
                "cpu_count": r.cpu_count,
                "ram_gb": r.ram_gb,
                "vram_gb": r.vram_gb,
                "tokens_per_sec": r.tokens_per_sec,
                "stake": round(self.stakes.get(r.peer_id, r.stake), 4),
                "opening_stake": n.profile.stake,
                "earned": round(self.earnings.get(r.peer_id, 0.0), 6),
                "price_per_1k": n.profile.price_per_1k,
                "base_ttft_ms": n.profile.base_ttft_ms,
                "last_ttft_ms": round(self._ns(r.peer_id)["last_ttft_ms"], 1),
                "jobs_served": self._ns(r.peer_id)["jobs_served"],
                "inflight": self._ns(r.peer_id)["inflight"],
                "healthy": n.healthy,
                "executes": n.profile.executes,
                "cpu_percent": (round(cpu_percent, 1)
                                if n.profile.executes and cpu_percent is not None else None),
                "ram_available_gb": (round(ram_free, 2)
                                     if n.profile.executes and ram_free is not None else None),
                "telemetry_error": telemetry_error if n.profile.executes else None,
                "hardware_measured": (self.host_hardware_error is None
                                      if n.profile.executes else False),
                "updated_ms": r.updated_ms,
                "signature_valid": verify_message(r, r.wallet_address),
            })
        return out

    # -- auction ---------------------------------------------------------

    def _bid_for(self, node: GridNode, job: JobRequest) -> Optional[Bid]:
        """What this node would bid, or None if it declines to bid at all.

        A node only declines for reasons it owns: it does not serve the model, or
        it is unhealthy. Eligibility - tier, latency budget, price ceiling,
        signature - is the requester's call and is decided by `edgegrid.market`,
        so an over-budget bid still reaches the auction and gets a recorded
        rejection reason instead of vanishing here.
        """
        p = node.profile
        if job.model not in node.record.models:
            return None
        if not node.healthy:
            return None
        warm = job.model in node.record.warm_models
        # Deterministic per (job, node) jitter: reproducible auctions, no RNG state.
        jitter = int(hashlib.sha256((job.job_id + node.peer_id).encode()).hexdigest()[:4], 16)
        jitter_factor = 0.85 + (jitter % 1000) / 1000.0 * 0.30
        ttft = p.base_ttft_ms * (0.55 if warm else 1.0) * jitter_factor
        ttft += self._ns(node.peer_id)["inflight"] * 250.0
        price = round(p.price_per_1k * (job.max_tokens / 1000.0) * jitter_factor, 6)
        bid = Bid(
            job_id=job.job_id,
            bidder_peer_id=node.peer_id,
            bidder_wallet=node.identity.address,
            price=price,
            estimated_ttft_ms=round(ttft, 1),
            warm=warm,
            tier=node.record.tier,
            stake=self.stakes.get(node.peer_id, p.stake),
        )
        node.identity.sign_message(bid)
        return bid

    @staticmethod
    def effective_price(bid: Bid) -> float:
        """Ranking price, from `edgegrid.market` so there is one definition of the
        warm-start handicap in the codebase rather than a gateway-local copy."""
        return market.effective_price(bid)

    def admission_reason(self, bid: Bid) -> Optional[str]:
        """Why the requester will not admit this bid to the auction, or None.

        These are registry rules, not auction rules, which is why they live here
        and not in `edgegrid.market`. The important one is the identity binding.
        `market.exclusion_reason` verifies a bid's signature against
        `bid.bidder_wallet` - a field the bidder writes itself - so on its own that
        check proves the bid was signed by whoever owns the wallet named in it, and
        nothing at all about *which peer* bid. A bidder can therefore claim another
        peer's `bidder_peer_id` (and its tier and stake) while naming its own wallet
        for the payout. The requester holds the node registry, so the requester is
        the party that can bind the two, and it must, before the market ever ranks
        the bid.
        """
        node = next((n for n in self.nodes if n.peer_id == bid.bidder_peer_id), None)
        if node is None:
            return f"bidder {bid.bidder_peer_id[-12:]} is not in the node registry"
        registered = node.record.wallet_address
        if bid.bidder_wallet.lower() != registered.lower():
            return (f"bidder_wallet {bid.bidder_wallet} is not the wallet registered for "
                    f"peer {bid.bidder_peer_id[-12:]} ({registered}) - the signature "
                    f"proves ownership of the wallet in the bid, not of the peer id")
        if not verify_message(bid, registered):
            return f"bid is not signed by the registered key for peer {bid.bidder_peer_id[-12:]}"
        if bid.stake < C.MIN_STAKE:
            return (f"stake {bid.stake:.2f} < MIN_STAKE {C.MIN_STAKE:.2f}, not admitted")
        held = self.stakes.get(bid.bidder_peer_id, node.record.stake)
        if bid.stake > max(held, node.record.stake) + 1e-9:
            return (f"bid claims stake {bid.stake:.2f} but the registry holds "
                    f"{held:.2f} for that peer")
        return None

    def run_auction(self, job: JobRequest,
                    extra_bids: Iterable[Bid] = ()) -> tuple["market.AuctionOutcome",
                                                             list[str]]:
        """Collect bids and clear them through `edgegrid.market`.

        The gateway does not implement its own clearing rule. It gathers signed
        bids, applies the registry rules in `admission_reason` (which the market
        module cannot apply, because it does not hold the registry), and hands the
        survivors to the market module, which owns eligibility, the deterministic
        ranking, and the threshold (Vickrey) clearing price.

        `extra_bids` is the seam a p2p backend uses: bids that arrived over the
        wire go through exactly the same admission gate as the locally generated
        ones. Every refusal is returned in `drops` and noted on the job record.
        """
        t0 = time.monotonic()
        bids: list[Bid] = []
        drops: list[str] = []
        candidates = [(n.profile.label, self._bid_for(n, job)) for n in self.nodes]
        candidates += [("wire", b) for b in extra_bids]
        for label, bid in candidates:
            if bid is None:
                continue
            refusal = self.admission_reason(bid)
            if refusal is not None:
                drops.append(f"{label} {bid.bidder_peer_id[-12:]}: {refusal}")
                continue
            bids.append(bid)
            self.bus.publish("bid", job_id=job.job_id, bid=bid.model_dump(mode="json"),
                             effective_price=round(self.effective_price(bid), 6))
        outcome = market.evaluate(bids, job,
                                  auction_ms=round((time.monotonic() - t0) * 1000.0, 3),
                                  require_signature=True)
        for sb in outcome.rejected:
            drops.append(f"{sb.bid.bidder_peer_id[-12:]}: rejected by the auction "
                         f"({sb.reason})")
        if outcome.award is not None:
            self.requester.sign_message(outcome.award)
        return outcome, drops

    # -- inference -------------------------------------------------------

    async def _stream_inference(self, job: JobRequest, messages: list[dict],
                                node: GridNode, temperature: float) -> AsyncIterator[tuple[str, Any]]:
        """Stream real tokens from Ollama, measuring TTFT off the first chunk."""
        if not self.ollama_ok:
            raise GridError(f"no inference runtime: ollama unreachable at "
                            f"{self.ollama_host} ({self.ollama_error})")
        body = {
            "model": job.model,
            "messages": messages,
            "stream": True,
            "options": {"num_predict": job.max_tokens, "temperature": temperature},
        }
        t0 = time.monotonic()
        ttft_ms: Optional[float] = None
        chunks: list[str] = []
        final: dict = {}
        async with self.client.stream("POST", f"{self.ollama_host}/api/chat",
                                      json=body) as resp:
            if resp.status_code != 200:
                detail = (await resp.aread()).decode("utf-8", "replace")[:400]
                raise GridError(f"ollama /api/chat returned {resp.status_code}: {detail}")
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise GridError(f"ollama emitted an unparseable stream line: {exc}") from exc
                if obj.get("error"):
                    raise GridError(f"ollama error: {obj['error']}")
                piece = (obj.get("message") or {}).get("content", "")
                if piece:
                    if ttft_ms is None:
                        ttft_ms = (time.monotonic() - t0) * 1000.0
                    chunks.append(piece)
                    yield "delta", piece
                if obj.get("done"):
                    final = obj
        total_ms = (time.monotonic() - t0) * 1000.0
        output = "".join(chunks)
        if ttft_ms is None:
            # No content token ever came off the socket, so there is no
            # time-to-first-token. Reporting `total_ms` in its place - which is
            # what this used to do - puts a fabricated latency measurement on the
            # record, into `ttft_ms_mean`, and into any figure quoted from it.
            raise GridError(
                f"the runtime streamed no content token (done_reason="
                f"{final.get('done_reason', '')!r}, eval_count={final.get('eval_count')}); "
                f"there is no time-to-first-token to report and no output to commit")
        # Token counts come from the runtime. If the runtime did not report them we
        # say so rather than substituting a word count.
        eval_count = final.get("eval_count")
        eval_ns = final.get("eval_duration")
        prompt_count = final.get("prompt_eval_count")
        # See MIN_EVAL_DURATION_NS: a rate divided out of a sub-millisecond clock
        # reading is an artefact, not throughput.
        throughput_measured = bool(
            eval_count and isinstance(eval_ns, (int, float))
            and eval_ns >= MIN_EVAL_DURATION_NS)
        tps = (eval_count / (eval_ns / 1e9)) if throughput_measured else 0.0
        result = InferenceResult(
            job_id=job.job_id,
            provider_peer_id=node.peer_id,
            output=output,
            model=job.model,
            tokens_generated=int(eval_count) if eval_count is not None else 0,
            ttft_ms=round(ttft_ms, 3),
            total_ms=round(total_ms, 3),
            tokens_per_sec=round(tps, 3),
            warm=job.model in node.record.warm_models,
            output_hash=InferenceResult.hash_output(output),
        )
        node.identity.sign_message(result)
        yield "result", {
            "result": result,
            "prompt_tokens": int(prompt_count) if prompt_count is not None else 0,
            "prompt_tokens_reported": prompt_count is not None,
            "token_counts_reported": eval_count is not None,
            "throughput_measured": throughput_measured,
            "eval_duration_ns": eval_ns,
            "load_duration_ms": round((final.get("load_duration") or 0) / 1e6, 3),
            "done_reason": final.get("done_reason", ""),
        }

    # -- commitment ------------------------------------------------------

    def commit(self, job: JobRequest, result: InferenceResult,
               node: GridNode) -> tuple[Commitment, str]:
        """Post the output to the DA layer and bind it with a signed commitment.

        Two blobs go into one DA block: a provenance record, then the raw output.
        The output blob is submitted last and unpadded, so `sha256(blob) ==
        commitment.output_hash` and a verifier can check both the hash and the
        Merkle proof knowing only the commitment. Batching the provenance blob
        alongside it is not cosmetic - a block with a single leaf has an empty
        inclusion proof, which proves nothing about the tree.
        """
        provenance = json.dumps({
            "job_id": job.job_id,
            "provider_peer_id": node.peer_id,
            "model": result.model,
            "prompt_hash": sha256_hex(job.prompt),
            "output_hash": result.output_hash,
            "tokens_generated": result.tokens_generated,
            "ttft_ms": result.ttft_ms,
            "created_ms": now_ms(),
        }, sort_keys=True, separators=(",", ":"))
        meta_blob = self.da.submit_blob(provenance, namespace=NAMESPACE_INFERENCE, seal=False)
        blob = self.da.submit_blob(result.output, namespace=NAMESPACE_INFERENCE, seal=True)
        commitment = Commitment(
            job_id=job.job_id,
            provider_peer_id=node.peer_id,
            output_hash=result.output_hash,
            namespace=NAMESPACE_INFERENCE,
            blob_ref=blob.blob_id,
            blob_height=blob.height,
            prompt_hash=sha256_hex(job.prompt),
        )
        node.identity.sign_message(commitment)
        return commitment, meta_blob.blob_id

    # -- verification ----------------------------------------------------

    def check_da(self, commitment: Commitment) -> tuple[str, str]:
        """Re-derive the DA check from its primitives, returning (status, detail).

        `DALayer.verify_blob` answers one boolean for three different situations,
        and two of them mean opposite things:

          * the store cannot produce the blob or a proof -> the verifier has no
            evidence either way. That is an outage (DA_UNAVAILABLE) and must not
            cost the provider its stake, exactly as a judge outage must not.
          * the store produces bytes that do not hash to the committed
            `output_hash`, or a proof that does not land on the block root ->
            the provider is bound to something it did not produce. That is
            evidence (DA_MISMATCH), and it is what slashing is for.

        The detail string says which one happened and is written onto the verdict,
        so no row ever records "DA check failed" without saying how.
        """
        data = self.da.get_blob(commitment.blob_ref)
        if data is None:
            return (DA_UNAVAILABLE,
                    f"the DA store did not return blob {commitment.blob_ref}")
        got = sha256_hex(data)
        if got != commitment.output_hash:
            return (DA_MISMATCH,
                    f"blob {commitment.blob_ref} hashes to {got[:16]} but the provider "
                    f"committed to {commitment.output_hash[:16]}")
        got_proof = self.da.inclusion_proof(commitment.blob_ref)
        if got_proof is None:
            return (DA_UNAVAILABLE,
                    f"the DA store could not produce an inclusion proof for "
                    f"blob {commitment.blob_ref}")
        proof, root = got_proof
        if not verify_proof(data, proof, root):
            return (DA_MISMATCH,
                    f"the inclusion proof for blob {commitment.blob_ref} does not verify "
                    f"against block root {root[:16]}")
        return DA_OK, ""

    async def verify(self, job: JobRequest, result: InferenceResult,
                     commitment: Commitment, question: str) -> Verdict:
        """Re-fetch the blob, check the Merkle proof, then judge the answer.

        Every failure mode here is an explicit verdict carrying the real backend
        name and the real reason. Neither a judge outage nor a DA outage is ever
        recorded as fraud detection."""
        t0 = time.monotonic()
        da_status, da_detail = self.check_da(commitment)
        blob_ok = da_status == DA_OK
        backend = C.JUDGE_BACKEND.lower()

        def _verdict(kind: VerdictKind, *, score=None, reason="", model="") -> Verdict:
            v = Verdict(
                job_id=job.job_id,
                validator_peer_id=self.validator_peer_id,
                verdict=kind,
                quality_score=score,
                judge_score=float(score) if score is not None else None,
                reason=reason,
                judge_backend=backend,
                judge_model=model,
                blob_verified=blob_ok,
                latency_ms=round((time.monotonic() - t0) * 1000.0, 3),
            )
            self.validator.sign_message(v)
            return v

        if da_status == DA_UNAVAILABLE:
            # No evidence either way. Hold the escrow; do not touch the stake.
            return _verdict(VerdictKind.ERROR,
                            reason=f"DA unavailable: {da_detail}; the verifier has no "
                                   f"evidence for or against the provider, so this is "
                                   f"neither a pass nor a fail",
                            model=NO_JUDGE_CALLED)
        if da_status == DA_MISMATCH:
            return _verdict(VerdictKind.FAIL, reason=f"DA mismatch: {da_detail}",
                            model=NO_JUDGE_CALLED)
        try:
            score, reason, model = await self._judge(question, result.output)
        except Exception as exc:
            return _verdict(VerdictKind.ERROR,
                            reason=f"judge unavailable: {type(exc).__name__}: {exc}",
                            model=JUDGE_NEVER_RESPONDED)
        kind = VerdictKind.PASS if score >= C.PASS_THRESHOLD else VerdictKind.FAIL
        return _verdict(kind, score=score, reason=reason, model=model)

    async def _judge(self, question: str, output: str) -> tuple[int, str, str]:
        """Call the configured judge. Raises on anything it cannot honestly parse."""
        backend = C.JUDGE_BACKEND.lower()
        user = (f"Question: {question}\nAI-generated answer: {output}\n\n"
                f"Evaluate the answer now.")
        if backend == "groq":
            if not C.GROQ_API_KEY:
                raise GridError("JUDGE_BACKEND=groq but GROQ_API_KEY is empty; refusing to "
                                "fall back to another backend silently")
            from groq import AsyncGroq

            client = AsyncGroq(api_key=C.GROQ_API_KEY)
            resp = await client.chat.completions.create(
                model=C.GROQ_JUDGE_MODEL,
                messages=[{"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                          {"role": "user", "content": user}],
                temperature=0.0,
                response_format={"type": "json_object"},
            )
            return (*self._parse_judge(resp.choices[0].message.content), resp.model)
        if backend == "ollama":
            r = await self.client.post(
                f"{self.ollama_host}/api/chat",
                json={"model": C.JUDGE_MODEL,
                      "messages": [{"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                                   {"role": "user", "content": user}],
                      "stream": False, "format": "json",
                      "options": {"temperature": 0.0}},
                timeout=C.INFERENCE_TIMEOUT_S)
            r.raise_for_status()
            payload = r.json()
            return (*self._parse_judge((payload.get("message") or {}).get("content", "")),
                    payload.get("model", C.JUDGE_MODEL))
        if backend == "mock":
            # Reachable only when JUDGE_BACKEND=mock is set deliberately. It is
            # recorded on the verdict as judge_backend="mock" so no result derived
            # from it can be mistaken for a real judgement.
            score = 5 if len(output.strip()) > 40 else 2
            return score, "heuristic length rule (JUDGE_BACKEND=mock)", "heuristic-rule-judge"
        raise GridError(f"unsupported JUDGE_BACKEND={C.JUDGE_BACKEND!r}")

    @staticmethod
    def _parse_judge(text: str) -> tuple[int, str]:
        if not text:
            raise GridError("judge returned an empty response")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                raise GridError(f"judge response was not JSON: {text[:200]!r}") from None
            data = json.loads(text[start:end + 1])
        if "score" not in data:
            raise GridError(f"judge response had no score field: {text[:200]!r}")
        score = int(data["score"])
        if not 1 <= score <= 5:
            raise GridError(f"judge score {score} outside the 1-5 rubric")
        return score, str(data.get("reason", "")).strip()

    # -- settlement ------------------------------------------------------

    def settle(self, job: JobRequest, award: JobAward, verdict: Optional[Verdict],
               node: GridNode) -> SettlementRecord:
        """Release or slash the escrow. Value conserves: the escrowed amount ends
        up entirely in provider payout, requester refund, validator reward and
        treasury, or stays escrowed pending re-verification."""
        amount = award.clearing_price
        kind = verdict.verdict if verdict else None
        rec = SettlementRecord(
            job_id=job.job_id,
            provider_peer_id=node.peer_id,
            requester_peer_id=job.requester_peer_id,
            amount=amount,
            state=EscrowState.SETTLED,
            challenge_deadline_ms=now_ms() + int(C.CHALLENGE_WINDOW_S * 1000),
            remaining_stake=self.stakes.get(node.peer_id, 0.0),
        )
        if kind == VerdictKind.FAIL:
            available = self.stakes.get(node.peer_id, 0.0)
            slash = min(amount, available)
            rec.state = EscrowState.SLASHED
            rec.slashed = True
            # No rounding anywhere in the split: the treasury share is the exact
            # complement of the validator share, so validator + treasury == slash
            # to the last bit. Rounding here would leave an unaccounted residue,
            # which is precisely the accounting error this record exists to rule
            # out. Rounding is a presentation concern and happens in the API layer.
            rec.slash_amount = slash
            rec.validator_reward = slash * C.VALIDATOR_SLASH_SHARE
            rec.treasury_amount = slash - rec.validator_reward
            rec.provider_payout = 0.0
            rec.requester_refund = amount
            rec.fully_covered = slash >= amount
        elif kind == VerdictKind.ERROR:
            # The judge could not rule. Funds stay escrowed; nothing is paid and
            # nothing is slashed on the strength of an outage.
            rec.state = EscrowState.AWAITING_VERIFICATION
            rec.provider_payout = 0.0
            rec.requester_refund = 0.0
        else:
            rec.provider_payout = amount
        self._apply(rec, +1)
        rec.remaining_stake = self.stakes.get(node.peer_id, 0.0)
        return rec

    def _apply(self, rec: SettlementRecord, sign: int) -> None:
        """Move value for a settlement record. `sign=-1` reverses it, which is what
        an operator re-audit of an already-settled job does."""
        if rec.slashed:
            self.stakes[rec.provider_peer_id] = (
                self.stakes.get(rec.provider_peer_id, 0.0) - sign * rec.slash_amount)
            self.validator_earnings += sign * rec.validator_reward
            self.treasury += sign * rec.treasury_amount
        if rec.provider_payout:
            self.earnings[rec.provider_peer_id] = (
                self.earnings.get(rec.provider_peer_id, 0.0) + sign * rec.provider_payout)

    # -- the pipeline ----------------------------------------------------

    async def run_job(self, *, messages: list[dict], model: str, max_tokens: int = 256,
                      temperature: float = 0.7, max_price: float = 1.0,
                      max_latency_ms: int = 30_000, force_verify: bool = False,
                      client_label: str = "") -> AsyncIterator[tuple[str, Any]]:
        """Run one job through all five stages, yielding as it goes.

        Yields ("stage", dict) as stages open and close, ("delta", str) for every
        real token off the runtime, and finally ("record", dict) - always, including
        on failure, so a caller never has to guess what happened.
        """
        prompt_text = _flatten(messages)
        question = _last_user(messages)
        job = JobRequest(
            prompt=prompt_text,
            model=model,
            max_tokens=max_tokens,
            requester_peer_id=self.requester_peer_id,
            requester_wallet=self.requester.address,
            max_price=max_price,
            max_latency_ms=max_latency_ms,
        )
        self.requester.sign_message(job)

        rec: dict[str, Any] = {
            "job_id": job.job_id,
            "created_ms": job.created_ms,
            "mode": self.mode,
            "model": model,
            "client": client_label,
            "status": "running",
            "question": question,
            "stages": {s: {"state": "pending", "ms": None} for s in STAGES},
            "request": job.model_dump(mode="json"),
            "bids": [], "award": None, "result": None, "commitment": None,
            "verdict": None, "settlement": None,
            "sampled": False, "sample_rate": self.sample_rate, "forced_verify": force_verify,
            "notes": [], "error": None, "total_ms": None,
            "execution": {
                "runtime": "ollama",
                "endpoint": self.ollama_host,
                "attributed_to_winner": False,
                "executed_by_peer_id": None,
            },
        }
        self._store(rec)
        self.bus.publish("job.created", job_id=job.job_id, job=rec["request"],
                         question=question, mode=self.mode)
        t_job = time.monotonic()

        def stage(name: str, state: str, ms: Optional[float] = None, **extra) -> None:
            rec["stages"][name] = {"state": state, "ms": None if ms is None else round(ms, 2)}
            self.bus.publish("job.stage", job_id=job.job_id, stage=name, state=state,
                             ms=rec["stages"][name]["ms"], **extra)

        def note(msg: str, level: str = "info") -> None:
            rec["notes"].append({"level": level, "message": msg, "ts_ms": now_ms()})
            self.bus.publish("log", job_id=job.job_id, level=level, message=msg)

        def fail(msg: str) -> dict:
            rec["status"] = "error"
            rec["error"] = msg
            rec["total_ms"] = round((time.monotonic() - t_job) * 1000.0, 2)
            note(msg, "error")
            return rec

        # -- stage 1: auction ---------------------------------------------
        stage("auction", "running")
        t0 = time.monotonic()
        outcome, drops = self.run_auction(job)
        award = outcome.award
        for d in drops:
            note(f"auction: {d}", "warn")
        if award is None:
            stage("auction", "error", (time.monotonic() - t0) * 1000.0)
            yield "record", fail(
                f"auction produced no eligible bids for model={model!r} "
                f"(received={outcome.n_received}, rejected={outcome.reason_counts()}, "
                f"max_price={max_price}, max_latency_ms={max_latency_ms}, "
                f"nodes={len(self.nodes)}, ollama_ok={self.ollama_ok})")
            return
        rec["bids"] = [
            sb.bid.model_dump(mode="json") |
            {"effective_price": round(sb.effective, 6), "eligible": True, "reason": None,
             "winner": sb.bid.bidder_peer_id == award.winner_peer_id}
            for sb in outcome.ranked
        ] + [
            sb.bid.model_dump(mode="json") |
            {"effective_price": None, "eligible": False, "reason": sb.reason,
             "winner": False}
            for sb in outcome.rejected
        ]
        rec["award"] = award.model_dump(mode="json")
        rec["auction"] = {"received": outcome.n_received, "eligible": outcome.n_eligible,
                          "rejected": outcome.reason_counts()}
        stage("auction", "ok", (time.monotonic() - t0) * 1000.0,
              n_bids=award.n_bids, clearing_price=award.clearing_price,
              winner=award.winner_peer_id)
        self.bus.publish("award", job_id=job.job_id, award=rec["award"])

        winner = self._node(award.winner_peer_id)
        host = next((n for n in self.nodes if n.profile.executes), None)
        if host is None:
            yield "record", fail("no node on this grid owns an inference runtime")
            return
        rec["execution"]["executed_by_peer_id"] = host.peer_id
        rec["execution"]["attributed_to_winner"] = winner.peer_id != host.peer_id
        if rec["execution"]["attributed_to_winner"]:
            note(f"winner {winner.profile.label} ({winner.peer_id[-12:]}) has no runtime on "
                 f"this host; tokens are produced by this machine's ollama and attributed "
                 f"to the winner. local mode only.", "warn")

        # -- stage 2: inference -------------------------------------------
        stage("inference", "running", winner=winner.peer_id)
        self.bus.publish("inference.start", job_id=job.job_id, peer_id=winner.peer_id,
                         model=model)
        wstats = self._ns(winner.peer_id)
        wstats["inflight"] += 1
        t0 = time.monotonic()
        result: Optional[InferenceResult] = None
        meta: dict = {}
        try:
            async for kind, data in self._stream_inference(job, messages, winner, temperature):
                if kind == "delta":
                    yield "delta", data
                else:
                    result, meta = data["result"], data
        except Exception as exc:
            stage("inference", "error", (time.monotonic() - t0) * 1000.0)
            wstats["inflight"] -= 1
            yield "record", fail(f"inference failed: {type(exc).__name__}: {exc}")
            return
        wstats["inflight"] -= 1
        assert result is not None
        wstats["jobs_served"] += 1
        wstats["last_ttft_ms"] = result.ttft_ms
        rec["result"] = result.model_dump(mode="json")
        rec["usage"] = {"prompt_tokens": meta.get("prompt_tokens", 0),
                        "completion_tokens": result.tokens_generated,
                        "total_tokens": meta.get("prompt_tokens", 0) + result.tokens_generated}
        rec["execution"]["load_duration_ms"] = meta.get("load_duration_ms", 0.0)
        rec["execution"]["token_counts_reported"] = meta["token_counts_reported"]
        rec["execution"]["prompt_tokens_reported"] = meta["prompt_tokens_reported"]
        rec["execution"]["throughput_measured"] = meta["throughput_measured"]
        rec["execution"]["eval_duration_ns"] = meta["eval_duration_ns"]
        if not meta["token_counts_reported"]:
            note("runtime did not report eval_count; tokens_generated recorded as 0 rather "
                 "than estimated from a word count", "warn")
        if not meta["prompt_tokens_reported"]:
            note("runtime did not report prompt_eval_count; usage.prompt_tokens recorded "
                 "as 0 rather than estimated", "warn")
        if not meta["throughput_measured"]:
            note(f"runtime did not time the generation usefully "
                 f"(eval_duration={meta['eval_duration_ns']} ns, under the "
                 f"{MIN_EVAL_DURATION_NS} ns floor); tokens_per_sec recorded as 0 rather "
                 f"than divided out of a clock artefact. ttft_ms is unaffected - it is "
                 f"measured here, not read back from the runtime", "warn")
        stage("inference", "ok", (time.monotonic() - t0) * 1000.0,
              ttft_ms=result.ttft_ms, tokens=result.tokens_generated,
              tokens_per_sec=result.tokens_per_sec)
        self.bus.publish("inference.done", job_id=job.job_id, result=rec["result"])

        # -- stage 3: commit ----------------------------------------------
        stage("commit", "running")
        t0 = time.monotonic()
        try:
            commitment, meta_ref = self.commit(job, result, winner)
        except Exception as exc:
            stage("commit", "error", (time.monotonic() - t0) * 1000.0)
            yield "record", fail(f"DA commitment failed: {type(exc).__name__}: {exc}")
            return
        proof = self.da.inclusion_proof(commitment.blob_ref)
        rec["commitment"] = commitment.model_dump(mode="json")
        rec["da"] = {"height": commitment.blob_height,
                     "root": proof[1] if proof else None,
                     "proof_len": len(proof[0]) if proof else 0,
                     "proof": proof[0] if proof else [],
                     "meta_ref": meta_ref,
                     "namespace": commitment.namespace}
        stage("commit", "ok", (time.monotonic() - t0) * 1000.0,
              blob_ref=commitment.blob_ref, height=commitment.blob_height)
        self.bus.publish("commit", job_id=job.job_id, commitment=rec["commitment"],
                         da=rec["da"])

        # -- stage 4: verify ----------------------------------------------
        sampled = force_verify or (random.random() < self.sample_rate)
        rec["sampled"] = sampled
        verdict: Optional[Verdict] = None
        if not sampled:
            stage("verify", "skipped", 0.0, reason=f"not sampled (rate={self.sample_rate})")
        else:
            stage("verify", "running", backend=C.JUDGE_BACKEND)
            t0 = time.monotonic()
            verdict = await self.verify(job, result, commitment, question)
            rec["verdict"] = verdict.model_dump(mode="json")
            stage("verify", "ok" if verdict.verdict != VerdictKind.ERROR else "error",
                  (time.monotonic() - t0) * 1000.0, verdict=verdict.verdict.value,
                  score=verdict.quality_score, backend=verdict.judge_backend)
            if verdict.verdict == VerdictKind.ERROR:
                note(f"verification ERROR (not a pass, not a fail): {verdict.reason}", "error")
            self.bus.publish("verdict", job_id=job.job_id, verdict=rec["verdict"])

        # -- stage 5: settle ----------------------------------------------
        stage("settle", "running")
        t0 = time.monotonic()
        settlement = self.settle(job, award, verdict, winner)
        rec["settlement"] = settlement.model_dump(mode="json")
        self.settlements.append(rec["settlement"] | {"label": winner.profile.label,
                                                     "audit": False, "reversed": False,
                                                     "reversed_ms": None})
        stage("settle", "ok", (time.monotonic() - t0) * 1000.0,
              escrow_state=settlement.state.value, payout=settlement.provider_payout,
              slashed=settlement.slash_amount)
        self.bus.publish("settlement", job_id=job.job_id, settlement=rec["settlement"])

        rec["status"] = "complete"
        rec["total_ms"] = round((time.monotonic() - t_job) * 1000.0, 2)
        yield "record", rec

    # -- operator actions ------------------------------------------------

    async def reverify(self, job_id: str) -> dict:
        """Audit an already-settled job on demand: re-run the judge and re-settle.

        The previous settlement's value movement is reversed before the new one is
        applied, so an audit cannot double-count a payout or a slash."""
        rec = self.jobs.get(job_id)
        if rec is None:
            raise GridError(f"unknown job {job_id}")
        if rec["status"] != "complete" or not rec.get("result"):
            raise GridError(f"job {job_id} has no completed inference to audit")
        job = JobRequest.model_validate(rec["request"])
        result = InferenceResult.model_validate(rec["result"])
        commitment = Commitment.model_validate(rec["commitment"])
        award = JobAward.model_validate(rec["award"])
        winner = self._node(award.winner_peer_id)

        if rec.get("settlement"):
            self._apply(SettlementRecord.model_validate(rec["settlement"]), -1)
            # Reversing the value movement is not enough on its own: the ledger row
            # for that settlement is still sitting in `self.settlements` showing a
            # payout that no longer exists, so anyone summing the ledger - the
            # dashboard included - double-counts it. Mark it as reversed here.
            self._mark_reversed(job_id)
            self.bus.publish("log", job_id=job_id, level="warn",
                             message="re-audit: reversing the previous settlement before "
                                     "re-settling; the superseded ledger row is marked "
                                     "reversed and no longer counts as value moved")
        verdict = await self.verify(job, result, commitment, rec["question"])
        rec["verdict"] = verdict.model_dump(mode="json")
        rec["sampled"] = True
        rec["forced_verify"] = True
        rec["stages"]["verify"] = {"state": "ok" if verdict.verdict != VerdictKind.ERROR
                                   else "error", "ms": round(verdict.latency_ms, 2)}
        self.bus.publish("verdict", job_id=job_id, verdict=rec["verdict"])
        settlement = self.settle(job, award, verdict, winner)
        rec["settlement"] = settlement.model_dump(mode="json")
        self.settlements.append(rec["settlement"] | {"label": winner.profile.label,
                                                     "audit": True, "reversed": False,
                                                     "reversed_ms": None})
        self.bus.publish("settlement", job_id=job_id, settlement=rec["settlement"])
        self.bus.publish("job.stage", job_id=job_id, stage="settle", state="ok",
                         ms=None, escrow_state=settlement.state.value)
        return rec

    def _mark_reversed(self, job_id: str) -> None:
        """Mark this job's live ledger rows as reversed. Rows are never deleted -
        the audit trail is the point - they stop counting as value moved."""
        for row in self.settlements:
            if row["job_id"] == job_id and not row.get("reversed"):
                row["reversed"] = True
                row["reversed_ms"] = now_ms()

    def ledger_totals(self) -> dict:
        """Value actually moved, over rows that have not been reversed by an audit."""
        live = [s for s in self.settlements if not s.get("reversed")]
        return {
            "rows": len(self.settlements),
            "rows_live": len(live),
            "rows_reversed": len(self.settlements) - len(live),
            "paid": round(sum(s["provider_payout"] for s in live), 6),
            "slashed": round(sum(s["slash_amount"] for s in live), 6),
            "escrowed": round(sum(s["amount"] for s in live), 6),
        }

    # -- storage / projections -------------------------------------------

    def _store(self, rec: dict) -> None:
        self.jobs[rec["job_id"]] = rec
        self.job_order.append(rec["job_id"])
        while len(self.job_order) > self.max_jobs:
            self.jobs.pop(self.job_order.pop(0), None)

    def job_list(self, limit: int = 50) -> list[dict]:
        ids = self.job_order[-limit:][::-1]
        return [self.jobs[i] for i in ids if i in self.jobs]

    def stats(self) -> dict:
        jobs = [self.jobs[i] for i in self.job_order if i in self.jobs]
        done = [j for j in jobs if j["status"] == "complete"]
        errored = [j for j in jobs if j["status"] == "error"]
        ttfts = [j["result"]["ttft_ms"] for j in done if j.get("result")]
        # Only jobs whose runtime actually timed the generation contribute to the
        # throughput mean; the rest are counted so the gap is visible rather than
        # silently narrowing the sample. See MIN_EVAL_DURATION_NS.
        tps = [j["result"]["tokens_per_sec"] for j in done
               if j.get("result") and (j.get("execution") or {}).get("throughput_measured")]
        tps_unmeasured = sum(
            1 for j in done
            if j.get("result") and not (j.get("execution") or {}).get("throughput_measured"))
        tokens = sum(j["result"]["tokens_generated"] for j in done if j.get("result"))
        verdicts = [j["verdict"]["verdict"] for j in done if j.get("verdict")]
        escrowed = sum(j["settlement"]["amount"] for j in done if j.get("settlement"))
        paid = sum(j["settlement"]["provider_payout"] for j in done if j.get("settlement"))
        slashed = sum(j["settlement"]["slash_amount"] for j in done if j.get("settlement"))
        grid_usd = paid * C.GRID_USD
        centralized_usd = (tokens / 1000.0) * C.CENTRALIZED_USD_PER_1K_TOKENS
        return {
            "mode": self.mode,
            "uptime_ms": now_ms() - self._started_ms,
            "nodes": len(self.nodes),
            "healthy_nodes": sum(1 for n in self.nodes if n.healthy),
            "jobs_total": len(jobs),
            "jobs_complete": len(done),
            "jobs_error": len(errored),
            "tokens_generated": tokens,
            "ttft_ms_mean": round(sum(ttfts) / len(ttfts), 1) if ttfts else None,
            "ttft_ms_min": round(min(ttfts), 1) if ttfts else None,
            "ttft_ms_max": round(max(ttfts), 1) if ttfts else None,
            "tokens_per_sec_mean": round(sum(tps) / len(tps), 2) if tps else None,
            "tokens_per_sec_n": len(tps),
            "tokens_per_sec_unmeasured": tps_unmeasured,
            "sample_rate": self.sample_rate,
            "jobs_sampled": sum(1 for j in done if j["sampled"]),
            "verdict_pass": verdicts.count("pass"),
            "verdict_fail": verdicts.count("fail"),
            "verdict_error": verdicts.count("error"),
            # The configured judge is what the NEXT job would use. The verdicts
            # already on the board may have been produced by a different backend
            # (the config is env-overridable and can change between jobs), so the
            # census of what actually ruled is reported alongside it and never
            # inferred from the current setting.
            "judge_backend": C.JUDGE_BACKEND,
            "judge_model": C.JUDGE_MODEL,
            "judge_backends_used": _census(
                j["verdict"]["judge_backend"] for j in done if j.get("verdict")),
            "judge_models_used": _census(
                j["verdict"]["judge_model"] for j in done if j.get("verdict")),
            "grid_escrowed": round(escrowed, 6),
            "grid_paid": round(paid, 6),
            "grid_slashed": round(slashed, 6),
            "treasury": round(self.treasury, 6),
            "validator_earnings": round(self.validator_earnings, 6),
            "total_stake": round(sum(self.stakes.values()), 6),
            "total_earned": round(sum(self.earnings.values()), 6),
            "grid_usd": round(grid_usd, 6),
            "centralized_usd": round(centralized_usd, 6),
            "ledger": self.ledger_totals(),
            "da": self.da.stats(),
            "ollama_ok": self.ollama_ok,
            "ollama_error": self.ollama_error,
            "host_hardware_error": self.host_hardware_error,
            "events": self.bus.seq,
            "event_subscribers": self.bus.subscriber_count,
        }


# --------------------------------------------------------------------------
# message helpers
# --------------------------------------------------------------------------

def _census(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for v in values:
        counts[v] = counts.get(v, 0) + 1
    return counts


def _flatten(messages: list[dict]) -> str:
    """The transcript that is signed, committed to the DA layer, and hashed. The
    whole conversation is committed, not just the final turn, so a provider cannot
    later claim it was answering a different question."""
    return "\n".join(f"{m.get('role', 'user')}: {m.get('content', '')}" for m in messages)


def _last_user(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return str(m.get("content", ""))
    return messages[-1].get("content", "") if messages else ""


# --------------------------------------------------------------------------
# backend selection
# --------------------------------------------------------------------------

# Modules asked, in order, for `async def open_grid(bus) -> GridLike`. None of
# them exposes one today, which is why the gateway serves `local`.
TRANSPORT_MODULES = ("edgegrid.p2p", "edgegrid.swarm")


async def open_grid(bus: EventBus) -> tuple[Any, str, str]:
    """Return (grid, mode, reason).

    A live P2P grid is used only when a transport module exposes `open_grid(bus)`
    and it connects. Nothing else is ever labelled `p2p`. The reason string is
    surfaced verbatim by `/health` so an operator can see why local mode was chosen.

    Note that the auction itself is always `edgegrid.market`, in both modes. The
    thing that differs between p2p and local is the transport - who the bids come
    from - not the clearing rule.
    """
    attempts: list[str] = []
    for module_name in TRANSPORT_MODULES:
        try:
            mod = __import__(module_name, fromlist=["open_grid"])
        except ImportError as exc:
            attempts.append(f"{module_name}: not importable ({type(exc).__name__}: {exc})")
            continue
        except Exception as exc:
            # The module exists and blew up while importing. That is a real
            # transport failure and must be named as one - reporting only the last
            # module tried would hide it behind an irrelevant AttributeError.
            attempts.append(f"{module_name}: FAILED TO IMPORT ({type(exc).__name__}: {exc})")
            continue
        open_p2p = getattr(mod, "open_grid", None)
        if open_p2p is None:
            attempts.append(f"{module_name}: imported but exposes no open_grid(bus)")
            continue
        try:
            grid = await open_p2p(bus)
        except Exception as exc:
            attempts.append(f"{module_name}.open_grid(bus) FAILED TO CONNECT "
                            f"({type(exc).__name__}: {exc})")
            continue
        return grid, "p2p", f"connected to a live libp2p grid via {module_name}.open_grid"

    # Every attempt is reported, not just the last one. This string is the whole of
    # what an operator has to go on when the gateway says `local`, and a p2p module
    # that exists and crashed must not be masked by one that merely has no
    # open_grid.
    reason = ("no p2p transport opened a grid; serving from the in-process LocalGrid, "
              "which clears bids through the same edgegrid.market auction but over one "
              "process rather than a swarm. attempts: " + " | ".join(attempts))
    grid = LocalGrid(bus)
    await grid.start()
    bus.publish("log", level="warn", message=reason)
    return grid, grid.mode, reason
