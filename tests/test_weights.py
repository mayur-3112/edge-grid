"""Tests for content-addressed weight distribution.

Two layers, deliberately separated:

  * The CID arithmetic and the cache policy are tested against fixtures and a
    fake store, so they are deterministic and run with no daemon. The CID
    fixtures are the values kubo itself printed for those bytes: the empty file
    is the canonical published one, the rest were recorded from `ipfs add` on
    kubo 0.43.0 with the default chunker. If this module's arithmetic ever
    drifts from kubo's, these fail without a daemon being involved.
  * `@pytest.mark.live` tests run the same paths against the real daemon that
    `make ipfs-up` starts, including the round trip and the tampering case.

The negative cases are the point: a store that serves other bytes and a cache
that was edited on disk must both be rejected, and the tests assert on the
exception type, not on a log line.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from edgegrid.weights import (
    CHUNK_SIZE,
    CacheTooSmall,
    CIDMismatch,
    ContentHashMismatch,
    IPFSUnavailable,
    IPFSWeightStore,
    LocalWeightCache,
    ModelNotResolvable,
    UnsupportedDAG,
    WeightResolver,
    cid_for_bytes,
    cid_for_file,
    cid_for_path,
    sha256_file,
)


# --------------------------------------------------------------------------
# CID arithmetic
# --------------------------------------------------------------------------

def _pattern(n: int, seed: int = 0) -> bytes:
    """Deterministic, non-repeating filler so a layout bug cannot be masked by
    two chunks happening to be identical."""
    import hashlib
    out = bytearray()
    block = hashlib.sha256(str(seed).encode()).digest()
    while len(out) < n:
        block = hashlib.sha256(block).digest()
        out += block
    return bytes(out[:n])


# (size, cid_version, expected CID) as printed by kubo 0.43.0 for `_pattern(size)`
# with --chunker=size-262144 --hash=sha2-256.
KUBO_FIXTURES = [
    (0, 0, "QmbFMke1KXqnYyBBWxB74N4c5SBnJMVAiMNRcGu6x1AwQH"),
    (0, 1, "bafkreihdwdcefgh4dqkjv67uzcmw7ojee6xedzdetojuzjevtenxquvyku"),
    (1, 0, "QmSNLQtBaTa1XT6QJUUbUxQvVXjU1Pzw9pndLdQf4RKxgo"),
    (1000, 0, "QmU6G3KJgmZvFL6kCaFgBG5Td5SK5ATKqcQSAahLJFqXtH"),
    (CHUNK_SIZE, 0, "QmfFcbz2bEiAsxu98E4P33csXiBgCFni9ppwM4eW3vBuRR"),
    (CHUNK_SIZE + 1, 0, "QmNiSsTcGYkLf21rC89XmAFUns414Jq6dUYmg8royghh16"),
    (3 * CHUNK_SIZE, 0, "QmVuPnwEyA1AAodehqJPjSCJcMxA4K4H75g6GEy9RzWHcf"),
    (3 * CHUNK_SIZE, 1, "bafybeiaamkkeuddwtv3f2kvlsuu32enaahzcgvvto4r7nre4vk7cu6dfzy"),
]


@pytest.mark.parametrize("size,version,expected", KUBO_FIXTURES)
def test_cid_matches_kubo_fixture(size, version, expected):
    assert cid_for_bytes(_pattern(size), version) == expected


def test_cid_v0_is_base58_and_v1_is_base32():
    c0 = cid_for_bytes(b"abc", 0)
    c1 = cid_for_bytes(b"abc", 1)
    assert c0.startswith("Qm") and len(c0) == 46
    assert c1.startswith("bafk")


def test_cid_changes_when_one_bit_changes():
    a = _pattern(5000)
    b = bytearray(a)
    b[2500] ^= 0x01
    assert cid_for_bytes(a) != cid_for_bytes(bytes(b))


def test_cid_for_file_streams_and_matches_bytes(tmp_path: Path):
    data = _pattern(CHUNK_SIZE * 2 + 7)
    p = tmp_path / "w.bin"
    p.write_bytes(data)
    assert cid_for_file(p) == cid_for_bytes(data)


def test_multi_level_dag_crosses_the_link_limit(tmp_path: Path):
    """175 chunks cannot fit in one 174-link node, so the builder must add a
    level. A single-level builder silently produces a different CID here."""
    from edgegrid.weights import MAX_LINKS
    one_level = cid_for_bytes(_pattern(CHUNK_SIZE * MAX_LINKS, seed=1))
    two_level = cid_for_bytes(_pattern(CHUNK_SIZE * (MAX_LINKS + 1), seed=1))
    assert one_level != two_level


def test_directory_cid_depends_on_names_not_only_content(tmp_path: Path):
    a = tmp_path / "a"
    (a / "sub").mkdir(parents=True)
    (a / "sub" / "x.bin").write_bytes(b"same content")
    b = tmp_path / "b"
    (b / "sub").mkdir(parents=True)
    (b / "sub" / "y.bin").write_bytes(b"same content")
    assert cid_for_path(a) != cid_for_path(b)


def test_symlink_in_a_directory_is_refused(tmp_path: Path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "real.bin").write_bytes(b"x")
    (d / "link.bin").symlink_to(d / "real.bin")
    with pytest.raises(UnsupportedDAG):
        cid_for_path(d)


def test_sha256_file_of_a_directory_is_order_independent(tmp_path: Path):
    for name in ("one", "two"):
        d = tmp_path / name
        d.mkdir()
        for f in (("b.bin", b"bbb"), ("a.bin", b"aaa"))[::1 if name == "one" else -1]:
            (d / f[0]).write_bytes(f[1])
    assert sha256_file(tmp_path / "one") == sha256_file(tmp_path / "two")


# --------------------------------------------------------------------------
# a fake store, so cache policy is testable without a daemon
# --------------------------------------------------------------------------

class FakeStore(IPFSWeightStore):
    """Serves from a dict of CID -> bytes. Counts downloads, so "a cache hit
    never re-downloads" is asserted on the network, not on a timing."""

    def __init__(self, blobs: dict[str, bytes] | None = None, cid_version: int = 0):
        self.api_url = "fake://store"
        self.timeout_s = 1.0
        self.cid_version = cid_version
        self.raw_leaves = cid_version == 1
        self.blobs: dict[str, bytes] = dict(blobs or {})
        self.downloads: list[str] = []
        self.serve_instead: dict[str, str] = {}

    def available(self) -> bool:
        return True

    def stat(self, cid: str) -> dict:
        if cid not in self.blobs:
            raise KeyError(cid)
        return {"Hash": cid, "Size": len(self.blobs[cid]), "Type": "file"}

    def put(self, data: bytes) -> str:
        cid = cid_for_bytes(data, self.cid_version, self.raw_leaves)
        self.blobs[cid] = data
        return cid

    def _download(self, cid: str, tmp: Path) -> Path:
        self.downloads.append(cid)
        served = self.serve_instead.get(cid, cid)
        if served not in self.blobs:
            raise KeyError(served)
        out = tmp / "blob"
        out.write_bytes(self.blobs[served])
        return out


@pytest.fixture
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def cache(store: FakeStore, tmp_path: Path) -> LocalWeightCache:
    return LocalWeightCache(store, cache_dir=tmp_path / "cache", budget_bytes=10_000)


def test_fetch_verifies_and_returns_the_bytes(cache: LocalWeightCache, store: FakeStore):
    cid = store.put(b"weights-v1")
    r = cache.fetch(cid)
    assert r.hit is False and r.verified is True
    assert r.path.read_bytes() == b"weights-v1"
    assert cache.verify(cid) == (True, cid)


def test_cache_hit_does_not_redownload(cache: LocalWeightCache, store: FakeStore):
    cid = store.put(_pattern(500))
    cache.fetch(cid)
    cache.fetch(cid)
    cache.fetch(cid)
    assert store.downloads == [cid], "a hit must not touch the store"
    assert cache.stats()["session"] == {
        "hits": 2, "misses": 1, "evictions": 0,
        "bytes_downloaded": 500, "bytes_evicted": 0}


def test_eviction_is_least_recently_used(cache: LocalWeightCache, store: FakeStore):
    a = store.put(_pattern(4000, 1))
    b = store.put(_pattern(4000, 2))
    c = store.put(_pattern(4000, 3))
    cache.fetch(a)
    cache.fetch(b)
    cache.fetch(a)                      # a is now the most recently used
    r = cache.fetch(c)                  # 12000 > 10000 budget: one must go
    assert r.evicted == [b], "the least recently used entry must be the one evicted"
    assert cache.contains(a) and cache.contains(c) and not cache.contains(b)
    assert cache.total_bytes() <= cache.budget_bytes


def test_eviction_takes_as_many_as_needed(cache: LocalWeightCache, store: FakeStore):
    smalls = [store.put(_pattern(2000, i)) for i in range(4)]
    for cid in smalls:
        cache.fetch(cid)
    # 8000 cached, budget 10000, 5000 incoming: exactly two of the four have to
    # go, oldest first, and no more than that.
    big = store.put(_pattern(5000, 99))
    r = cache.fetch(big)
    assert r.evicted == smalls[:2], "evicts in LRU order until the incoming fits"
    assert cache.total_bytes() == 2 * 2000 + 5000


def test_artefact_larger_than_the_budget_raises(cache: LocalWeightCache, store: FakeStore):
    cid = store.put(_pattern(20_000))
    with pytest.raises(CacheTooSmall):
        cache.fetch(cid)
    assert not cache.contains(cid)


def test_store_serving_other_bytes_is_rejected(cache: LocalWeightCache, store: FakeStore):
    honest = store.put(b"the weights you asked for")
    other = store.put(b"a much cheaper model")
    store.serve_instead[honest] = other
    with pytest.raises(CIDMismatch):
        cache.fetch(honest)
    assert not cache.contains(honest), "rejected bytes must not be cached"
    assert not cache.path_for(honest).exists()


def test_corrupted_cache_file_fails_reverification(cache: LocalWeightCache, store: FakeStore):
    cid = store.put(_pattern(3000))
    p = cache.fetch(cid).path
    data = bytearray(p.read_bytes())
    data[1500] ^= 0xFF
    p.write_bytes(bytes(data))
    ok, got = cache.verify(cid)
    assert ok is False and got != cid


def test_a_blob_deleted_underneath_the_index_is_a_miss_not_a_hit(
        cache: LocalWeightCache, store: FakeStore):
    cid = store.put(_pattern(1000))
    cache.fetch(cid)
    cache.path_for(cid).unlink()
    r = cache.fetch(cid)
    assert r.hit is False and r.path.exists()
    assert store.downloads == [cid, cid]


def test_index_survives_a_new_cache_object(store: FakeStore, tmp_path: Path):
    c1 = LocalWeightCache(store, cache_dir=tmp_path / "c", budget_bytes=10_000)
    cid = store.put(_pattern(1000))
    c1.fetch(cid)
    c2 = LocalWeightCache(store, cache_dir=tmp_path / "c", budget_bytes=10_000)
    assert c2.fetch(cid).hit is True
    assert c2.stats()["lifetime"]["misses"] == 1


def test_evict_and_clear(cache: LocalWeightCache, store: FakeStore):
    cids = [store.put(_pattern(1000, i)) for i in range(3)]
    for c in cids:
        cache.fetch(c)
    assert cache.evict(cids[0]) is True
    assert cache.evict(cids[0]) is False
    assert cache.clear() == 2
    assert cache.entries() == []


# --------------------------------------------------------------------------
# resolver
# --------------------------------------------------------------------------

@pytest.fixture
def resolver(cache: LocalWeightCache, tmp_path: Path) -> WeightResolver:
    return WeightResolver(cache, tmp_path / "manifest.json", use_chain=False)


def test_resolver_round_trip_through_the_manifest(resolver: WeightResolver,
                                                  store: FakeStore, tmp_path: Path):
    data = _pattern(2048)
    cid = store.put(data)
    src = tmp_path / "w.bin"
    src.write_bytes(data)
    resolver.publish_to_manifest("m1", cid, sha256_file(src), len(data))

    r = resolver.resolve("m1")
    assert r.cid == cid and r.source == "manifest" and r.cache_hit is False
    assert r.content_hash_checked is True
    assert r.path.read_bytes() == data

    again = resolver.resolve("m1")
    assert again.cache_hit is True


def test_resolver_raises_for_an_unknown_model(resolver: WeightResolver):
    with pytest.raises(ModelNotResolvable):
        resolver.resolve("no-such-model")


def test_resolver_rejects_a_manifest_digest_that_does_not_match(
        resolver: WeightResolver, store: FakeStore):
    cid = store.put(_pattern(1024))
    resolver.publish_to_manifest("m2", cid, "00" * 32, 1024)
    with pytest.raises(ContentHashMismatch):
        resolver.resolve("m2")


def test_resolver_records_that_a_cached_digest_was_not_rechecked(
        resolver: WeightResolver, store: FakeStore, tmp_path: Path):
    data = _pattern(700)
    cid = store.put(data)
    src = tmp_path / "w2.bin"
    src.write_bytes(data)
    resolver.publish_to_manifest("m3", cid, sha256_file(src), len(data))
    resolver.resolve("m3")
    r = resolver.resolve("m3", verify_cached=False)
    assert r.cache_hit is True
    assert r.content_hash_checked is False, "a skipped check must be visible in the row"
    assert r.sha256 == "" and r.to_row()["content_hash_checked"] is False


def test_resolver_never_returns_a_path_when_verification_fails(
        resolver: WeightResolver, store: FakeStore, tmp_path: Path):
    data = _pattern(900)
    cid = store.put(data)
    other = store.put(_pattern(900, 7))
    store.serve_instead[cid] = other
    resolver.publish_to_manifest("m4", cid, "aa" * 32, len(data))
    with pytest.raises(CIDMismatch):
        resolver.resolve("m4")


# --------------------------------------------------------------------------
# unavailable daemon
# --------------------------------------------------------------------------

def test_available_is_false_and_calls_raise_when_nothing_is_listening():
    # Port 1 on loopback: reserved, never bound by anything on this host.
    store = IPFSWeightStore(api_url="http://127.0.0.1:1", timeout_s=1.0)
    assert store.available() is False
    with pytest.raises(IPFSUnavailable):
        store.version()


# --------------------------------------------------------------------------
# live: the real daemon
# --------------------------------------------------------------------------

def _daemon_up() -> bool:
    return IPFSWeightStore(api_url=os.getenv("IPFS_API_URL", "http://127.0.0.1:5001"),
                           timeout_s=2.0).available()


@pytest.mark.live
def test_live_round_trip_against_kubo(tmp_path: Path):
    """Publish, fetch, verify, and confirm this module's CID is the daemon's.

    This is the test that would catch kubo changing its default layout: the
    local computation and the daemon's answer are compared on real bytes.
    """
    if not _daemon_up():
        pytest.skip("no kubo daemon; run `make ipfs-up`")
    store = IPFSWeightStore()
    src = tmp_path / "weights.bin"
    src.write_bytes(_pattern(CHUNK_SIZE * 3 + 11, seed=42))

    res = store.add(src)
    assert res.daemon_cid == res.local_cid == cid_for_file(src)
    assert res.cid in store.pins()
    assert store.stat(res.cid)["Type"] == "file"

    cache = LocalWeightCache(store, cache_dir=tmp_path / "cache",
                             budget_bytes=CHUNK_SIZE * 8)
    cold = cache.fetch(res.cid)
    assert cold.hit is False and cold.path.read_bytes() == src.read_bytes()
    warm = cache.fetch(res.cid)
    assert warm.hit is True
    assert warm.fetch_ms < cold.fetch_ms
    assert cache.verify(res.cid) == (True, res.cid)


@pytest.mark.live
def test_live_tampered_artefact_is_rejected(tmp_path: Path):
    """The negative result, against the real daemon: two artefacts are really
    published, and a fetch of one that is answered with the other raises."""
    if not _daemon_up():
        pytest.skip("no kubo daemon; run `make ipfs-up`")
    from inference.weights_cli import TamperingStore

    honest = tmp_path / "honest.bin"
    honest.write_bytes(_pattern(4096, seed=1))
    cheap = tmp_path / "cheap.bin"
    cheap.write_bytes(_pattern(4096, seed=2))

    store = IPFSWeightStore()
    a = store.add(honest).cid
    b = store.add(cheap).cid
    assert a != b

    tamper = TamperingStore(swap={a: b})
    cache = LocalWeightCache(tamper, cache_dir=tmp_path / "cache", budget_bytes=1 << 20)
    with pytest.raises(CIDMismatch) as e:
        cache.fetch(a)
    assert a in str(e.value) and b in str(e.value)
    assert not cache.path_for(a).exists()


@pytest.mark.live
def test_live_directory_round_trip(tmp_path: Path):
    if not _daemon_up():
        pytest.skip("no kubo daemon; run `make ipfs-up`")
    store = IPFSWeightStore()
    d = tmp_path / "model"
    (d / "shards").mkdir(parents=True)
    (d / "config.json").write_text(json.dumps({"arch": "test"}))
    (d / "shards" / "00.bin").write_bytes(_pattern(CHUNK_SIZE + 5, seed=3))

    res = store.add(d)
    assert res.is_dir and res.cid == cid_for_path(d)
    back = store.get(res.cid, tmp_path / "back")
    assert (back / "config.json").read_text() == (d / "config.json").read_text()
    assert (back / "shards" / "00.bin").read_bytes() == (d / "shards" / "00.bin").read_bytes()


@pytest.mark.live
def test_live_cli_demo_runs():
    if not _daemon_up():
        pytest.skip("no kubo daemon; run `make ipfs-up`")
    from inference.weights_cli import main
    assert main(["--budget-bytes", str(64 << 20), "demo", "--demo-bytes", str(1 << 20)]) == 0
