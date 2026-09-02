"""Command line for content-addressed weight distribution (Objective 3).

The node-side face of `edgegrid.weights`: publish an artefact and get a CID,
resolve a model id to verified local weights, see what the LRU cache holds, and
run the experiment that measures all of it.

    python -m inference.weights_cli status
    python -m inference.weights_cli publish weights.gguf --model qwen3-vl:2b --register
    python -m inference.weights_cli fetch qwen3-vl:2b
    python -m inference.weights_cli ls
    python -m inference.weights_cli demo
    python -m inference.weights_cli experiment

`demo` is the one-command proof: it publishes a synthetic weight file, fetches
it on a cold cache and again warm, and prints both timings. `experiment` is the
same thing at several sizes with a byte budget small enough to force eviction,
plus the negative case - a tampered artefact - written through `RunLog`.
"""

from __future__ import annotations

import argparse
import os
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

from edgegrid.runlog import RunLog
from edgegrid.weights import (
    CIDMismatch,
    IPFSUnavailable,
    IPFSWeightStore,
    LocalWeightCache,
    ModelNotResolvable,
    WeightError,
    WeightResolver,
    WEIGHTS_CACHE_BUDGET_BYTES,
    WEIGHTS_CACHE_DIR,
    WEIGHTS_MANIFEST,
    cid_for_path,
    register_model_onchain,
    sha256_file,
)

MIB = 1024 * 1024


def human(n: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(n) < 1024 or unit == "GiB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GiB"


def _store(args) -> IPFSWeightStore:
    store = IPFSWeightStore(api_url=args.api, cid_version=args.cid_version)
    if not store.available():
        raise IPFSUnavailable(
            f"no kubo daemon at {args.api}. Start one:\n"
            f"  make ipfs-up\n"
            f"(or: cd deploy/ipfs && docker compose up -d)")
    return store


def _cache(args, store: IPFSWeightStore) -> LocalWeightCache:
    return LocalWeightCache(store, cache_dir=args.cache_dir, budget_bytes=args.budget_bytes)


# --------------------------------------------------------------------------
# synthetic weights
#
# A stand-in for a GGUF: incompressible bytes of a stated size, derived from a
# seed so a run is reproducible and two artefacts of the same size are still
# different content. Nothing here pretends to be a real model - the experiment
# measures distribution, not inference.
# --------------------------------------------------------------------------

def synth_weights(path: Path, size_bytes: int, seed: int = 0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    with path.open("wb") as fh:
        written = 0
        while written < size_bytes:
            take = min(MIB, size_bytes - written)
            fh.write(rng.randbytes(take))
            written += take
    return path


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_status(args) -> int:
    store = IPFSWeightStore(api_url=args.api, cid_version=args.cid_version)
    up = store.available()
    print(f"ipfs api        {args.api}   {'up' if up else 'DOWN'}")
    if up:
        v = store.version()
        print(f"kubo version    {v['Version']} ({v.get('Commit', '')})")
        print(f"peer id         {store.node_id()}")
        print(f"pinned cids     {len(store.pins())}")
    cache = LocalWeightCache(store, cache_dir=args.cache_dir, budget_bytes=args.budget_bytes)
    s = cache.stats()
    print(f"cache dir       {cache.dir}")
    print(f"cache usage     {human(s['used_bytes'])} / {human(s['budget_bytes'])} "
          f"in {s['entries']} entries")
    print(f"cache lifetime  {s['lifetime']}")
    print(f"manifest        {args.manifest} "
          f"({len(WeightResolver(cache, args.manifest).manifest().get('models', {}))} models)")
    try:
        from edgegrid.chain import chain_available
        ok, why = chain_available()
        print(f"chain           {'up' if ok else 'unusable'}: {why.splitlines()[0]}")
    except Exception as e:
        print(f"chain           unusable: {type(e).__name__}: {e}")
    return 0 if up else 1


def cmd_publish(args) -> int:
    store = _store(args)
    path = Path(args.path)
    local = cid_for_path(path, store.cid_version, store.raw_leaves)
    res = store.add(path, pin=not args.no_pin)
    digest = sha256_file(path)
    print(f"path            {path}")
    print(f"bytes           {res.bytes} ({human(res.bytes)})")
    print(f"cid             {res.cid}")
    print(f"cid (local)     {local}   [computed here, not taken from the daemon]")
    print(f"sha256          {digest}")
    print(f"pinned          {res.pinned}")
    print(f"add ms          {res.add_ms:.1f}")

    if args.model:
        cache = _cache(args, store)
        resolver = WeightResolver(cache, args.manifest, use_chain=False)
        resolver.publish_to_manifest(args.model, res.cid, digest, res.bytes)
        print(f"manifest        {args.manifest} <- {args.model}")
    if args.register:
        if not args.model:
            print("--register needs --model", file=sys.stderr)
            return 2
        rec = register_model_onchain(args.model, res.cid, digest, sender=args.sender)
        print(f"chain           {rec['action']} v{rec['version']} "
              f"tx={rec['tx_hash']} gas={rec['gas_used']}")
    return 0


def cmd_fetch(args) -> int:
    store = _store(args)
    cache = _cache(args, store)
    resolver = WeightResolver(cache, args.manifest, use_chain=not args.no_chain)
    r = resolver.resolve(args.model_id, verify_cached=not args.no_verify_cached)
    print(f"model           {r.model_id}")
    print(f"cid             {r.cid}   (source: {r.source})")
    if r.chain_note:
        print(f"chain note      {r.chain_note}")
    print(f"path            {r.path}")
    print(f"bytes           {r.bytes} ({human(r.bytes)})")
    print(f"cache           {'HIT' if r.cache_hit else 'MISS (downloaded and verified)'}")
    print(f"fetch ms        {r.fetch_ms:.1f}")
    print(f"sha256          {r.sha256 or '(not recomputed: --no-verify-cached on a hit)'}")
    print(f"digest checked  {r.content_hash_checked}")
    if args.dest:
        dest = Path(args.dest)
        shutil.copytree(r.path, dest) if r.path.is_dir() else shutil.copy2(r.path, dest)
        print(f"copied to       {dest}")
    return 0


def cmd_ls(args) -> int:
    store = IPFSWeightStore(api_url=args.api, cid_version=args.cid_version)
    cache = _cache(args, store)
    entries = cache.entries()
    if not entries:
        print(f"cache {cache.dir} is empty")
        return 0
    now = time.time()
    print(f"{'CID':<50} {'SIZE':>10} {'HITS':>5} {'LAST USE':>10}  MODEL")
    for e in entries:
        print(f"{e.cid:<50} {human(e.bytes):>10} {e.hits:>5} "
              f"{now - e.last_used:>9.1f}s  {e.model_id}")
    s = cache.stats()
    print(f"\n{human(s['used_bytes'])} / {human(s['budget_bytes'])} used, "
          f"{s['entries']} entries")
    print(f"session {s['session']}")
    print(f"lifetime {s['lifetime']}")
    return 0


def cmd_evict(args) -> int:
    store = IPFSWeightStore(api_url=args.api, cid_version=args.cid_version)
    cache = _cache(args, store)
    if args.all:
        print(f"evicted {cache.clear()} entries")
        return 0
    if not args.cid:
        print("give a CID or --all", file=sys.stderr)
        return 2
    print("evicted" if cache.evict(args.cid) else "not cached")
    return 0


def cmd_demo(args) -> int:
    """Publish a synthetic weight file, fetch it cold, then warm."""
    store = _store(args)
    tmp = Path(tempfile.mkdtemp(prefix="edgegrid-weights-demo-"))
    cache_dir = tmp / "cache"
    cache = LocalWeightCache(store, cache_dir=cache_dir, budget_bytes=args.budget_bytes)
    try:
        src = synth_weights(tmp / "demo.weights", args.demo_bytes, seed=int(time.time()))
        res = store.add(src)
        print(f"published       {human(res.bytes)} -> {res.cid}")
        print(f"                daemon and local CID agree ({res.local_cid})")

        cold = cache.fetch(res.cid, model_id="demo")
        ok, got = cache.verify(res.cid)
        print(f"cold fetch      {cold.fetch_ms:8.1f} ms   hit={cold.hit}  verified={ok}")
        warm = cache.fetch(res.cid, model_id="demo")
        print(f"warm fetch      {warm.fetch_ms:8.1f} ms   hit={warm.hit}")
        speedup = cold.fetch_ms / warm.fetch_ms if warm.fetch_ms > 0 else float("inf")
        print(f"speedup         {speedup:8.1f}x")
        print(f"cache           {cache.stats()['session']}")
        if not ok:
            print(f"VERIFICATION FAILED: cached bytes hash to {got}", file=sys.stderr)
            return 1
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# experiment
# --------------------------------------------------------------------------

class TamperingStore(IPFSWeightStore):
    """A store that serves the wrong bytes - the adversary, made concrete.

    A provider that wants to be paid for a large model while running a small one
    has to make the fetch of the large model's CID return something else. kubo
    itself will not do that (it validates blocks against their hashes on the way
    out), so the substitution is made here, at the point where bytes enter this
    process. That is the right place: it is exactly the trust boundary
    `IPFSWeightStore.get` is supposed to defend, and the test is whether the CID
    recomputation notices.
    """

    def __init__(self, *a, swap: Optional[dict[str, str]] = None, **kw):
        super().__init__(*a, **kw)
        self.swap = dict(swap or {})

    def _download(self, cid: str, tmp: Path) -> Path:
        return super()._download(self.swap.get(cid, cid), tmp)


def cmd_experiment(args) -> int:
    """Publish artefacts of varying size, fetch each cold and warm, force the
    LRU to evict, and confirm that a tampered artefact is rejected.

    Honesty note recorded in the run log as well as here: the daemon runs on
    this host, so a "cold" fetch measures the local HTTP API, block reassembly
    and CID recomputation, not a wide-area transfer. The number that is being
    claimed is the cost the *client* pays to obtain and verify weights, and the
    cold/warm ratio is what the cache is judged on.
    """
    store = _store(args)
    version = store.version()
    sizes = [int(s) for s in args.sizes.split(",")]
    # A budget that forces eviction but can still hold the largest artefact:
    # below the total, above the biggest single item. A budget under the biggest
    # item is a legitimate configuration - it raises CacheTooSmall rather than
    # thrashing - but it is not what this experiment is measuring.
    budget = (args.budget_bytes if args.budget_bytes_given
              else max(int(sum(sizes) * 0.45), max(sizes) + MIB))

    params = {
        "sizes_bytes": sizes,
        "budget_bytes": budget,
        "total_bytes": sum(sizes),
        "cid_version": store.cid_version,
        "api_url": store.api_url,
        "kubo_version": version["Version"],
        "kubo_commit": version.get("Commit", ""),
        "peer_id": store.node_id(),
        "chunk_size": 262144,
        "daemon_is_local": store.api_url.startswith(("http://127.0.0.1", "http://localhost")),
    }

    with RunLog("weights", params) as log, \
            tempfile.TemporaryDirectory(prefix="edgegrid-weights-exp-") as _workroot:
        if budget >= sum(sizes):
            log.note(f"budget {budget} >= total {sum(sizes)}: nothing can be evicted, so "
                     f"the eviction columns in this run are structurally zero")
        log.note("cold fetches cross a loopback HTTP API to a kubo daemon on this host; "
                 "they measure client-side fetch and verification cost, not WAN transfer")
        # Bytes live outside the run directory: a results directory holding a
        # hundred megabytes of synthetic weights per run is a results directory
        # nobody keeps. Only the measurements are written to `log.dir`.
        workroot = Path(_workroot)
        log.note(f"artefacts and caches under {workroot}, removed when the run ends")
        work = workroot / "artefacts"
        cache_dir = workroot / "cache"
        cache = LocalWeightCache(store, cache_dir=cache_dir, budget_bytes=budget)
        # Not "manifest.json": RunLog writes its own manifest.json into the run
        # directory when it finishes, and the two would silently overwrite.
        resolver = WeightResolver(cache, Path(log.dir) / "weights_manifest.json",
                                  use_chain=False)

        published: list[dict] = []
        for i, size in enumerate(sizes):
            model_id = f"synth-weights-{size}"
            src = synth_weights(work / f"{model_id}.bin", size, seed=i)
            res = store.add(src)
            digest = sha256_file(src)
            resolver.publish_to_manifest(model_id, res.cid, digest, res.bytes)
            published.append({"model_id": model_id, "cid": res.cid, "bytes": res.bytes,
                              "sha256": digest, "add_ms": res.add_ms, "src": src})
            log.note(f"published {model_id} {res.bytes}B -> {res.cid} in {res.add_ms:.1f}ms")

        # Cold pass: fetched in ascending size order into a budget that cannot
        # hold them all, so the LRU has to evict and the eviction order is
        # observable.
        for p in published:
            cold = cache.fetch(p["cid"], model_id=p["model_id"])
            ok, got = cache.verify(p["cid"])
            warm = cache.fetch(p["cid"], model_id=p["model_id"])
            log.append("artefacts", {
                "model_id": p["model_id"],
                "cid": p["cid"],
                "bytes": p["bytes"],
                "add_ms": round(p["add_ms"], 2),
                "cold_fetch_ms": round(cold.fetch_ms, 2),
                "warm_fetch_ms": round(warm.fetch_ms, 3),
                "speedup": round(cold.fetch_ms / warm.fetch_ms, 1) if warm.fetch_ms else "",
                "cold_hit": cold.hit,
                "warm_hit": warm.hit,
                "cid_verified": ok,
                "recomputed_cid": got,
                "mb_per_s": round(p["bytes"] / 1e6 / (cold.fetch_ms / 1000.0), 2)
                if cold.fetch_ms else "",
                "evicted_on_insert": ";".join(cold.evicted),
                "n_evicted": len(cold.evicted),
            })
        after_cold = cache.stats()

        # Second pass over every artefact in the same order. Anything the LRU
        # evicted during the first pass is a miss again; anything that survived
        # is a hit. This is the number that says the cache is an LRU and not a
        # dictionary that grew without bound.
        second_hits = 0
        for p in published:
            r = cache.fetch(p["cid"], model_id=p["model_id"])
            second_hits += int(r.hit)
            log.append("second_pass", {
                "model_id": p["model_id"], "cid": p["cid"], "bytes": p["bytes"],
                "hit": r.hit, "fetch_ms": round(r.fetch_ms, 3),
                "n_evicted": len(r.evicted),
            })
        log.note(f"second pass hit {second_hits}/{len(published)}. A repeated sequential "
                 f"scan of a working set larger than the budget "
                 f"({sum(sizes)} > {budget} bytes) is the worst case for LRU: each fetch "
                 f"evicts the entry the next fetch would have wanted. That is a property "
                 f"of the access pattern, not a defect - the per-model reuse a node "
                 f"actually has is the cold/warm pair in artefacts.csv")

        # -- eviction order ------------------------------------------------
        #
        # "It evicts something" is not the claim; "it evicts the least recently
        # used thing" is. Three artefacts are loaded into a budget that holds
        # exactly three, the oldest is then touched so the *middle* one becomes
        # least-recently-used, and a fourth is inserted. The row records what
        # was predicted and what actually went, so the claim is falsifiable.
        small = published[:3]
        fourth = published[3] if len(published) > 3 else None
        if fourth is not None:
            lru_budget = sum(p["bytes"] for p in small) + fourth["bytes"] - 1
            lru_cache = LocalWeightCache(store, cache_dir=workroot / "cache-lru",
                                         budget_bytes=lru_budget)
            for p in small:
                lru_cache.fetch(p["cid"], model_id=p["model_id"])
            time.sleep(0.01)                     # distinct last_used stamps
            lru_cache.fetch(small[0]["cid"])     # touch: small[1] is now the LRU
            expected = small[1]["cid"]
            r4 = lru_cache.fetch(fourth["cid"], model_id=fourth["model_id"])
            log.append("lru_order", {
                "budget_bytes": lru_budget,
                "loaded": ";".join(p["model_id"] for p in small),
                "touched": small[0]["model_id"],
                "inserted": fourth["model_id"],
                "expected_evicted": expected,
                "actual_evicted": ";".join(r4.evicted),
                "correct": r4.evicted[:1] == [expected],
                "still_cached": ";".join(sorted(e.cid for e in lru_cache.entries())),
            })
            lru_correct = r4.evicted[:1] == [expected]
        else:
            lru_correct = None
            log.drop("lru_order", "fewer than four artefacts; no ordering case to run")

        # -- the negative result -----------------------------------------
        #
        # Two distinct tampering routes, because they defend different things:
        # a store that serves another artefact's bytes for a requested CID, and
        # a cache directory edited after a verified fetch.
        verdicts: list[dict] = []

        honest, victim = published[0], published[-1]
        tamper = TamperingStore(api_url=store.api_url, cid_version=store.cid_version,
                                swap={victim["cid"]: honest["cid"]})
        tamper_cache_dir = workroot / "cache-tampered"
        tamper_cache = LocalWeightCache(tamper, cache_dir=tamper_cache_dir,
                                        budget_bytes=budget)
        try:
            r = tamper_cache.fetch(victim["cid"])
            verdicts.append({"case": "store_serves_other_artefact",
                             "cid_requested": victim["cid"], "cid_served": honest["cid"],
                             "outcome": "ACCEPTED", "exception": "",
                             "detail": f"returned {r.path}"})
        except CIDMismatch as e:
            verdicts.append({"case": "store_serves_other_artefact",
                             "cid_requested": victim["cid"], "cid_served": honest["cid"],
                             "outcome": "REJECTED", "exception": type(e).__name__,
                             "detail": str(e).replace("\n", " ")[:220]})
        cached_after_reject = (tamper_cache.path_for(victim["cid"]).exists())

        # One byte flipped in the middle of an artefact that is already cached
        # and was already verified once.
        flip_target = published[-1]
        good = cache.fetch(flip_target["cid"], model_id=flip_target["model_id"])
        blob = good.path
        data = bytearray(blob.read_bytes())
        pos = len(data) // 2
        data[pos] ^= 0xFF
        blob.write_bytes(bytes(data))
        ok, got = cache.verify(flip_target["cid"])
        verdicts.append({"case": "cached_artefact_bit_flipped",
                         "cid_requested": flip_target["cid"], "cid_served": got,
                         "outcome": "REJECTED" if not ok else "ACCEPTED",
                         "exception": "", "detail": f"one byte flipped at offset {pos}"})

        # And the same corrupted file offered through the resolver, which is the
        # path a node actually calls.
        try:
            rw = resolver.resolve(flip_target["model_id"], verify_cached=True)
            verdicts.append({"case": "resolver_on_corrupted_cache",
                             "cid_requested": flip_target["cid"], "cid_served": rw.sha256,
                             "outcome": "ACCEPTED", "exception": "",
                             "detail": f"returned {rw.path}"})
        except WeightError as e:
            verdicts.append({"case": "resolver_on_corrupted_cache",
                             "cid_requested": flip_target["cid"], "cid_served": "",
                             "outcome": "REJECTED", "exception": type(e).__name__,
                             "detail": str(e).replace("\n", " ")[:220]})

        # A control: the honest artefact through the same code path, so a
        # "rejected" row cannot be read as "this rejects everything".
        try:
            rc = resolver.resolve(honest["model_id"], verify_cached=True)
            verdicts.append({"case": "control_honest_artefact",
                             "cid_requested": honest["cid"], "cid_served": rc.cid,
                             "outcome": "ACCEPTED", "exception": "",
                             "detail": f"sha256 {rc.sha256[:16]}... checked="
                                       f"{rc.content_hash_checked}"})
        except WeightError as e:
            verdicts.append({"case": "control_honest_artefact",
                             "cid_requested": honest["cid"], "cid_served": "",
                             "outcome": "REJECTED", "exception": type(e).__name__,
                             "detail": str(e).replace("\n", " ")[:220]})

        for v in verdicts:
            log.append("verification", v)

        log.write_json("cache_stats", {
            "after_cold_pass": after_cold,
            "final": cache.stats(),
            "tampered_cache_kept_bytes": cached_after_reject,
        })

        attacks = [v for v in verdicts if v["case"] != "control_honest_artefact"]
        control = verdicts[-1]
        rejected = sum(1 for v in attacks if v["outcome"] == "REJECTED")
        print(f"\nrun          {log.dir}")
        print(f"artefacts    {len(published)}  total {human(sum(sizes))}  "
              f"budget {human(budget)}")
        st = cache.stats()["session"]
        print(f"cache        hits={st['hits']} misses={st['misses']} "
              f"evictions={st['evictions']} evicted={human(st['bytes_evicted'])}")
        print(f"lru order    {'correct' if lru_correct else lru_correct}")
        print(f"verification {rejected}/{len(attacks)} tampering cases rejected; "
              f"honest control {control['outcome']}")

        failures = []
        if cached_after_reject:
            failures.append("the rejected artefact was left in the cache")
        if rejected != len(attacks):
            failures.append("a tampering case was not rejected")
        if control["outcome"] != "ACCEPTED":
            failures.append("the honest control was rejected")
        if lru_correct is False:
            failures.append("eviction did not follow least-recently-used order")
        if failures:
            log.write_json("failures", failures)
            for f in failures:
                print(f"FAILED: {f}", file=sys.stderr)
            return 1
    return 0


# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="weights_cli", description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api", default=os.getenv("IPFS_API_URL", "http://127.0.0.1:5001"),
                   help="kubo HTTP API base url")
    p.add_argument("--cache-dir", default=str(WEIGHTS_CACHE_DIR), type=Path)
    p.add_argument("--budget-bytes", type=int, default=WEIGHTS_CACHE_BUDGET_BYTES,
                   help="LRU byte budget for the cache")
    p.add_argument("--manifest", default=str(WEIGHTS_MANIFEST), type=Path)
    p.add_argument("--cid-version", type=int, default=0, choices=(0, 1))
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="daemon, cache and chain state").set_defaults(fn=cmd_status)

    pub = sub.add_parser("publish", help="add a file or directory to IPFS")
    pub.add_argument("path")
    pub.add_argument("--model", help="also record the CID for this model id")
    pub.add_argument("--register", action="store_true",
                     help="write the binding to ModelRegistry on chain")
    pub.add_argument("--sender", help="address to send the registration from")
    pub.add_argument("--no-pin", action="store_true")
    pub.set_defaults(fn=cmd_publish)

    f = sub.add_parser("fetch", help="resolve a model id to verified local weights")
    f.add_argument("model_id")
    f.add_argument("--dest", help="also copy the artefact here")
    f.add_argument("--no-chain", action="store_true", help="use the manifest only")
    f.add_argument("--no-verify-cached", action="store_true",
                   help="skip the sha256 re-read on a cache hit (recorded in the row)")
    f.set_defaults(fn=cmd_fetch)

    sub.add_parser("ls", help="what is cached, with sizes and last use").set_defaults(fn=cmd_ls)

    ev = sub.add_parser("evict", help="drop one CID or the whole cache")
    ev.add_argument("cid", nargs="?")
    ev.add_argument("--all", action="store_true")
    ev.set_defaults(fn=cmd_evict)

    d = sub.add_parser("demo", help="publish, cold fetch, warm fetch, timings")
    d.add_argument("--demo-bytes", type=int, default=8 * MIB)
    d.set_defaults(fn=cmd_demo)

    x = sub.add_parser("experiment", help="the full measured run, through RunLog")
    x.add_argument("--sizes", default=f"{MIB//16},{MIB},{4*MIB},{16*MIB},{48*MIB}",
                   help="comma-separated artefact sizes in bytes")
    x.set_defaults(fn=cmd_experiment)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    p = build_parser()
    args = p.parse_args(argv)
    # `experiment` derives its budget from the artefact sizes unless the flag
    # was given explicitly; argparse cannot tell a default from a repeat of it.
    raw = argv if argv is not None else sys.argv[1:]
    args.budget_bytes_given = "--budget-bytes" in raw
    try:
        return args.fn(args)
    except (WeightError, ModelNotResolvable) as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
