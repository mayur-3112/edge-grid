"""Content-addressed model weight distribution (Objective 3 / Module 3).

Objective 3 promises an edge client that can *manage model weights*. Until now
the only part of that promise in the repository was `ModelRegistry.sol`, which
binds a model id to a content hash on chain; nothing fetched the bytes that hash
refers to, and nothing checked them. This module is the missing half.

Three things live here, in the order a fetch uses them:

  * **`IPFSWeightStore`** - a client for a real kubo daemon's HTTP API
    (`deploy/ipfs/docker-compose.yml` brings one up). Add, cat, pin, stat.
  * **`LocalWeightCache`** - the LRU cache with a byte budget that Module 3
    specifies. A hit never touches the network; an insert that overruns the
    budget evicts least-recently-used entries until it fits.
  * **`WeightResolver`** - model id -> CID (from `ModelRegistry` when a
    deployment is reachable, otherwise from a local manifest) -> verified local
    path.

The property that makes this different from an HTTP download with extra steps is
that **the CID is recomputed from the received bytes before they are returned**.
This module contains its own UnixFS/dag-pb implementation for exactly that
reason: asking the daemon what the hash is and believing the answer would verify
nothing, because the daemon is the party a provider would have to compromise to
serve tampered weights. `cid_for_file()` reproduces kubo's default file layout
(size-262144 chunker, sha2-256, balanced DAG, 174 links per node, CIDv0 with
UnixFS leaves) from first principles, and `IPFSWeightStore.add()` cross-checks
its own answer against the daemon's on every publish, so a layout this code
cannot reproduce fails loudly at publish time rather than silently at fetch time.

What is deliberately not implemented, and raises `UnsupportedDAG` rather than
degrading: HAMT-sharded directories, non-default chunkers, hash functions other
than sha2-256, and trickle DAGs. Each of those changes the CID for the same
bytes, so guessing would mean returning weights this module cannot vouch for.

Everything degraded is named. `available()` is a non-raising probe; every other
entry point raises a named exception rather than falling back to an unverified
source.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import io
import json
import os
import shutil
import tarfile
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional

import httpx

from edgegrid import config as C

__all__ = [
    "WeightError", "IPFSUnavailable", "IPFSRequestError", "CIDMismatch",
    "ContentHashMismatch", "ModelNotResolvable", "UnsupportedDAG", "CacheTooSmall",
    "IPFSWeightStore", "LocalWeightCache", "WeightResolver",
    "AddResult", "CacheEntry", "FetchResult", "ResolvedWeights",
    "cid_for_bytes", "cid_for_file", "cid_for_path", "sha256_file",
    "layouts_for_cid", "verify_cid",
    "CHUNK_SIZE", "MAX_LINKS",
]

# --------------------------------------------------------------------------
# tunables
#
# These belong in `edgegrid.config` with everything else, and are read the same
# way (env with a documented default) so a run can be reproduced. They live here
# because `config.py` is owned by another track and appending to it would be an
# edit outside this one; moving these six lines across is the only change needed
# to consolidate them.
# --------------------------------------------------------------------------

IPFS_API_URL = os.getenv("IPFS_API_URL", "http://127.0.0.1:5001")
IPFS_GATEWAY_URL = os.getenv("IPFS_GATEWAY_URL", "http://127.0.0.1:8080")
IPFS_TIMEOUT_S = float(os.getenv("IPFS_TIMEOUT_S", "120"))
WEIGHTS_CACHE_DIR = Path(os.getenv("WEIGHTS_CACHE_DIR", str(C.REPO_ROOT / ".weights")))
WEIGHTS_CACHE_BUDGET_BYTES = int(os.getenv("WEIGHTS_CACHE_BUDGET_BYTES", str(2 * 1024**3)))
WEIGHTS_MANIFEST = Path(os.getenv("WEIGHTS_MANIFEST", str(WEIGHTS_CACHE_DIR / "manifest.json")))


# --------------------------------------------------------------------------
# errors - one name per failure mode, so a caller can tell "the daemon is down"
# from "these bytes are not the bytes I asked for"
# --------------------------------------------------------------------------

class WeightError(RuntimeError):
    """Base for every failure of the weight distribution path."""


class IPFSUnavailable(WeightError):
    """No kubo daemon answered. Says how to start one."""


class IPFSRequestError(WeightError):
    """The daemon answered with an error."""


class CIDMismatch(WeightError):
    """Bytes were received whose CID is not the CID that was requested.

    This is the adversary the project exists to catch: a provider serving
    weights that are not the weights the model id is bound to. It is never
    recoverable and never downgraded to a warning.
    """


class ContentHashMismatch(WeightError):
    """The fetched weights do not match the digest `ModelRegistry` records."""


class ModelNotResolvable(WeightError):
    """No CID could be found for a model id, from the chain or a manifest."""


class UnsupportedDAG(WeightError):
    """A DAG shape whose CID this module cannot independently reproduce."""


class CacheTooSmall(WeightError):
    """One artefact is larger than the whole cache budget."""


# --------------------------------------------------------------------------
# CID computation
#
# kubo's default `ipfs add` for a file: split into 262144-byte chunks, wrap each
# chunk in a UnixFS `File` node, and build a balanced DAG with at most 174 links
# per node. The block hash is sha2-256 and the CID is v0 (the bare multihash,
# base58btc). Reproduced here from the dag-pb and UnixFS specifications.
# --------------------------------------------------------------------------

CHUNK_SIZE = 262_144           # kubo's `size-262144` default chunker
MAX_LINKS = 174                # go-unixfs DefaultLinksPerBlock
_SHARD_THRESHOLD = 256 * 1024  # kubo shards a directory whose block exceeds this

_CODEC_DAG_PB = 0x70
_CODEC_RAW = 0x55
_MH_SHA2_256 = 0x12

_UNIXFS_FILE = 2
_UNIXFS_DIRECTORY = 1

_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58encode(raw: bytes) -> str:
    """base58btc, as CIDv0 is printed. Implemented here rather than pulled from
    a dependency because a CID this module cannot render is a CID it cannot
    check, and that should not turn on a transitive package being installed."""
    n = int.from_bytes(raw, "big")
    out = ""
    while n > 0:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    for b in raw:                       # leading zero bytes are leading '1's
        if b != 0:
            break
        out = "1" + out
    return out


def _b32encode(raw: bytes) -> str:
    """base32 lower-case, no padding - the multibase 'b' alphabet."""
    return base64.b32encode(raw).decode("ascii").rstrip("=").lower()


def _varint(n: int) -> bytes:
    if n < 0:
        raise ValueError(f"varint cannot encode {n}")
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _pb_bytes(field_no: int, data: bytes) -> bytes:
    return bytes([(field_no << 3) | 2]) + _varint(len(data)) + data


def _pb_varint(field_no: int, value: int) -> bytes:
    return bytes([(field_no << 3) | 0]) + _varint(value)


def _multihash(digest: bytes) -> bytes:
    return bytes([_MH_SHA2_256, len(digest)]) + digest


def _unixfs_file(data: Optional[bytes], filesize: int, blocksizes: Iterable[int]) -> bytes:
    """UnixFS `Data` message for a file node.

    Field order and presence follow go-unixfs exactly: Type is always written,
    Data only when non-empty, filesize always, blocksizes unpacked (the .proto
    is proto2). A single byte out of place changes the CID.
    """
    out = _pb_varint(1, _UNIXFS_FILE)
    if data:
        out += _pb_bytes(2, data)
    out += _pb_varint(3, filesize)
    for bs in blocksizes:
        out += _pb_varint(4, bs)
    return out


def _unixfs_directory() -> bytes:
    return _pb_varint(1, _UNIXFS_DIRECTORY)


def _pb_node(links: list[bytes], data: Optional[bytes]) -> bytes:
    """dag-pb PBNode. Links (field 2) are serialised before Data (field 1);
    the reverse order is a different byte string and so a different CID."""
    out = b"".join(_pb_bytes(2, ln) for ln in links)
    if data is not None:
        out += _pb_bytes(1, data)
    return out


def _pb_link(cid_bytes: bytes, name: str, tsize: int) -> bytes:
    return (_pb_bytes(1, cid_bytes)
            + _pb_bytes(2, name.encode("utf-8"))
            + _pb_varint(3, tsize))


@dataclass(frozen=True)
class _Node:
    """One block of a UnixFS DAG, with what a parent needs to link to it."""
    cid_bytes: bytes    # binary CID, as it appears in a parent's PBLink.Hash
    cid: str            # printable CID
    block_len: int      # serialised size of this block alone
    filesize: int       # payload bytes this subtree covers
    cumulative: int     # block_len + every descendant's block_len (PBLink.Tsize)


def _cid(block: bytes, codec: int, version: int) -> tuple[bytes, str]:
    mh = _multihash(hashlib.sha256(block).digest())
    if version == 0:
        if codec != _CODEC_DAG_PB:
            raise UnsupportedDAG("CIDv0 exists only for dag-pb blocks")
        return mh, _b58encode(mh)
    raw = bytes([0x01, codec]) + mh
    return raw, "b" + _b32encode(raw)


def _leaf(chunk: bytes, cid_version: int, raw_leaves: bool) -> _Node:
    if raw_leaves:
        cid_bytes, cid = _cid(chunk, _CODEC_RAW, cid_version)
        return _Node(cid_bytes, cid, len(chunk), len(chunk), len(chunk))
    block = _pb_node([], _unixfs_file(chunk, len(chunk), ()))
    cid_bytes, cid = _cid(block, _CODEC_DAG_PB, cid_version)
    return _Node(cid_bytes, cid, len(block), len(chunk), len(block))


def _internal(children: list[_Node], cid_version: int) -> _Node:
    links = [_pb_link(c.cid_bytes, "", c.cumulative) for c in children]
    filesize = sum(c.filesize for c in children)
    block = _pb_node(links, _unixfs_file(None, filesize, [c.filesize for c in children]))
    cid_bytes, cid = _cid(block, _CODEC_DAG_PB, cid_version)
    cumulative = len(block) + sum(c.cumulative for c in children)
    return _Node(cid_bytes, cid, len(block), filesize, cumulative)


def _fill(leaves: list[_Node], i: int, depth: int, children: list[_Node],
          cid_version: int) -> tuple[_Node, int]:
    """One node of the balanced builder, filled to `MAX_LINKS` or exhaustion.

    Mirrors go-unixfs `importer/balanced.fillNodeRec`: at depth 1 the children
    are leaves, deeper the children are recursively filled subtrees.
    """
    while len(children) < MAX_LINKS and i < len(leaves):
        if depth == 1:
            child, i = leaves[i], i + 1
        else:
            child, i = _fill(leaves, i, depth - 1, [], cid_version)
        children.append(child)
    return _internal(children, cid_version), i


def _build_file_dag(chunks: Iterator[bytes], cid_version: int, raw_leaves: bool) -> _Node:
    leaves = [_leaf(c, cid_version, raw_leaves) for c in chunks]
    if not leaves:
        # An empty file still gets one leaf. With raw leaves that is the empty
        # raw block; otherwise it is a UnixFS File node with no data, for which
        # kubo prints QmbFMke1KXqnYyBBWxB74N4c5SBnJMVAiMNRcGu6x1AwQH.
        if raw_leaves:
            return _leaf(b"", cid_version, True)
        block = _pb_node([], _unixfs_file(None, 0, ()))
        cid_bytes, cid = _cid(block, _CODEC_DAG_PB, cid_version)
        return _Node(cid_bytes, cid, len(block), 0, len(block))
    root, i, depth = leaves[0], 1, 1
    while i < len(leaves):
        root, i = _fill(leaves, i, depth, [root], cid_version)
        depth += 1
    return root


def _chunk_bytes(data: bytes) -> Iterator[bytes]:
    for off in range(0, len(data), CHUNK_SIZE):
        yield data[off:off + CHUNK_SIZE]


def _chunk_file(path: Path) -> Iterator[bytes]:
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(CHUNK_SIZE)
            if not chunk:
                return
            yield chunk


def cid_for_bytes(data: bytes, cid_version: int = 0,
                  raw_leaves: Optional[bool] = None) -> str:
    """CID kubo's `ipfs add` would print for `data` held in a file."""
    raw = (cid_version == 1) if raw_leaves is None else raw_leaves
    return _build_file_dag(_chunk_bytes(data), cid_version, raw).cid


def cid_for_file(path: Path | str, cid_version: int = 0,
                 raw_leaves: Optional[bool] = None) -> str:
    """CID kubo's `ipfs add` would print for the file at `path`, computed here.

    Streams the file, so a weight file larger than memory is fine.
    """
    raw = (cid_version == 1) if raw_leaves is None else raw_leaves
    return _build_file_dag(_chunk_file(Path(path)), cid_version, raw).cid


def _dir_node(path: Path, cid_version: int, raw_leaves: bool) -> _Node:
    """Directory node for `ipfs add -r`. Links sorted by name, as kubo emits."""
    children: list[tuple[str, _Node]] = []
    for entry in sorted(path.iterdir(), key=lambda p: p.name):
        if entry.is_symlink():
            raise UnsupportedDAG(
                f"{entry} is a symlink; UnixFS symlink nodes are not implemented")
        if entry.is_dir():
            children.append((entry.name, _dir_node(entry, cid_version, raw_leaves)))
        elif entry.is_file():
            children.append((entry.name,
                             _build_file_dag(_chunk_file(entry), cid_version, raw_leaves)))
        else:
            raise UnsupportedDAG(f"{entry} is neither a regular file nor a directory")
    links = [_pb_link(n.cid_bytes, name, n.cumulative) for name, n in children]
    block = _pb_node(links, _unixfs_directory())
    if len(block) > _SHARD_THRESHOLD:
        raise UnsupportedDAG(
            f"{path} would be HAMT-sharded by kubo ({len(block)} bytes of links); "
            f"this module cannot reproduce sharded directory CIDs")
    cid_bytes, cid = _cid(block, _CODEC_DAG_PB, cid_version)
    return _Node(cid_bytes, cid, len(block), sum(n.filesize for _, n in children),
                 len(block) + sum(n.cumulative for _, n in children))


def cid_for_path(path: Path | str, cid_version: int = 0,
                 raw_leaves: Optional[bool] = None) -> str:
    """CID for a file or a directory tree, computed locally."""
    p = Path(path)
    raw = (cid_version == 1) if raw_leaves is None else raw_leaves
    if p.is_dir():
        return _dir_node(p, cid_version, raw).cid
    if p.is_file():
        return _build_file_dag(_chunk_file(p), cid_version, raw).cid
    raise FileNotFoundError(f"no file or directory at {p}")


def layouts_for_cid(cid: str) -> list[tuple[int, bool]]:
    """The `(cid_version, raw_leaves)` layouts the CID string itself can denote.

    Verification has to recompute under the layout the *CID* implies, never the
    layout the client happens to be configured for. Recomputing a CIDv0 under
    CIDv1 rules yields a different string for identical, honest bytes, and
    reporting that as a mismatch accuses an honest provider of serving tampered
    weights - the one error this module must not make.

    A CIDv1 dag-pb root is genuinely ambiguous: raw and UnixFS leaves produce
    the same root codec, and the root alone cannot say which was used. Both are
    tried. Two candidate strings out of 2**256 concedes nothing to an adversary,
    who still has to find bytes hashing to the exact CID that was asked for.
    """
    if cid.startswith("Qm") and len(cid) == 46:
        return [(0, False)]                  # CIDv0: dag-pb root, UnixFS leaves
    if cid.startswith("bafkrei"):
        return [(1, True)]                   # v1 raw codec: a lone raw leaf
    if cid.startswith("bafybei"):
        return [(1, True), (1, False)]       # v1 dag-pb root, leaves either way
    raise UnsupportedDAG(
        f"{cid!r} is not a CID shape this module can verify: only CIDv0 (Qm...) "
        f"and base32 CIDv1 with the raw or dag-pb codec (bafkrei.../bafybei...) "
        f"are reproducible here")


def verify_cid(path: Path | str, cid: str) -> tuple[bool, str]:
    """Recompute `cid` from the bytes at `path`. Returns `(ok, computed_cid)`.

    Every check of received or cached bytes goes through here rather than
    calling `cid_for_path` with a caller-chosen layout, so a configuration
    difference can never be mistaken for corruption.
    """
    computed = ""
    for version, raw in layouts_for_cid(cid):
        computed = cid_for_path(path, version, raw)
        if computed == cid:
            return True, computed
    return False, computed


def sha256_file(path: Path | str) -> str:
    """sha256 of a file's bytes - the digest `ModelRegistry.contentHash` holds.

    For a directory this is the digest of the deterministic concatenation of
    every relative path and its content hash, so the field still means "these
    exact bytes" for a multi-file artefact.
    """
    p = Path(path)
    h = hashlib.sha256()
    if p.is_dir():
        for f in sorted(x for x in p.rglob("*") if x.is_file()):
            h.update(str(f.relative_to(p)).encode("utf-8"))
            h.update(hashlib.sha256(f.read_bytes()).digest())
        return h.hexdigest()
    with p.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _dir_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


# --------------------------------------------------------------------------
# the store
# --------------------------------------------------------------------------

@dataclass
class AddResult:
    cid: str
    bytes: int
    name: str
    is_dir: bool
    pinned: bool
    daemon_cid: str          # what the daemon said, kept even when it agrees
    local_cid: str           # what this module computed
    add_ms: float


class IPFSWeightStore:
    """Client for a kubo daemon's HTTP API.

    Every parameter that affects the CID for a given set of bytes is sent
    explicitly on `add` - chunker, hash, cid-version, raw-leaves - so a daemon
    whose config differs from the defaults cannot silently produce a CID this
    module would then fail to reproduce.
    """

    def __init__(self, api_url: str = IPFS_API_URL, timeout_s: float = IPFS_TIMEOUT_S,
                 cid_version: int = 0, raw_leaves: Optional[bool] = None):
        self.api_url = api_url.rstrip("/")
        self.timeout_s = timeout_s
        self.cid_version = cid_version
        self.raw_leaves = (cid_version == 1) if raw_leaves is None else raw_leaves
        self._client = httpx.Client(base_url=f"{self.api_url}/api/v0", timeout=timeout_s)

    # -- plumbing --------------------------------------------------------

    def _post(self, path: str, **kw) -> httpx.Response:
        try:
            r = self._client.post(path, **kw)
        except httpx.HTTPError as e:
            raise IPFSUnavailable(
                f"no kubo daemon at {self.api_url} ({type(e).__name__}: {e}). Start one:\n"
                f"  make ipfs-up") from e
        if r.status_code >= 400:
            raise IPFSRequestError(
                f"POST {path} -> {r.status_code}: {r.text[:400].strip()}")
        return r

    def available(self) -> bool:
        """Non-raising probe. False means the daemon is unreachable or not a
        kubo API; the caller decides what to do, and every other method raises
        rather than pretending."""
        try:
            r = self._client.post("version", timeout=min(5.0, self.timeout_s))
        except httpx.HTTPError:
            return False
        return r.status_code == 200 and "Version" in r.text

    def version(self) -> dict:
        return self._post("version").json()

    def node_id(self) -> str:
        return self._post("id").json()["ID"]

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "IPFSWeightStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- add -------------------------------------------------------------

    def _add_params(self, pin: bool) -> dict:
        return {
            "pin": "true" if pin else "false",
            "cid-version": str(self.cid_version),
            "raw-leaves": "true" if self.raw_leaves else "false",
            "chunker": f"size-{CHUNK_SIZE}",
            "hash": "sha2-256",
            "wrap-with-directory": "false",
            "inline": "false",
            "trickle": "false",
            "progress": "false",
        }

    def add(self, path: Path | str, pin: bool = True) -> AddResult:
        """Publish a file or directory and return its CID.

        The CID is computed locally as well and the two are compared. They
        disagree only if the daemon used a layout this module does not
        reproduce, in which case nothing downstream could verify a fetch of it -
        so it raises here, at publish time, where it is cheap to notice.
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"nothing to publish at {p}")
        is_dir = p.is_dir()
        t0 = time.monotonic()
        if is_dir:
            files, closers = self._dir_multipart(p)
        else:
            fh = p.open("rb")
            closers = [fh]
            files = [("file", (p.name, fh, "application/octet-stream"))]
        try:
            r = self._post("add", params=self._add_params(pin), files=files)
        finally:
            for c in closers:
                c.close()
        add_ms = (time.monotonic() - t0) * 1000.0

        entries = [json.loads(line) for line in r.text.splitlines() if line.strip()]
        if not entries:
            raise IPFSRequestError(f"/add returned no entries for {p}")
        # kubo emits children first and the root last.
        root = entries[-1]
        daemon_cid = root["Hash"]
        local_cid = cid_for_path(p, self.cid_version, self.raw_leaves)
        if daemon_cid != local_cid:
            raise CIDMismatch(
                f"daemon computed {daemon_cid} for {p} but this module computed "
                f"{local_cid}. The daemon used a DAG layout that cannot be reproduced "
                f"locally, so a fetch of this CID could not be verified.")
        return AddResult(cid=daemon_cid, bytes=_dir_size(p), name=p.name, is_dir=is_dir,
                         pinned=pin, daemon_cid=daemon_cid, local_cid=local_cid,
                         add_ms=add_ms)

    @staticmethod
    def _dir_multipart(root: Path) -> tuple[list, list]:
        """Multipart parts for a recursive add: every directory as an
        `application/x-directory` part, every file under its relative path."""
        files: list = []
        closers: list = []
        files.append(("file", (root.name, io.BytesIO(b""), "application/x-directory")))
        for entry in sorted(root.rglob("*"), key=lambda x: str(x)):
            rel = f"{root.name}/{entry.relative_to(root).as_posix()}"
            if entry.is_dir():
                files.append(("file", (rel, io.BytesIO(b""), "application/x-directory")))
            elif entry.is_file():
                fh = entry.open("rb")
                closers.append(fh)
                files.append(("file", (rel, fh, "application/octet-stream")))
            else:
                raise UnsupportedDAG(f"{entry} is neither a regular file nor a directory")
        return files, closers

    # -- read ------------------------------------------------------------

    def cat(self, cid: str) -> bytes:
        """Raw bytes of a file CID. Unverified - `get()` is what callers use."""
        return self._post("cat", params={"arg": cid}).content

    def get(self, cid: str, dest: Path | str) -> Path:
        """Download `cid` to `dest` and **verify it by recomputing the CID**.

        Nothing is moved into place until the recomputed CID matches, so a
        caller can never be handed unverified bytes even transiently. The
        recomputation uses the layout `cid` itself implies (`verify_cid`), not
        this store's configured layout: a v1-configured client fetching a v0 CID
        gets honest bytes, and must not be told they were tampered with.
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = Path(tempfile.mkdtemp(prefix=".fetch-", dir=str(dest.parent)))
        try:
            staged = self._download(cid, tmp)
            ok, got = verify_cid(staged, cid)
            if not ok:
                raise CIDMismatch(
                    f"requested {cid} but the received bytes hash to {got}. "
                    f"The content served does not match the content requested; "
                    f"it is discarded rather than returned.")
            if dest.exists():
                shutil.rmtree(dest) if dest.is_dir() else dest.unlink()
            shutil.move(str(staged), str(dest))
            return dest
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _download(self, cid: str, tmp: Path) -> Path:
        """Fetch into `tmp` and return the staged path (file or directory).

        `/api/v0/cat` is used for files. For a directory CID kubo only offers
        `/api/v0/get`, which streams a tar; it is extracted and the directory
        DAG is rebuilt from the extracted tree to check the CID.
        """
        is_dir = self.stat(cid).get("Type") == "directory"
        if not is_dir:
            out = tmp / "blob"
            with self._client.stream("POST", "cat", params={"arg": cid}) as r:
                if r.status_code >= 400:
                    r.read()
                    raise IPFSRequestError(f"/cat {cid} -> {r.status_code}: {r.text[:400]}")
                with out.open("wb") as fh:
                    for block in r.iter_bytes(1 << 20):
                        fh.write(block)
            return out
        blob = self._post("get", params={"arg": cid, "archive": "true",
                                         "compress": "false"}).content
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r|") as tf:
            for member in tf:
                if member.name.startswith("/") or ".." in Path(member.name).parts:
                    raise IPFSRequestError(f"tar member escapes the destination: {member.name}")
                if member.issym() or member.islnk():
                    raise UnsupportedDAG(f"tar member {member.name} is a link")
                tf.extract(member, path=tmp, filter="data")
        extracted = [p for p in tmp.iterdir()]
        if len(extracted) != 1:
            raise IPFSRequestError(f"/get {cid} produced {len(extracted)} top-level entries")
        return extracted[0]

    # -- pinning and metadata -------------------------------------------

    def pin(self, cid: str) -> list[str]:
        return self._post("pin/add", params={"arg": cid, "recursive": "true"}).json()["Pins"]

    def unpin(self, cid: str) -> list[str]:
        return self._post("pin/rm", params={"arg": cid, "recursive": "true"}).json()["Pins"]

    def pins(self) -> set[str]:
        keys = self._post("pin/ls", params={"type": "recursive"}).json().get("Keys", {})
        return set(keys)

    def stat(self, cid: str) -> dict:
        """Size and shape of a CID, from the daemon's own index."""
        return self._post("files/stat", params={"arg": f"/ipfs/{cid}"}).json()


# --------------------------------------------------------------------------
# the cache
# --------------------------------------------------------------------------

@dataclass
class CacheEntry:
    cid: str
    path: str            # relative to the cache's blob directory
    bytes: int
    is_dir: bool
    added: float
    last_used: float
    hits: int
    model_id: str = ""

    def to_json(self) -> dict:
        return {"cid": self.cid, "path": self.path, "bytes": self.bytes,
                "is_dir": self.is_dir, "added": self.added,
                "last_used": self.last_used, "hits": self.hits,
                "model_id": self.model_id}

    @staticmethod
    def from_json(d: dict) -> "CacheEntry":
        return CacheEntry(d["cid"], d["path"], int(d["bytes"]), bool(d["is_dir"]),
                          float(d["added"]), float(d["last_used"]), int(d["hits"]),
                          d.get("model_id", ""))


@dataclass
class FetchResult:
    cid: str
    path: Path
    hit: bool
    bytes: int
    fetch_ms: float
    verified: bool
    evicted: list[str] = field(default_factory=list)


class LocalWeightCache:
    """LRU cache of verified artefacts, bounded by a byte budget.

    Module 3 asks for an LRU cache and this is one, with the properties that
    make the claim checkable rather than decorative:

      * a hit never re-downloads - it only stamps `last_used` and returns;
      * an insert that would overrun the budget evicts strictly least-recently-
        used entries until it fits, and names every eviction in the result;
      * an artefact larger than the whole budget raises `CacheTooSmall` instead
        of evicting everything and then failing anyway;
      * the index is mutated under an exclusive file lock, because two node
        processes sharing a cache directory would otherwise lose each other's
        accounting - the same reasoning as `edgegrid.chain.devnet_lock`.

    Counters are kept for the run (`stats()["session"]`) and cumulatively in the
    index (`stats()["lifetime"]`), so an experiment can report the hit rate it
    caused rather than the hit rate the directory has accumulated.
    """

    def __init__(self, store: IPFSWeightStore,
                 cache_dir: Path | str = WEIGHTS_CACHE_DIR,
                 budget_bytes: int = WEIGHTS_CACHE_BUDGET_BYTES):
        if budget_bytes <= 0:
            raise ValueError(f"budget_bytes must be positive, got {budget_bytes}")
        self.store = store
        self.dir = Path(cache_dir)
        self.blobs = self.dir / "blobs"
        self.blobs.mkdir(parents=True, exist_ok=True)
        self.budget_bytes = int(budget_bytes)
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.evictions = 0
        self.bytes_downloaded = 0
        self.bytes_evicted = 0

    # -- index -----------------------------------------------------------

    @property
    def index_file(self) -> Path:
        return self.dir / "index.json"

    def _read_index(self) -> tuple[dict[str, CacheEntry], dict[str, int]]:
        if not self.index_file.exists():
            return {}, {"hits": 0, "misses": 0, "evictions": 0,
                        "bytes_downloaded": 0, "bytes_evicted": 0}
        d = json.loads(self.index_file.read_text())
        entries = {c: CacheEntry.from_json(e) for c, e in d.get("entries", {}).items()}
        life = d.get("lifetime", {})
        for k in ("hits", "misses", "evictions", "bytes_downloaded", "bytes_evicted"):
            life.setdefault(k, 0)
        return entries, life

    def _write_index(self, entries: dict[str, CacheEntry], lifetime: dict[str, int]) -> None:
        payload = {"budget_bytes": self.budget_bytes,
                   "entries": {c: e.to_json() for c, e in entries.items()},
                   "lifetime": lifetime}
        tmp = self.index_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self.index_file)

    def _locked(self):
        """Exclusive lock over the index, held across a read-modify-write."""
        class _L:
            def __init__(self, path: Path):
                self.path = path

            def __enter__(self):
                self.fh = open(self.path, "w")
                fcntl.flock(self.fh, fcntl.LOCK_EX)
                return self

            def __exit__(self, *exc):
                fcntl.flock(self.fh, fcntl.LOCK_UN)
                self.fh.close()
        return _L(self.dir / ".index.lock")

    # -- queries ---------------------------------------------------------

    def entries(self) -> list[CacheEntry]:
        entries, _ = self._read_index()
        return sorted(entries.values(), key=lambda e: e.last_used, reverse=True)

    def total_bytes(self) -> int:
        entries, _ = self._read_index()
        return sum(e.bytes for e in entries.values())

    def path_for(self, cid: str) -> Path:
        return self.blobs / cid

    def contains(self, cid: str) -> bool:
        entries, _ = self._read_index()
        return cid in entries and self.path_for(cid).exists()

    def stats(self) -> dict:
        _, life = self._read_index()
        return {
            "session": {"hits": self.hits, "misses": self.misses,
                        "evictions": self.evictions,
                        "bytes_downloaded": self.bytes_downloaded,
                        "bytes_evicted": self.bytes_evicted},
            "lifetime": life,
            "budget_bytes": self.budget_bytes,
            "used_bytes": self.total_bytes(),
            "entries": len(self.entries()),
        }

    # -- fetch -----------------------------------------------------------

    def fetch(self, cid: str, model_id: str = "") -> FetchResult:
        """Return a local, CID-verified path for `cid`, downloading if needed."""
        with self._lock:
            t0 = time.monotonic()
            with self._locked():
                entries, life = self._read_index()
                entry = entries.get(cid)
                dest = self.path_for(cid)
                if entry is not None and dest.exists():
                    entry.last_used = time.time()
                    entry.hits += 1
                    if model_id:
                        entry.model_id = model_id
                    self.hits += 1
                    life["hits"] += 1
                    self._write_index(entries, life)
                    return FetchResult(cid=cid, path=dest, hit=True, bytes=entry.bytes,
                                       fetch_ms=(time.monotonic() - t0) * 1000.0,
                                       verified=True)
                if entry is not None and not dest.exists():
                    # The blob was removed underneath us. Drop the stale row
                    # rather than reporting a hit on a file that is not there.
                    del entries[cid]
                    self._write_index(entries, life)
            self.misses += 1

            # Downloaded outside the index lock: it is the slow part, and
            # holding a cross-process lock across it would serialise every node
            # on the machine behind one download.
            staged_dir = Path(tempfile.mkdtemp(prefix=".dl-", dir=str(self.blobs)))
            try:
                staged = self.store.get(cid, staged_dir / "artefact")
                size = _dir_size(staged)
                if size > self.budget_bytes:
                    raise CacheTooSmall(
                        f"{cid} is {size} bytes but the cache budget is "
                        f"{self.budget_bytes}; raise WEIGHTS_CACHE_BUDGET_BYTES")
                with self._locked():
                    entries, life = self._read_index()
                    evicted = self._evict_for(entries, life, size, keep=cid)
                    dest = self.path_for(cid)
                    if dest.exists():
                        shutil.rmtree(dest) if dest.is_dir() else dest.unlink()
                    shutil.move(str(staged), str(dest))
                    now = time.time()
                    entries[cid] = CacheEntry(cid=cid, path=cid, bytes=size,
                                              is_dir=dest.is_dir(), added=now,
                                              last_used=now, hits=0, model_id=model_id)
                    life["misses"] += 1
                    life["bytes_downloaded"] += size
                    self._write_index(entries, life)
                self.bytes_downloaded += size
                return FetchResult(cid=cid, path=dest, hit=False, bytes=size,
                                   fetch_ms=(time.monotonic() - t0) * 1000.0,
                                   verified=True, evicted=evicted)
            finally:
                shutil.rmtree(staged_dir, ignore_errors=True)

    def _evict_for(self, entries: dict[str, CacheEntry], life: dict[str, int],
                   incoming: int, keep: str) -> list[str]:
        """Evict least-recently-used entries until `incoming` bytes fit."""
        evicted: list[str] = []
        used = sum(e.bytes for c, e in entries.items() if c != keep)
        order = sorted((e for c, e in entries.items() if c != keep),
                       key=lambda e: e.last_used)
        for e in order:
            if used + incoming <= self.budget_bytes:
                break
            p = self.path_for(e.cid)
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            elif p.exists():
                p.unlink()
            del entries[e.cid]
            used -= e.bytes
            evicted.append(e.cid)
            self.evictions += 1
            self.bytes_evicted += e.bytes
            life["evictions"] += 1
            life["bytes_evicted"] += e.bytes
        if used + incoming > self.budget_bytes:
            raise CacheTooSmall(
                f"cannot fit {incoming} bytes in a {self.budget_bytes}-byte budget "
                f"even after evicting everything evictable")
        return evicted

    def verify(self, cid: str) -> tuple[bool, str]:
        """Recompute the CID of a cached artefact from the bytes on disk.

        A fetch verifies on the way in; this re-verifies afterwards, which is
        what catches a cache directory that was tampered with between fetches.
        Returns (ok, computed_cid). The layout comes from the CID, not from the
        store's configuration - see `verify_cid`.
        """
        p = self.path_for(cid)
        if not p.exists():
            raise WeightError(f"{cid} is not in {self.blobs}")
        return verify_cid(p, cid)

    def evict(self, cid: str) -> bool:
        """Drop one entry. Used by the tests and by `weights_cli evict`."""
        with self._locked():
            entries, life = self._read_index()
            if cid not in entries:
                return False
            p = self.path_for(cid)
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            elif p.exists():
                p.unlink()
            life["evictions"] += 1
            life["bytes_evicted"] += entries[cid].bytes
            del entries[cid]
            self._write_index(entries, life)
            return True

    def clear(self) -> int:
        with self._locked():
            entries, life = self._read_index()
            n = len(entries)
            for cid in list(entries):
                p = self.path_for(cid)
                if p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
                elif p.exists():
                    p.unlink()
            self._write_index({}, life)
            return n


# --------------------------------------------------------------------------
# the resolver
# --------------------------------------------------------------------------

@dataclass
class ResolvedWeights:
    model_id: str
    cid: str
    path: Path
    source: str              # "chain" | "manifest"
    cache_hit: bool
    bytes: int
    fetch_ms: float
    sha256: str
    content_hash_checked: bool
    chain_note: str = ""     # why the chain was not used, when it was not

    def to_row(self) -> dict:
        return {"model_id": self.model_id, "cid": self.cid, "path": str(self.path),
                "source": self.source, "cache_hit": self.cache_hit, "bytes": self.bytes,
                "fetch_ms": round(self.fetch_ms, 2), "sha256": self.sha256,
                "content_hash_checked": self.content_hash_checked,
                "chain_note": self.chain_note}


def model_key(model_id: str) -> bytes:
    """keccak256 of the model name - the bytes32 `ModelRegistry` indexes by."""
    from web3 import Web3
    return Web3.keccak(text=model_id)


class WeightResolver:
    """model id -> CID -> verified local path.

    The CID comes from `ModelRegistry` when a deployment is reachable and the id
    is registered, and from a local manifest otherwise. The fallback is not
    silent: `ResolvedWeights.source` says which was used and `chain_note` says
    why the chain was not, and both are written into the experiment's rows.

    Two independent checks run on every fetch that is not a cache hit:

      * the CID is recomputed from the received bytes (in `IPFSWeightStore.get`);
      * the sha256 is compared against `ModelRegistry.contentHash` when the CID
        came from the chain.

    Either failing raises. There is no code path that returns a path to bytes
    that did not verify.
    """

    def __init__(self, cache: LocalWeightCache,
                 manifest_path: Path | str = WEIGHTS_MANIFEST,
                 use_chain: bool = True):
        self.cache = cache
        self.manifest_path = Path(manifest_path)
        self.use_chain = use_chain
        self._backend = None
        self._chain_note = "" if use_chain else "chain lookup disabled by caller"

    # -- manifest --------------------------------------------------------

    def manifest(self) -> dict:
        if not self.manifest_path.exists():
            return {"models": {}}
        return json.loads(self.manifest_path.read_text())

    def publish_to_manifest(self, model_id: str, cid: str, sha256: str,
                            size_bytes: int) -> None:
        m = self.manifest()
        m.setdefault("models", {})[model_id] = {
            "cid": cid, "sha256": sha256, "bytes": size_bytes,
            "published_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps(m, indent=2))

    # -- chain -----------------------------------------------------------

    def _chain(self):
        """The `ChainBackend`, or None with `_chain_note` explaining why not."""
        if not self.use_chain:
            return None
        if self._backend is not None:
            return self._backend
        try:
            from edgegrid.chain import ChainBackend, ChainUnavailable
        except Exception as e:                       # web3 missing entirely
            self._chain_note = f"chain client unavailable: {type(e).__name__}: {e}"
            return None
        try:
            self._backend = ChainBackend()
        except ChainUnavailable as e:
            self._chain_note = f"chain unusable: {e.__class__.__name__}: {str(e).splitlines()[0]}"
            return None
        return self._backend

    def lookup(self, model_id: str) -> tuple[str, Optional[str], str, str]:
        """(cid, expected_sha256 or None, source, note). Raises if unresolvable."""
        backend = self._chain()
        if backend is not None:
            try:
                m = backend.models.functions.models(model_key(model_id)).call()
            except Exception as e:
                self._chain_note = f"ModelRegistry read failed: {type(e).__name__}: {e}"
                m = None
            if m is not None:
                publisher, content_hash, _version, _reg, _upd, revoked, uri = m[:7]
                if int(publisher, 16) != 0 and not revoked:
                    cid = uri.split("ipfs://", 1)[-1].strip()
                    if not cid:
                        raise ModelNotResolvable(
                            f"{model_id} is registered on chain with uri {uri!r}, which "
                            f"carries no CID")
                    return cid, content_hash.hex(), "chain", ""
                self._chain_note = (f"{model_id} is not registered on chain"
                                    if int(publisher, 16) == 0
                                    else f"{model_id} is revoked on chain")
        entry = self.manifest().get("models", {}).get(model_id)
        if entry is None:
            raise ModelNotResolvable(
                f"no CID for model {model_id!r}: {self._chain_note or 'chain not consulted'}; "
                f"and it is absent from {self.manifest_path}. Publish it first:\n"
                f"  python -m inference.weights_cli publish <file> --model {model_id}")
        return entry["cid"], entry.get("sha256"), "manifest", self._chain_note

    # -- resolve ---------------------------------------------------------

    def resolve(self, model_id: str, verify_cached: bool = True) -> ResolvedWeights:
        """Fetch and verify the weights for `model_id`.

        `verify_cached=False` skips the sha256 re-read on a cache *hit*, which
        for a multi-gigabyte GGUF is minutes of disk. The CID check on download
        is never skipped. When it is skipped the row says so:
        `content_hash_checked` is False, so a run can never look verified when
        it was not.
        """
        cid, expected, source, note = self.lookup(model_id)
        r = self.cache.fetch(cid, model_id=model_id)
        if r.hit and not verify_cached:
            return ResolvedWeights(model_id=model_id, cid=cid, path=r.path, source=source,
                                   cache_hit=True, bytes=r.bytes, fetch_ms=r.fetch_ms,
                                   sha256="", content_hash_checked=False, chain_note=note)
        digest = sha256_file(r.path)
        checked = False
        if expected:
            expected = expected.lower().removeprefix("0x")
            if source == "chain":
                if digest != expected:
                    raise ContentHashMismatch(
                        f"{model_id}: ModelRegistry records contentHash {expected} but the "
                        f"weights behind {cid} hash to {digest}. The registered digest and "
                        f"the published bytes disagree; the weights are not returned.")
                checked = True
            elif digest != expected:
                raise ContentHashMismatch(
                    f"{model_id}: manifest records sha256 {expected} but the weights "
                    f"behind {cid} hash to {digest}")
            else:
                checked = True
        return ResolvedWeights(model_id=model_id, cid=cid, path=r.path, source=source,
                               cache_hit=r.hit, bytes=r.bytes, fetch_ms=r.fetch_ms,
                               sha256=digest, content_hash_checked=checked,
                               chain_note=note)


def register_model_onchain(model_id: str, cid: str, content_hash_hex: str,
                           sender: Optional[str] = None) -> dict:
    """Bind `model_id` to `content_hash_hex` in `ModelRegistry`, uri `ipfs://<cid>`.

    Transaction signing, nonce policy and receipt checking live in
    `ChainBackend._send`; a second implementation here would be a second nonce
    policy against the same devnet, which is exactly the race that module's
    comments describe. It is reused deliberately.
    """
    from web3 import Web3

    from edgegrid.chain import ChainBackend

    backend = ChainBackend()
    who = Web3.to_checksum_address(sender) if sender else backend.owner
    key = model_key(model_id)
    digest = bytes.fromhex(content_hash_hex.removeprefix("0x"))
    if len(digest) != 32:
        raise ValueError(f"contentHash must be 32 bytes, got {len(digest)}")
    existing = backend.models.functions.models(key).call()
    uri = f"ipfs://{cid}"
    if int(existing[0], 16) == 0:
        fn = backend.models.functions.registerModel(key, digest, uri)
        action = "registerModel"
    else:
        fn = backend.models.functions.updateModel(key, digest, uri)
        action = "updateModel"
    receipt = backend._send(fn, who, label=action)
    m = backend.models.functions.models(key).call()
    return {"action": action, "tx_hash": receipt["transactionHash"].hex(),
            "gas_used": int(receipt["gasUsed"]), "model_id": model_id,
            "model_key": key.hex(), "cid": cid, "content_hash": digest.hex(),
            "version": int(m[2]), "publisher": m[0]}
