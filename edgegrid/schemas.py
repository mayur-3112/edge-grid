"""Wire schemas for every hop in the Edge Grid pipeline.

This module is the single source of truth for message shapes. `shared/schemas.md`
is generated from it by `python -m edgegrid.schemas --emit-markdown` and is never
hand-edited.

Flow:
    JobRequest   --gossipsub-->  Bid              (discovery / market protocol)
    JobAward     --direct p2p->  InferenceResult  (inference engine)
    InferenceResult ---------->  Commitment       (DA blob + on-chain reference)
    Commitment   ------------->  Verdict          (agentic verification)
    Verdict      ------------->  SettlementRecord (settlement)
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------
# topics / protocol ids / dht keyspace
# --------------------------------------------------------------------------

TOPIC_JOBS = "edgegrid/jobs/v1"
TOPIC_BIDS = "edgegrid/bids/v1"
TOPIC_AWARDS = "edgegrid/awards/v1"
TOPIC_COMMITMENTS = "edgegrid/commitments/v1"
ALL_TOPICS = (TOPIC_JOBS, TOPIC_BIDS, TOPIC_AWARDS, TOPIC_COMMITMENTS)

PROTOCOL_INFERENCE = "/edgegrid/inference/1.0.0"
DHT_NODE_PREFIX = "/edgegrid/node/"


def new_id() -> str:
    return str(uuid.uuid4())


def now_ms() -> int:
    return int(time.time() * 1000)


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def to_bytes(self) -> bytes:
        return self.model_dump_json().encode("utf-8")

    @classmethod
    def from_bytes(cls, raw: bytes):
        return cls.model_validate_json(raw)

    def canonical(self) -> bytes:
        """Deterministic bytes for signing and hashing: sorted keys, no
        whitespace, `signature` excluded so a message can be signed in place."""
        payload = self.model_dump(mode="json", exclude={"signature"})
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


# --------------------------------------------------------------------------
# node identity and capability
# --------------------------------------------------------------------------

class HardwareTier(int, Enum):
    """Tier classification from the Phase-1 design (Literature Survey, Module 1)."""

    CPU = 1           # Tier 1 - CPU only
    LOW_GPU = 2       # Tier 2 - integrated / low-end GPU
    DISCRETE_GPU = 3  # Tier 3 - discrete GPU with >= 16 GB VRAM


class NodeRecord(_Base):
    """Static node metadata, stored in the Kademlia DHT at DHT_NODE_PREFIX + peer_id."""

    peer_id: str
    wallet_address: str = Field(description="0x address derived from the node's secp256k1 key")
    pubkey_hex: str
    multiaddrs: list[str] = Field(default_factory=list)
    tier: HardwareTier = HardwareTier.CPU
    models: list[str] = Field(default_factory=list, description="model ids this node serves")
    warm_models: list[str] = Field(default_factory=list, description="subset already resident")
    cpu_count: int = 0
    ram_gb: float = 0.0
    vram_gb: float = 0.0
    tokens_per_sec: float = 0.0
    stake: float = 0.0
    updated_ms: int = Field(default_factory=now_ms)
    signature: Optional[str] = None


class Heartbeat(_Base):
    """Dynamic node state, broadcast over UDP every HEARTBEAT_INTERVAL_S seconds.

    Deliberately decoupled from the DHT: the DHT holds slow-changing facts, the
    heartbeat holds fast-changing ones (Phase-1 design, Module 1)."""

    peer_id: str
    seq: int
    ram_available_gb: float = 0.0
    vram_available_gb: float = 0.0
    cpu_percent: float = 0.0
    load1: float = 0.0
    warm_models: list[str] = Field(default_factory=list)
    inflight: int = 0
    healthy: bool = True
    sent_ms: int = Field(default_factory=now_ms)
    signature: Optional[str] = None


# --------------------------------------------------------------------------
# market protocol
# --------------------------------------------------------------------------

class JobRequest(_Base):
    """Published to TOPIC_JOBS. Signed by the requester."""

    job_id: str = Field(default_factory=new_id)
    prompt: str
    model: str
    max_tokens: int = 256
    requester_peer_id: str
    requester_wallet: str = ""
    max_price: float = Field(default=1.0, description="price ceiling, in GRID")
    max_latency_ms: int = Field(default=30_000, description="TTFT budget the bid must meet")
    min_tier: HardwareTier = HardwareTier.CPU
    created_ms: int = Field(default_factory=now_ms)
    signature: Optional[str] = None


class Bid(_Base):
    """Published to TOPIC_BIDS in response to a JobRequest. Signed by the bidder.

    `price` is the bidder's true reserve; under a second-price rule the winner is
    paid the runner-up's price, so bidding truthfully is the dominant strategy."""

    job_id: str
    bidder_peer_id: str
    bidder_wallet: str = ""
    price: float
    estimated_ttft_ms: float
    warm: bool = Field(default=False, description="model already resident in memory")
    tier: HardwareTier = HardwareTier.CPU
    stake: float = 0.0
    created_ms: int = Field(default_factory=now_ms)
    signature: Optional[str] = None


class JobAward(_Base):
    """Published to TOPIC_AWARDS once the auction closes. Signed by the requester."""

    job_id: str
    winner_peer_id: str
    winner_wallet: str = ""
    clearing_price: float = Field(description="second price - what the winner is actually paid")
    winning_bid_price: float = Field(description="the winner's own bid, kept for analysis")
    n_bids: int
    auction_ms: float = Field(description="broadcast -> award wall-clock, in ms")
    created_ms: int = Field(default_factory=now_ms)
    signature: Optional[str] = None


# --------------------------------------------------------------------------
# inference
# --------------------------------------------------------------------------

class InferenceResult(_Base):
    """Returned over PROTOCOL_INFERENCE by the winning node. Signed by the provider."""

    job_id: str
    provider_peer_id: str
    output: str
    model: str
    tokens_generated: int = Field(description="real token count from the runtime, not a word count")
    ttft_ms: float = Field(description="time to FIRST token - requires a streaming runtime")
    total_ms: float
    tokens_per_sec: float = 0.0
    warm: bool = False
    output_hash: str = Field(description="sha256 hex of `output`")
    signature: Optional[str] = None

    @classmethod
    def hash_output(cls, output: str) -> str:
        return sha256_hex(output)


# --------------------------------------------------------------------------
# data availability + commitment
# --------------------------------------------------------------------------

class Commitment(_Base):
    """The provider's binding claim about what it produced.

    `blob_ref` points into the DA layer; `output_hash` is what goes on chain. A
    verifier fetches the blob, recomputes the hash, and checks the Merkle proof
    before it ever calls a judge."""

    job_id: str
    provider_peer_id: str
    output_hash: str
    namespace: str
    blob_ref: str = Field(description="DA blob id")
    blob_height: int = Field(default=0, description="DA block height holding the blob")
    prompt_hash: str = ""
    created_ms: int = Field(default_factory=now_ms)
    tx_hash: str = Field(default="", description="on-chain tx that recorded this commitment")
    signature: Optional[str] = None


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------

class VerdictKind(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"  # judge unreachable / unparseable - NEVER silently a pass or fail


class Verdict(_Base):
    """A validator agent's ruling on one sampled job.

    ERROR is a first-class outcome. A judge outage must never be recorded as
    fraud detection, and an unparseable response must never become a pass."""

    job_id: str
    validator_peer_id: str = ""
    verdict: VerdictKind
    quality_score: Optional[int] = Field(default=None, ge=1, le=5)
    judge_score: Optional[float] = None
    reason: str = ""
    judge_backend: str = Field(description="groq | ollama | mock - recorded, never inferred")
    judge_model: str = Field(description="the model actually used, read back from the client")
    blob_verified: bool = Field(default=False, description="DA blob fetched and hash matched")
    latency_ms: float = 0.0
    created_ms: int = Field(default_factory=now_ms)
    signature: Optional[str] = None


# --------------------------------------------------------------------------
# settlement
# --------------------------------------------------------------------------

class EscrowState(str, Enum):
    OPEN = "open"
    AWAITING_VERIFICATION = "awaiting_verification"
    SETTLED = "settled"
    SLASHED = "slashed"
    REFUNDED = "refunded"


class SettlementRecord(_Base):
    """The final accounting row for one job. Value must conserve: every GRID that
    leaves the requester lands in exactly one of provider / validator / treasury."""

    job_id: str
    provider_peer_id: str
    requester_peer_id: str = ""
    amount: float = Field(description="escrowed amount = clearing price")
    state: EscrowState
    slashed: bool = False
    slash_amount: float = 0.0
    validator_reward: float = Field(default=0.0, description="80% of the slash")
    treasury_amount: float = Field(default=0.0, description="20% of the slash")
    provider_payout: float = 0.0
    requester_refund: float = 0.0
    fully_covered: bool = True
    remaining_stake: float = 0.0
    challenge_deadline_ms: int = 0
    tx_hash: str = ""
    gas_used: int = 0
    created_ms: int = Field(default_factory=now_ms)


# --------------------------------------------------------------------------
# markdown emitter - keeps shared/schemas.md honest
# --------------------------------------------------------------------------

_DOC_ORDER = [
    ("Node record (DHT value)", NodeRecord),
    ("Heartbeat (UDP)", Heartbeat),
    ("Job request (GossipSub: %s)" % TOPIC_JOBS, JobRequest),
    ("Bid (GossipSub: %s)" % TOPIC_BIDS, Bid),
    ("Job award (GossipSub: %s)" % TOPIC_AWARDS, JobAward),
    ("Inference result (direct stream: %s)" % PROTOCOL_INFERENCE, InferenceResult),
    ("Commitment (GossipSub: %s)" % TOPIC_COMMITMENTS, Commitment),
    ("Verdict (validator -> ledger)", Verdict),
    ("Settlement record (ledger / chain)", SettlementRecord),
]


def emit_markdown() -> str:
    lines = [
        "# Shared message schemas",
        "",
        "<!-- GENERATED by `python -m edgegrid.schemas --emit-markdown`. Do not edit by hand. -->",
        "",
        "Every field below is enforced at runtime by `edgegrid/schemas.py` "
        "(pydantic, `extra=\"forbid\"`). A track that sends an unknown field gets "
        "a validation error at the boundary rather than silent drift.",
        "",
    ]
    for title, model in _DOC_ORDER:
        lines += [f"## {title}", ""]
        if model.__doc__:
            lines += [" ".join(model.__doc__.split()), ""]
        lines += ["| field | type | default | notes |", "|---|---|---|---|"]
        for name, f in model.model_fields.items():
            ann = f.annotation
            tname = getattr(ann, "__name__", str(ann).replace("typing.", ""))
            if f.is_required():
                default = "**required**"
            else:
                d = f.default
                default = "`%s`" % (d.value if isinstance(d, Enum) else d)
                if f.default_factory is not None:
                    default = "_generated_"
            lines.append(f"| `{name}` | `{tname}` | {default} | {f.description or ''} |")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    if "--emit-markdown" in sys.argv:
        print(emit_markdown())
    else:
        print(f"edgegrid.schemas: {len(_DOC_ORDER)} message types")
        for title, model in _DOC_ORDER:
            print(f"  {model.__name__:20s} {len(model.model_fields):2d} fields  {title}")
