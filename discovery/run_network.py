"""Launch an N-node Edge Grid on one machine, as N real OS processes.

This is the harness behind Experiment 2 (auction convergence time, 3 -> 5 nodes).
Nodes are separate processes on purpose: threads in one interpreter would share a
trio clock and a GIL and would flatter the numbers. Each node here has its own
libp2p host, its own DHT, its own gossipsub heartbeat and its own scheduler,
which is what the deployed system looks like.

Topology is a star: node 0 is the requester and the bootstrap peer, providers 1..N-1
dial it and never dial each other. That is deliberate - it means a provider's DHT
lookup of another provider's record *cannot* be answered locally and has to be
routed through the bootstrap peer, which is the only way to show the DHT is doing
real work rather than echoing back values the node put there itself.

Timings recorded, all stamped inside the node processes rather than at the reading
end of a pipe:

  mesh_ready_ms          first spawn -> requester's mesh reached N-1 peers
  first_bid_ms/last_bid_ms   job published -> first / last bid at the requester
  broadcast_to_award_ms  job published -> award published (the headline number)
  auction_ms             the same interval as measured by the node itself

`broadcast_to_award_ms` necessarily includes the fixed `--bid-window`, since the
requester waits out the whole window by design. `last_bid_ms` is the part that
actually varies with node count, so both are reported.

Usage:
    python -m discovery.run_network --nodes 3 5 --repeats 3
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from libp2p.crypto.secp256k1 import create_new_key_pair
from libp2p.peer.id import ID

from edgegrid import config as C
from edgegrid.identity import Identity
from edgegrid.runlog import RunLog

DEFAULT_KEY_DIR = Path.home() / ".edgegrid" / "run_network"


def peer_id_for(name: str, key_dir: Path) -> str:
    """The PeerID a node will have, computed without starting it.

    Only possible because the libp2p key is derived from the persisted secp256k1
    identity, so it is stable across restarts - which is what lets the launcher
    write the bootstrap multiaddr before the bootstrap node exists."""
    ident = Identity.load_or_create(name, key_dir)
    return str(ID.from_pubkey(create_new_key_pair(ident.seed_bytes).public_key))


class NodeProc:
    """One node subprocess plus the structured events it has emitted."""

    def __init__(self, name: str, argv: list[str], log_dir: Path, env: dict):
        self.name = name
        self.argv = argv
        self.events: list[dict] = []
        self.unparsed_stdout = 0
        self.lock = threading.Lock()
        self.stderr_path = log_dir / f"{name}.stderr.log"
        self._err = self.stderr_path.open("w")
        self.proc = subprocess.Popen(
            argv, stdout=subprocess.PIPE, stderr=self._err,
            text=True, bufsize=1, env=env, cwd=str(C.REPO_ROOT))
        self.spawned_at = time.time()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        """Node stdout is a stream of one JSON event per line. Anything else is
        a library or interpreter message that has escaped onto the wrong stream,
        and it is counted rather than discarded - silently swallowing it is how
        a node that is failing looks identical to a node that is quiet."""
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                if not isinstance(ev, dict):
                    raise ValueError("not an object")
            except (json.JSONDecodeError, ValueError):
                with self.lock:
                    self.unparsed_stdout += 1
                continue
            with self.lock:
                self.events.append(ev)

    def snapshot(self) -> list[dict]:
        with self.lock:
            return list(self.events)

    def find(self, kind: str) -> Optional[dict]:
        for ev in self.snapshot():
            if ev.get("event") == kind:
                return ev
        return None

    def all_of(self, kind: str) -> list[dict]:
        return [e for e in self.snapshot() if e.get("event") == kind]

    def wait_for(self, kind: str, timeout_s: float) -> Optional[dict]:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            ev = self.find(kind)
            if ev is not None:
                return ev
            if self.proc.poll() is not None:
                return self.find(kind)
            time.sleep(0.05)
        return self.find(kind)

    def stop(self) -> None:
        """Terminate and record how the process actually ended.

        `exited_early` is the one that matters: a provider that died after
        reporting ready simply stops bidding, and without this the run just
        shows a thinner auction with no reason attached to it."""
        self.exited_early = self.proc.poll() is not None
        if not self.exited_early:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=5)
        self.returncode = self.proc.returncode
        self._err.close()
        self.stderr_bytes = self.stderr_path.stat().st_size

    def health(self) -> dict:
        """What the process itself did, independent of what it said on stdout."""
        with self.lock:
            unparsed = self.unparsed_stdout
        return {"name": self.name, "returncode": self.returncode,
                "exited_early": self.exited_early,
                "stderr_bytes": self.stderr_bytes,
                "stderr_log": str(self.stderr_path),
                "unparsed_stdout": unparsed}


def _count_drops(proc: NodeProc, job_id: str) -> dict:
    """Messages the requester rejected before they ever reached the auction,
    by reason. A forged bid never becomes a cheap bid; it becomes a counter."""
    counts: dict[str, int] = {}
    for ev in proc.all_of("dropped"):
        if ev.get("job_id") not in (job_id, None):
            continue
        key = f"{ev.get('kind')}:{ev.get('why')}"
        counts[key] = counts.get(key, 0) + 1
    return counts


def run_once(n_nodes: int, repeat: int, args: argparse.Namespace,
             log_dir: Path) -> dict:
    """Bring up one N-node network, run one auction, tear it down."""
    key_dir = Path(args.key_dir)
    names = [f"eg{n_nodes}-{i}" for i in range(n_nodes)]
    peer_ids = [peer_id_for(nm, key_dir) for nm in names]
    ports = [args.base_port + i for i in range(n_nodes)]
    bootstrap = f"/ip4/127.0.0.1/tcp/{ports[0]}/p2p/{peer_ids[0]}"

    env = dict(os.environ)
    env["PYTHONPATH"] = str(C.REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONUNBUFFERED"] = "1"

    def base_argv(i: int) -> list[str]:
        argv = [sys.executable, "-m", "discovery.node",
                "--name", names[i], "--port", str(ports[i]),
                "--key-dir", str(key_dir), "--model", args.model,
                "--advertise-ip", "127.0.0.1",
                "--bid-window", str(args.bid_window),
                "--ttl", str(args.ttl),
                "--wait-peers-timeout", str(args.timeout)]
        for pid in peer_ids:
            if pid != peer_ids[i]:
                argv += ["--dht-probe", pid]
        return argv

    procs: list[NodeProc] = []
    t_spawn = time.time()
    result: dict = {"n_nodes": n_nodes, "repeat": repeat, "ok": False,
                    "error": None, "peer_ids": peer_ids}
    try:
        # Requester / bootstrap first: providers need its multiaddr to dial.
        req_argv = base_argv(0) + [
            "--role", "requester", "--wait-peers", str(n_nodes - 1),
            "--job-after", str(args.settle_s), "--prompt", args.prompt,
            "--max-price", str(args.max_price),
            "--max-latency-ms", str(args.max_latency_ms)]
        procs.append(NodeProc(names[0], req_argv, log_dir, env))
        if procs[0].wait_for("ready", args.timeout) is None:
            raise RuntimeError("bootstrap node never reported ready")

        for i in range(1, n_nodes):
            price = round(args.base_price + i * args.price_step, 6)
            ttft = args.base_ttft_ms + i * args.ttft_step_ms
            argv = base_argv(i) + [
                "--role", "provider", "--bootstrap", bootstrap,
                "--price", str(price), "--ttft-ms", str(ttft),
                "--wait-peers", "1"]
            if i in args.warm_nodes:
                argv.append("--warm")
            if i in args.forge_nodes:
                argv.append("--forge-bids")
            procs.append(NodeProc(names[i], argv, log_dir, env))

        for p in procs[1:]:
            if p.wait_for("ready", args.timeout) is None:
                raise RuntimeError(f"{p.name} never reported ready")

        mesh_ev = procs[0].wait_for("mesh", args.timeout)
        if mesh_ev is None or not mesh_ev.get("reached"):
            raise RuntimeError(f"requester mesh never reached {n_nodes - 1} peers: {mesh_ev}")

        closed = procs[0].wait_for("auction_closed", args.timeout)
        if closed is None:
            raise RuntimeError("auction never closed")

        published = procs[0].find("job_published")
        bids = [e for e in procs[0].all_of("bid_received")
                if e["job_id"] == closed["job_id"]]
        t0 = published["ts_ms"]
        result.update({
            "ok": True,
            "job_id": closed["job_id"],
            "mesh_ready_ms": round((mesh_ev["ts_ms"] / 1000.0 - t_spawn) * 1000.0, 1),
            "mesh_peers": mesh_ev["mesh"],
            "routing_table": mesh_ev["routing_table"],
            "n_received": closed["n_received"],
            "n_eligible": closed["n_eligible"],
            "n_accounted": closed.get("n_accounted", ""),
            "rejected": closed["rejected"],
            "first_bid_ms": min((e["ts_ms"] - t0 for e in bids), default=None),
            "last_bid_ms": max((e["ts_ms"] - t0 for e in bids), default=None),
            "broadcast_to_award_ms": closed["ts_ms"] - t0,
            "auction_ms": closed["auction_ms"],
            "ranked": closed["bids"],
            "award": closed["award"],
            "dropped_by_requester": _count_drops(procs[0], closed["job_id"]),
        })
        # Give the award a moment to reach the winner before tearing down.
        # `winner_ack` is always a definite True/False for an auction that
        # produced an award: leaving it blank would read as "did not ack".
        winner = (closed.get("award") or {}).get("winner_peer_id")
        if not winner:
            # The auction closed with no eligible bid. That is a legitimate
            # outcome, not an acknowledged one - say so rather than leaving the
            # column blank, which reads the same as "the winner stayed silent".
            result["winner_ack"] = "no_award"
        else:
            match = next((p for p in procs[1:]
                          if (p.find("ready") or {}).get("peer_id") == winner), None)
            if match is None:
                result["winner_ack"] = False
                raise RuntimeError(f"award winner {winner[:16]} is not one of the "
                                   "launched providers")
            result["winner_ack"] = match.wait_for("won", 5.0) is not None
            if not result["winner_ack"]:
                raise RuntimeError(f"winner {winner[:16]} never acknowledged the award")
    except Exception as exc:
        result["ok"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        events = {p.name: p.snapshot() for p in procs}
        ready = {p.name: p.find("ready") for p in procs}
        lookups = {p.name: p.all_of("dht_lookup") for p in procs}
        for p in procs:
            p.stop()
        health = [p.health() for p in procs]
        result["events"] = events
        result["ready"] = ready
        result["dht_lookups"] = lookups
        result["node_health"] = health
        # A node that crashed, exited before teardown, wrote to stderr, or put
        # non-JSON on stdout is a caveat on every number in this row. The run is
        # not failed for it - stderr here is usually a libp2p miss warning - but
        # it is carried into the CSV so no reader has to assume it was clean.
        result["nodes_exited_early"] = sum(1 for h in health if h["exited_early"])
        result["nodes_nonzero_exit"] = sum(
            1 for h in health if h["returncode"] not in (0, -15))
        result["stderr_bytes"] = sum(h["stderr_bytes"] for h in health)
        result["unparsed_stdout"] = sum(h["unparsed_stdout"] for h in health)
    return result


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="discovery.run_network", description=__doc__.splitlines()[0])
    p.add_argument("--nodes", type=int, nargs="+", default=[3, 4, 5],
                   help="node counts to run, e.g. --nodes 3 4 5")
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--base-port", type=int, default=41100)
    p.add_argument("--key-dir", default=str(DEFAULT_KEY_DIR))
    p.add_argument("--model", default=C.OLLAMA_MODEL)
    p.add_argument("--prompt", default="What is the capital of France? Answer in one word.")
    p.add_argument("--bid-window", type=float, default=C.BID_WINDOW_S)
    p.add_argument("--settle-s", type=float, default=0.5,
                   help="pause between the mesh forming and the job going out")
    p.add_argument("--base-price", type=float, default=0.05)
    p.add_argument("--price-step", type=float, default=0.02)
    p.add_argument("--base-ttft-ms", type=float, default=1200.0)
    p.add_argument("--ttft-step-ms", type=float, default=100.0)
    p.add_argument("--max-price", type=float, default=0.20,
                   help="requester reserve; also the clearing price if only one bid arrives")
    p.add_argument("--max-latency-ms", type=int, default=30_000)
    p.add_argument("--warm-nodes", type=int, nargs="*", default=[],
                   help="indices of providers to mark warm (exercises WARM_START_BONUS)")
    p.add_argument("--forge-nodes", type=int, nargs="*", default=[],
                   help="indices of providers that sign bids with the wrong key "
                        "(exercises the requester's signature rejection path)")
    p.add_argument("--timeout", type=float, default=45.0)
    p.add_argument("--ttl", type=float, default=90.0, help="node self-exit timer")
    p.add_argument("--experiment", default="exp2-auction-convergence")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    params = {k: v for k, v in vars(args).items()}
    failures = 0

    with RunLog(args.experiment, params) as log:
        (log.dir / "node-logs").mkdir(exist_ok=True)
        for n in args.nodes:
            for r in range(args.repeats):
                print(f"[run_network] n={n} repeat={r} ...", flush=True)
                res = run_once(n, r, args, log.dir / "node-logs")
                log.write_json(f"events-n{n}-r{r}", res.pop("events"))

                if not res["ok"] or res.get("error"):
                    failures += 1
                    log.drop(f"n={n} r={r}", res["error"] or "unknown")
                    print(f"[run_network] FAILED n={n} r={r}: {res['error']}", flush=True)
                # Caveats that do not fail the run but must not be invisible.
                if res.get("nodes_nonzero_exit"):
                    log.drop(f"n={n} r={r}",
                             f"{res['nodes_nonzero_exit']} node(s) exited non-zero")
                if res.get("unparsed_stdout"):
                    log.drop(f"n={n} r={r}",
                             f"{res['unparsed_stdout']} non-JSON stdout line(s) from nodes")

                health = {h["name"]: h for h in (res.pop("node_health") or [])}
                for name, ready in (res.pop("ready") or {}).items():
                    h = health.get(name, {})
                    if ready:
                        log.append("nodes", {
                            "n_nodes": n, "repeat": r, "name": name,
                            "role": ready["role"], "peer_id": ready["peer_id"],
                            "wallet": ready["wallet"], "price": ready["price"],
                            "warm": ready["warm"], "model": ready["model"],
                            "tier": ready["tier"], "ttft_ms": ready.get("ttft_ms", ""),
                            "forge_bids": ready["forge_bids"],
                            "mesh": ready["mesh"], "meshed": ready["meshed"],
                            "bootstrap_ok": ready.get("bootstrap_ok", ""),
                            "bootstrap_failed": ready.get("bootstrap_failed", ""),
                            "routing_table": ready["routing_table"],
                            "returncode": h.get("returncode", ""),
                            "exited_early": h.get("exited_early", ""),
                            "stderr_bytes": h.get("stderr_bytes", ""),
                            "unparsed_stdout": h.get("unparsed_stdout", "")})
                    else:
                        # No ready event at all: the node never came up. Without
                        # this the node is simply absent from nodes.csv and N
                        # silently shrinks.
                        log.drop(f"n={n} r={r}", f"{name} never reported ready "
                                                 f"(returncode={h.get('returncode')})")

                for prober, rows in (res.pop("dht_lookups") or {}).items():
                    for row in rows:
                        log.append("dht_lookups", {
                            "n_nodes": n, "repeat": r, "prober": prober,
                            "peer": row["peer"], "source": row["source"],
                            "ok": row["ok"], "attempts": row.get("attempts", ""),
                            "wallet": row["wallet"],
                            "models": ";".join(row["models"] or [])})

                award = res.get("award") or {}
                log.append("auctions", {
                    "n_nodes": n, "repeat": r, "ok": res["ok"],
                    "error": res.get("error") or "",
                    "model": args.model, "max_price": args.max_price,
                    "bid_window_s": args.bid_window,
                    "warm_start_bonus": C.WARM_START_BONUS,
                    "job_id": res.get("job_id", ""),
                    "mesh_ready_ms": res.get("mesh_ready_ms", ""),
                    "n_received": res.get("n_received", ""),
                    "n_eligible": res.get("n_eligible", ""),
                    "n_accounted": res.get("n_accounted", ""),
                    "first_bid_ms": res.get("first_bid_ms", ""),
                    "last_bid_ms": res.get("last_bid_ms", ""),
                    "broadcast_to_award_ms": res.get("broadcast_to_award_ms", ""),
                    "auction_ms": res.get("auction_ms", ""),
                    "winner_peer_id": award.get("winner_peer_id", ""),
                    "winning_bid_price": award.get("winning_bid_price", ""),
                    "clearing_price": award.get("clearing_price", ""),
                    "winner_ack": res.get("winner_ack", ""),
                    "rejected": json.dumps(res.get("rejected", {})),
                    "dropped_at_wire": json.dumps(res.get("dropped_by_requester", {})),
                    "nodes_exited_early": res.get("nodes_exited_early", ""),
                    "nodes_nonzero_exit": res.get("nodes_nonzero_exit", ""),
                    "stderr_bytes": res.get("stderr_bytes", ""),
                    "unparsed_stdout": res.get("unparsed_stdout", ""),
                })
                for rank, b in enumerate(res.get("ranked") or []):
                    log.append("bids", {
                        "n_nodes": n, "repeat": r, "job_id": res.get("job_id", ""),
                        "rank": rank, "bidder": b["peer"], "price": b["price"],
                        "effective": b["effective"], "warm": b["warm"],
                        "ttft_ms": b["ttft_ms"]})

                if res["ok"]:
                    print(f"[run_network] n={n} r={r} "
                          f"bids={res['n_eligible']} "
                          f"winner={award.get('winner_peer_id','')[:16]}... "
                          f"bid={award.get('winning_bid_price')} "
                          f"clearing={award.get('clearing_price')} "
                          f"broadcast->award={res['broadcast_to_award_ms']}ms "
                          f"last_bid={res['last_bid_ms']}ms", flush=True)

        print(f"[run_network] results in {log.dir}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
