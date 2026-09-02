"""Data-availability layer (Phase-1 Objective 5 / Module 4).

SCOPE NOTE - read this before citing it in the report.
The full design posts output commitments to Celestia as namespaced blobs. This
module implements the same *interface and security property* against a local
append-only store: namespaced blobs, batched into blocks, each block committing
to its blobs through a binary Merkle tree, and an inclusion proof any verifier
can check without trusting the store.

What that buys us is real: a provider cannot show a verifier one output and the
chain another, because the on-chain reference pins a Merkle root and the proof
is checkable. What it does not buy us is Celestia's actual guarantee - that the
data is *available* to anyone, enforced by a decentralised validator set with
data-availability sampling. Swapping this for a Celestia light node means
reimplementing `submit_blob` and `get_blob` and nothing else.
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from edgegrid.config import DA_DIR

NAMESPACE_INFERENCE = "edgegrid.inference.v1"

_LEAF = b"\x00"
_NODE = b"\x01"


def _leaf_hash(data: bytes) -> bytes:
    return hashlib.sha256(_LEAF + data).digest()


def _node_hash(a: bytes, b: bytes) -> bytes:
    return hashlib.sha256(_NODE + a + b).digest()


def merkle_root(leaves: list[bytes]) -> bytes:
    """Binary Merkle root. Domain-separated leaves and nodes, so a leaf can
    never be reinterpreted as an internal node (second-preimage safety)."""
    if not leaves:
        return hashlib.sha256(b"").digest()
    level = [_leaf_hash(x) for x in leaves]
    while len(level) > 1:
        nxt = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else left  # duplicate odd tail
            nxt.append(_node_hash(left, right))
        level = nxt
    return level[0]


def merkle_proof(leaves: list[bytes], index: int) -> list[tuple[str, str]]:
    """Sibling path for `leaves[index]`, as [(side, hex), ...] with side in {L,R}."""
    if not 0 <= index < len(leaves):
        raise IndexError(f"leaf index {index} out of range for {len(leaves)} leaves")
    level = [_leaf_hash(x) for x in leaves]
    path: list[tuple[str, str]] = []
    idx = index
    while len(level) > 1:
        sib = idx ^ 1
        if sib >= len(level):
            sib = idx  # duplicated odd tail
        side = "R" if sib > idx else "L"
        path.append((side, level[sib].hex()))
        nxt = []
        for i in range(0, len(level), 2):
            left = level[i]
            right = level[i + 1] if i + 1 < len(level) else left
            nxt.append(_node_hash(left, right))
        level = nxt
        idx //= 2
    return path


def verify_proof(data: bytes, proof: list[tuple[str, str]], root_hex: str) -> bool:
    """Check that `data` is committed under `root_hex`. Pure function - a verifier
    needs no access to the store to run it."""
    try:
        h = _leaf_hash(data)
        for side, sib_hex in proof:
            sib = bytes.fromhex(sib_hex)
            h = _node_hash(sib, h) if side == "L" else _node_hash(h, sib)
        return h.hex() == root_hex
    except Exception:
        return False


@dataclass
class Blob:
    blob_id: str
    namespace: str
    height: int
    index: int
    data: bytes
    commitment: str

    def to_meta(self) -> dict:
        return {"blob_id": self.blob_id, "namespace": self.namespace,
                "height": self.height, "index": self.index,
                "commitment": self.commitment, "size": len(self.data)}


@dataclass
class Block:
    height: int
    root: str
    blob_ids: list[str] = field(default_factory=list)


class DALayer:
    """Append-only namespaced blob store with Merkle-committed blocks.

    Blobs accumulate in a pending batch; `seal_block()` commits the batch and
    fixes every inclusion proof in it. `submit_blob(..., seal=True)` (the
    default) seals immediately, which is what the single-node demo wants.
    """

    def __init__(self, root_dir: Optional[Path] = None):
        self.dir = Path(root_dir) if root_dir else DA_DIR
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "blobs").mkdir(exist_ok=True)
        self._lock = threading.Lock()
        self._pending: list[Blob] = []
        self._blocks: dict[int, Block] = {}
        self._height = 0
        self._load()

    # -- persistence -----------------------------------------------------

    @property
    def _chain_file(self) -> Path:
        return self.dir / "blocks.jsonl"

    def _load(self) -> None:
        if not self._chain_file.exists():
            return
        for line in self._chain_file.read_text().splitlines():
            if not line.strip():
                continue
            d = json.loads(line)
            self._blocks[d["height"]] = Block(d["height"], d["root"], d["blob_ids"])
            self._height = max(self._height, d["height"])

    def _blob_path(self, blob_id: str) -> Path:
        return self.dir / "blobs" / f"{blob_id}.json"

    # -- api -------------------------------------------------------------

    def submit_blob(self, data: bytes | str, namespace: str = NAMESPACE_INFERENCE,
                    seal: bool = True) -> Blob:
        if isinstance(data, str):
            data = data.encode("utf-8")
        with self._lock:
            commitment = hashlib.sha256(data).hexdigest()
            blob_id = hashlib.sha256(
                namespace.encode() + commitment.encode()
                + str(len(self._pending)).encode()
                + str(self._height + 1).encode()
            ).hexdigest()[:32]
            blob = Blob(blob_id, namespace, self._height + 1, len(self._pending),
                        data, commitment)
            self._pending.append(blob)
            self._blob_path(blob_id).write_text(json.dumps(
                {**blob.to_meta(), "data": data.decode("utf-8", "replace")}))
        if seal:
            self.seal_block()
            # re-read so the caller gets the sealed height
            return self.get_blob_meta(blob_id) or blob
        return blob

    def seal_block(self) -> Optional[Block]:
        with self._lock:
            if not self._pending:
                return None
            self._height += 1
            leaves = [b.data for b in self._pending]
            root = merkle_root(leaves).hex()
            blk = Block(self._height, root, [b.blob_id for b in self._pending])
            self._blocks[self._height] = blk
            for i, b in enumerate(self._pending):
                b.height, b.index = self._height, i
                p = self._blob_path(b.blob_id)
                d = json.loads(p.read_text())
                d.update({"height": self._height, "index": i})
                p.write_text(json.dumps(d))
            with self._chain_file.open("a") as f:
                f.write(json.dumps({"height": blk.height, "root": blk.root,
                                    "blob_ids": blk.blob_ids}) + "\n")
            self._pending = []
            return blk

    def get_blob(self, blob_id: str) -> Optional[bytes]:
        p = self._blob_path(blob_id)
        if not p.exists():
            return None
        return json.loads(p.read_text())["data"].encode("utf-8")

    def get_blob_meta(self, blob_id: str) -> Optional[Blob]:
        p = self._blob_path(blob_id)
        if not p.exists():
            return None
        d = json.loads(p.read_text())
        return Blob(d["blob_id"], d["namespace"], d["height"], d["index"],
                    d["data"].encode("utf-8"), d["commitment"])

    def block_root(self, height: int) -> Optional[str]:
        blk = self._blocks.get(height)
        return blk.root if blk else None

    def inclusion_proof(self, blob_id: str) -> Optional[tuple[list[tuple[str, str]], str]]:
        """Return (proof, root_hex) proving `blob_id` is in its block."""
        meta = self.get_blob_meta(blob_id)
        if not meta:
            return None
        blk = self._blocks.get(meta.height)
        if not blk:
            return None
        leaves = []
        for bid in blk.blob_ids:
            b = self.get_blob(bid)
            if b is None:
                return None
            leaves.append(b)
        return merkle_proof(leaves, meta.index), blk.root

    def verify_blob(self, blob_id: str, expected_hash: str) -> bool:
        """Full check a verifier runs: blob exists, its sha256 matches what the
        provider committed, and its Merkle proof lands on the block root."""
        data = self.get_blob(blob_id)
        if data is None:
            return False
        if hashlib.sha256(data).hexdigest() != expected_hash:
            return False
        got = self.inclusion_proof(blob_id)
        if not got:
            return False
        proof, root = got
        return verify_proof(data, proof, root)

    @property
    def height(self) -> int:
        return self._height

    def stats(self) -> dict:
        return {"height": self._height, "blocks": len(self._blocks),
                "blobs": sum(len(b.blob_ids) for b in self._blocks.values()),
                "pending": len(self._pending), "dir": str(self.dir)}
