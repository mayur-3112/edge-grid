"""Launch an N-node Edge Grid as N containers on a user-defined Docker bridge.

`discovery/run_network.py` runs N nodes as N OS processes on one host. Every one
of them binds the same loopback interface, so "the network" between peers is a
kernel memcpy: no bridge, no routing decision, no interface queue, one address
shared by all N. That is the largest single threat to the validity of every
timing in Chapter 8, and this module exists to remove the part of it that can be
removed on one machine.

Each node here gets its own network namespace, its own address on a private /24,
and traffic between peers crosses a real Linux bridge with real interface queues
that `tc netem` can be attached to. Concretely, what changes versus run_network:

  * distinct routable addresses instead of one shared 127.0.0.1, proven per run
    by `docker network inspect` (written to network-n<N>-r<R>.json),
  * every node binds the SAME port (4001) - impossible on shared loopback, and
    the cleanest evidence that the namespaces really are separate,
  * packets traverse veth pairs and the bridge's forwarding path, not the
    loopback shortcut,
  * `--latency-ms` can shape the link, which loopback co-processes cannot do
    per-peer.

What does NOT change, and must not be claimed: this is one kernel, one CPU, one
host, no wide-area path, no physical NIC, no independent clocks. It is a
materially stronger topology than shared loopback and materially weaker than
four machines on a LAN. See deploy/grid/README.md.

The measurements are deliberately identical to run_network's so the two are
directly comparable, and `--compare-single-host` runs both back to back and
writes the paired table:

  mesh_ready_ms          bootstrap container started -> requester's mesh reached N-1
  first_bid_ms/last_bid_ms   job published -> first / last bid at the requester
  broadcast_to_award_ms  job published -> award published
  auction_ms             the same interval as measured by the node itself

One caveat on mesh_ready_ms and only mesh_ready_ms: it is measured from the
moment `docker compose start` is issued for the bootstrap container, so it
carries container start-up cost that the process launcher does not have.
`container_create_ms` and `bootstrap_start_ms` are recorded beside it so a
reader can see how much. The other three metrics are measured entirely between
timestamps taken inside already-running nodes and carry no container overhead at
all, which is why they are the ones the comparison leans on.

Usage:
    python -m discovery.run_swarm --build --nodes 3 5 --repeats 3
    python -m discovery.run_swarm --nodes 3 --latency-ms 25
    python -m discovery.run_swarm --nodes 3 4 5 --repeats 3 --compare-single-host
    python -m discovery.run_swarm --write-compose      # regenerate the example
"""

from __future__ import annotations

import argparse
import csv
import ipaddress
import json
import os
import shutil
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from discovery.run_network import peer_id_for
from edgegrid import config as C
from edgegrid.runlog import RunLog

DEPLOY_DIR = C.REPO_ROOT / "deploy" / "grid"
DOCKERFILE = DEPLOY_DIR / "Dockerfile"
EXAMPLE_COMPOSE = DEPLOY_DIR / "compose.yml"
DEFAULT_KEY_DIR = Path.home() / ".edgegrid" / "run_swarm"
DEFAULT_IMAGE = "edgegrid-node:dev"
DEFAULT_PROJECT = "edgegrid-swarm"
DEFAULT_SUBNET = "10.77.0.0/24"
DEFAULT_IP_OFFSET = 10
DEFAULT_PORT = 4001

# The bootstrap multiaddr in the checked-in example cannot contain a real PeerID
# - that would mean committing a key. The example takes it from the environment
# instead, and `--print-bootstrap` prints the value to use.
BOOTSTRAP_PLACEHOLDER = (
    "${EG_BOOTSTRAP:?set EG_BOOTSTRAP - get it from: "
    "python -m discovery.run_swarm --print-bootstrap}")

METRICS = [
    ("first_bid_ms", "first bid (ms)"),
    ("last_bid_ms", "last bid (ms)"),
    ("broadcast_to_award_ms", "broadcast -> award (ms)"),
    ("mesh_ready_ms", "mesh ready (ms)"),
]


# --------------------------------------------------------------------------
# compose generation
# --------------------------------------------------------------------------

_BARE_KEY = ("abcdefghijklmnopqrstuvwxyz"
             "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-")


def _yaml_scalar(v: Any) -> str:
    """One YAML scalar.

    Strings go through json.dumps, whose output is a valid YAML double-quoted
    scalar - which sidesteps every YAML quoting trap at once (the `${VAR:?...}`
    bootstrap placeholder, `0.0.0.0`, `3s`, values beginning with `-`)."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return json.dumps(v)
    return json.dumps(str(v))


def _yaml_key(k: str) -> str:
    return k if k and all(c in _BARE_KEY for c in k) else json.dumps(k)


def _yaml_emit(lines: list[str], key: str, value: Any, indent: int) -> None:
    """Block-style YAML for the restricted shape `compose_spec` produces.

    Hand-rolled rather than PyYAML because PyYAML is not in the repo's
    requirements.txt, and a launcher that only works when some other package
    happened to pull in a transitive dependency is exactly the kind of silent
    coupling this repo avoids. The shape emitted is small and closed: nested
    maps, lists of scalars, and one list of single-key maps."""
    pad = "  " * indent
    if isinstance(value, dict):
        if not value:
            lines.append(f"{pad}{key}: {{}}")
            return
        lines.append(f"{pad}{key}:")
        for k, v in value.items():
            _yaml_emit(lines, _yaml_key(k), v, indent + 1)
    elif isinstance(value, list):
        if not value:
            lines.append(f"{pad}{key}: []")
            return
        lines.append(f"{pad}{key}:")
        for item in value:
            if isinstance(item, dict):
                sub: list[str] = []
                for k, v in item.items():
                    _yaml_emit(sub, _yaml_key(k), v, indent + 2)
                if isinstance(next(iter(item.values())), (dict, list)):
                    raise ValueError(
                        "list items whose first value is a collection are not "
                        f"supported by this emitter: {item!r}")
                sub[0] = "  " * (indent + 1) + "- " + sub[0].lstrip()
                lines.extend(sub)
            else:
                lines.append(f"{pad}  - {_yaml_scalar(item)}")
    else:
        lines.append(f"{pad}{key}: {_yaml_scalar(value)}")


def to_yaml(doc: dict) -> str:
    lines: list[str] = []
    for k, v in doc.items():
        _yaml_emit(lines, _yaml_key(k), v, 0)
    return "\n".join(lines) + "\n"


def node_ips(n_nodes: int, subnet: str = DEFAULT_SUBNET,
             ip_offset: int = DEFAULT_IP_OFFSET) -> list[str]:
    """Fixed addresses for N nodes, offset past the bridge's own gateway.

    Docker takes .1 of the subnet for the bridge gateway, so the offset must
    leave room for it; anything below 2 is rejected rather than silently
    colliding with the gateway and producing an unexplained start failure."""
    if ip_offset < 2:
        raise ValueError(f"ip_offset must be >= 2 (.1 is the bridge gateway), got {ip_offset}")
    net = ipaddress.ip_network(subnet)
    hosts = list(net.hosts())
    if ip_offset - 1 + n_nodes > len(hosts):
        raise ValueError(f"{subnet} cannot hold {n_nodes} nodes at offset {ip_offset}")
    return [str(hosts[ip_offset - 1 + i]) for i in range(n_nodes)]


def compose_spec(n_nodes: int, *,
                 image: str = DEFAULT_IMAGE,
                 project: str = DEFAULT_PROJECT,
                 subnet: str = DEFAULT_SUBNET,
                 ip_offset: int = DEFAULT_IP_OFFSET,
                 port: int = DEFAULT_PORT,
                 key_dir: str = "./keys",
                 peer_ids: Optional[list[str]] = None,
                 model: str = C.OLLAMA_MODEL,
                 ollama_host: str = "http://host.docker.internal:11434",
                 prompt: str = "What is the capital of France? Answer in one word.",
                 bid_window: float = C.BID_WINDOW_S,
                 settle_s: float = 0.5,
                 ttl: float = 90.0,
                 wait_peers_timeout: float = 45.0,
                 base_price: float = 0.05,
                 price_step: float = 0.02,
                 base_ttft_ms: float = 1200.0,
                 ttft_step_ms: float = 100.0,
                 max_price: float = 0.20,
                 max_latency_ms: int = 30_000,
                 latency_ms: float = 0.0,
                 cap_net_admin: Optional[bool] = None,
                 warm_nodes: tuple[int, ...] = (),
                 forge_nodes: tuple[int, ...] = ()) -> dict:
    """The compose document for an N-container swarm, as a plain dict.

    Same star topology and same per-node price/TTFT ladder as run_network, so
    the two harnesses differ in transport and nothing else: node 0 is the
    requester and the bootstrap peer, providers 1..N-1 dial it and never dial
    each other, which keeps the DHT lookups genuinely remote.

    `peer_ids=None` renders the committed example: no PeerID is known without
    reading a private key, so the bootstrap address comes from the environment
    and the DHT probes (which are addressed by PeerID) are omitted."""
    if n_nodes < 2:
        raise ValueError(f"a swarm needs at least a requester and one provider, got {n_nodes}")
    if peer_ids is not None and len(peer_ids) != n_nodes:
        raise ValueError(f"got {len(peer_ids)} peer ids for {n_nodes} nodes")
    ips = node_ips(n_nodes, subnet, ip_offset)
    # NET_ADMIN is granted only when shaping is actually requested. Handing every
    # container the capability by default would be a permission the experiment
    # does not need, and would hide the "netem was skipped" path that the
    # entrypoint exists to report.
    if cap_net_admin is None:
        cap_net_admin = latency_ms > 0

    if peer_ids is None:
        bootstrap = BOOTSTRAP_PLACEHOLDER
    else:
        bootstrap = f"/ip4/{ips[0]}/tcp/{port}/p2p/{peer_ids[0]}"

    services: dict = {}
    for i in range(n_nodes):
        name = f"node{i}"
        cmd = [
            "--name", f"eg{n_nodes}c-{i}",
            "--port", str(port),
            "--key-dir", "/keys",
            "--model", model,
            "--listen-ip", "0.0.0.0",
            "--advertise-ip", ips[i],
            "--bid-window", str(bid_window),
            "--ttl", str(ttl),
            "--wait-peers-timeout", str(wait_peers_timeout),
        ]
        if peer_ids is not None:
            for j, pid in enumerate(peer_ids):
                if j != i:
                    cmd += ["--dht-probe", pid]
        if i == 0:
            cmd += ["--role", "requester",
                    "--wait-peers", str(n_nodes - 1),
                    "--job-after", str(settle_s),
                    "--prompt", prompt,
                    "--max-price", str(max_price),
                    "--max-latency-ms", str(max_latency_ms)]
        else:
            cmd += ["--role", "provider",
                    "--bootstrap", bootstrap,
                    "--price", str(round(base_price + i * price_step, 6)),
                    "--ttft-ms", str(base_ttft_ms + i * ttft_step_ms),
                    "--wait-peers", "1"]
            if i in warm_nodes:
                cmd.append("--warm")
            if i in forge_nodes:
                cmd.append("--forge-bids")

        svc: dict = {
            "image": image,
            "container_name": f"{project}-{name}",
            "hostname": name,
            "init": True,
            # Ollama runs on the host, not in the swarm. host-gateway is the
            # only portable way for a bridged container to name the host.
            "extra_hosts": ["host.docker.internal:host-gateway"],
            "environment": {
                "EG_NAME": name,
                "EG_ADVERTISE_IP": ips[i],
                "EG_NETEM_MS": str(latency_ms),
                "OLLAMA_HOST": ollama_host,
                "OLLAMA_MODEL": model,
            },
            # Read-only: identities are generated on the host and the container
            # is never able to mint one, so a container cannot quietly acquire a
            # wallet the launcher does not know about.
            "volumes": [f"{key_dir}:/keys:ro"],
            "networks": {"gridnet": {"ipv4_address": ips[i]}},
            "command": cmd,
            "stop_grace_period": "3s",
        }
        if cap_net_admin:
            svc["cap_add"] = ["NET_ADMIN"]
        services[name] = svc

    return {
        "name": project,
        "services": services,
        "networks": {
            "gridnet": {
                "name": f"{project}-net",
                "driver": "bridge",
                "ipam": {"config": [{"subnet": subnet}]},
            }
        },
    }


def render_compose(n_nodes: int, **kwargs) -> str:
    """`compose_spec` as YAML text, with a provenance header."""
    spec = compose_spec(n_nodes, **kwargs)
    header = (
        "# GENERATED FILE - do not edit by hand.\n"
        "#\n"
        "#   python -m discovery.run_swarm --write-compose\n"
        "#\n"
        "# regenerates it, and tests/test_swarm.py::test_committed_compose_matches_generator\n"
        "# fails if this file and discovery/run_swarm.py have drifted apart.\n"
        "#\n"
        f"# {n_nodes} Edge Grid nodes, each in its own network namespace with its own\n"
        f"# address on the {spec['networks']['gridnet']['ipam']['config'][0]['subnet']} bridge. Every node binds the same TCP port,\n"
        "# which is the point: on shared loopback that is impossible.\n"
        "#\n"
        "# This example does not carry a bootstrap PeerID, because a PeerID cannot be\n"
        "# known without a private key and no key is committed. Create the identities and\n"
        "# print the address first:\n"
        "#\n"
        "#   python -m discovery.run_swarm --write-keys ./deploy/grid/keys --nodes 3\n"
        "#   export EG_BOOTSTRAP=\"$(python -m discovery.run_swarm --print-bootstrap --nodes 3 \\\n"
        "#                            --key-dir ./deploy/grid/keys)\"\n"
        "#   docker compose -f deploy/grid/compose.yml up\n"
        "#\n"
        "# For measurements use discovery/run_swarm.py instead - it sequences the\n"
        "# bootstrap ahead of the providers, collects each container's JSON event stream\n"
        "# and writes a RunLog. `docker compose up` starts everything at once, and\n"
        "# discovery.node does not retry its bootstrap dial.\n"
    )
    return header + to_yaml(spec)


def write_example_compose(path: Path = EXAMPLE_COMPOSE, n_nodes: int = 3) -> Path:
    """Regenerate the committed example. Kept in one place so the example and
    the launcher can never render different topologies."""
    path.write_text(render_compose(n_nodes, key_dir="./keys"))
    return path


# --------------------------------------------------------------------------
# docker plumbing
# --------------------------------------------------------------------------

class DockerUnavailable(RuntimeError):
    """Docker is not usable. Raised rather than degrading to a mock swarm."""


def docker_ok() -> tuple[bool, str]:
    """Whether a docker daemon is reachable, and what it said if not."""
    if shutil.which("docker") is None:
        return False, "docker executable not on PATH"
    try:
        out = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"],
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if out.returncode != 0:
        return False, (out.stderr or out.stdout).strip()[:400]
    return True, out.stdout.strip()


def build_image(image: str = DEFAULT_IMAGE) -> dict:
    """Build the node image from the repo root. Returns timing and the digest."""
    t0 = time.time()
    cmd = ["docker", "build", "-f", str(DOCKERFILE), "-t", image, str(C.REPO_ROOT)]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if out.returncode != 0:
        raise DockerUnavailable(f"image build failed:\n{out.stderr[-4000:]}")
    ident = subprocess.run(["docker", "image", "inspect", image, "--format", "{{.Id}}"],
                           capture_output=True, text=True, timeout=30)
    return {"image": image, "build_s": round(time.time() - t0, 2),
            "image_id": ident.stdout.strip()}


def _dc(compose_file: Path, project: str, *args: str, timeout: float = 300,
        check: bool = True) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose", "-f", str(compose_file), "-p", project, *args]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and out.returncode != 0:
        raise DockerUnavailable(
            f"`{' '.join(cmd)}` failed ({out.returncode}):\n{(out.stderr or out.stdout)[-4000:]}")
    return out


def _dc_safe(notes: list[str], compose_file: Path, project: str, *args: str,
             timeout: float = 300) -> None:
    """A compose call whose failure must not destroy the run it is cleaning up.

    Teardown genuinely does hang sometimes - `down` removing the bridge has been
    observed to block for minutes on a busy daemon - and a measured auction must
    not be thrown away because the cleanup after it was slow. The failure is
    appended to `notes`, which travel into the results row, so a leaked network
    is visible rather than merely absent."""
    what = " ".join(args)
    try:
        out = subprocess.run(
            ["docker", "compose", "-f", str(compose_file), "-p", project, *args],
            capture_output=True, text=True, timeout=timeout)
        if out.returncode != 0:
            notes.append(f"{what}: rc={out.returncode} {(out.stderr or out.stdout).strip()[:200]}")
    except (subprocess.TimeoutExpired, OSError) as exc:
        notes.append(f"{what}: {type(exc).__name__} after {timeout}s")


def _force_remove_network(notes: list[str], network: str) -> None:
    """Last-resort bridge removal after a `down` that did not finish.

    Left behind, the network is reused by the next run at the same node count
    and the two runs share a bridge that was never cleanly torn down - which is
    exactly the kind of unrecorded state that makes a repeat non-comparable."""
    try:
        out = subprocess.run(["docker", "network", "rm", network],
                             capture_output=True, text=True, timeout=60)
        if out.returncode == 0:
            notes.append(f"network {network} removed by force after teardown trouble")
        elif "not found" not in (out.stderr or "").lower():
            notes.append(f"network rm {network}: {(out.stderr or '').strip()[:200]}")
    except (subprocess.TimeoutExpired, OSError) as exc:
        notes.append(f"network rm {network}: {type(exc).__name__}")


class SwarmContainer:
    """One container plus the JSON event stream it has emitted.

    `docker logs --follow` replays the whole log before following, so attaching
    after the container has started loses nothing - which is what lets the
    providers all be started in one compose call and read afterwards.

    stdout and stderr stay separated because the container has no TTY: docker
    demultiplexes them back onto our own two streams. Anything on stdout that is
    not a JSON object is counted, never discarded, for the same reason
    run_network counts it: a node that is failing must not look like one that is
    merely quiet."""

    def __init__(self, service: str, container: str, log_dir: Path):
        self.name = service
        self.container = container
        self.events: list[dict] = []
        self.unparsed_stdout = 0
        self.lock = threading.Lock()
        self.stderr_path = log_dir / f"{service}.stderr.log"
        self._err = self.stderr_path.open("w")
        self.proc: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None
        self.returncode: Optional[int] = None
        self.exited_early = False
        self.oom_killed = False
        self.state = "unknown"
        self.stderr_bytes = 0

    def follow(self) -> None:
        self.proc = subprocess.Popen(
            ["docker", "logs", "--follow", self.container],
            stdout=subprocess.PIPE, stderr=self._err, text=True, bufsize=1)
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        assert self.proc is not None and self.proc.stdout is not None
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
            time.sleep(0.05)
        return self.find(kind)

    def inspect(self) -> None:
        """Record how the container itself ended, independent of what it said.

        A container that exited before teardown stops bidding, and without this
        the run just shows a thinner auction with no reason attached."""
        out = subprocess.run(
            ["docker", "inspect", "--format",
             "{{.State.Status}}|{{.State.ExitCode}}|{{.State.OOMKilled}}",
             self.container], capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            self.state = f"inspect_failed: {(out.stderr or '').strip()[:120]}"
            return
        status, code, oom = out.stdout.strip().split("|")
        self.state = status
        self.returncode = int(code)
        self.oom_killed = oom.strip().lower() == "true"

    def close(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self._reader is not None:
            self._reader.join(timeout=5)
        self._err.close()
        self.stderr_bytes = self.stderr_path.stat().st_size

    def health(self) -> dict:
        with self.lock:
            unparsed = self.unparsed_stdout
        return {"name": self.name, "container": self.container, "state": self.state,
                "returncode": self.returncode, "exited_early": self.exited_early,
                "oom_killed": self.oom_killed, "stderr_bytes": self.stderr_bytes,
                "stderr_log": str(self.stderr_path), "unparsed_stdout": unparsed}


def network_ips(network: str) -> dict:
    """Container -> IPv4 address, straight from `docker network inspect`.

    This is the evidence that the peers really do hold distinct addresses; it is
    read from Docker rather than from our own compose file on purpose, because
    the compose file only states an intention."""
    out = subprocess.run(["docker", "network", "inspect", network],
                         capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        return {"error": (out.stderr or out.stdout).strip()[:400]}
    doc = json.loads(out.stdout)[0]
    return {
        "network": doc.get("Name"),
        "network_id": doc.get("Id", "")[:12],
        "driver": doc.get("Driver"),
        "subnet": (doc.get("IPAM", {}).get("Config") or [{}])[0].get("Subnet"),
        "gateway": (doc.get("IPAM", {}).get("Config") or [{}])[0].get("Gateway"),
        "containers": {c["Name"]: c["IPv4Address"]
                       for c in (doc.get("Containers") or {}).values()},
    }


# --------------------------------------------------------------------------
# one swarm run
# --------------------------------------------------------------------------

def _count_drops(c: SwarmContainer, job_id: str) -> dict:
    counts: dict[str, int] = {}
    for ev in c.all_of("dropped"):
        if ev.get("job_id") not in (job_id, None):
            continue
        key = f"{ev.get('kind')}:{ev.get('why')}"
        counts[key] = counts.get(key, 0) + 1
    return counts


def run_once(n_nodes: int, repeat: int, args: argparse.Namespace,
             log_dir: Path) -> dict:
    """Bring up one N-container swarm, run one auction, tear it down."""
    key_dir = Path(args.key_dir).expanduser().resolve()
    names = [f"eg{n_nodes}c-{i}" for i in range(n_nodes)]
    peer_ids = [peer_id_for(nm, key_dir) for nm in names]
    ips = node_ips(n_nodes, args.subnet, args.ip_offset)
    project = f"{args.project}-n{n_nodes}"
    network = f"{project}-net"

    compose_path = log_dir / f"compose-n{n_nodes}-r{repeat}.yml"
    compose_path.write_text(render_compose(
        n_nodes, image=args.image, project=project, subnet=args.subnet,
        ip_offset=args.ip_offset, port=args.port, key_dir=str(key_dir),
        peer_ids=peer_ids, model=args.model, ollama_host=args.ollama_host,
        prompt=args.prompt, bid_window=args.bid_window, settle_s=args.settle_s,
        ttl=args.ttl, wait_peers_timeout=args.timeout,
        base_price=args.base_price, price_step=args.price_step,
        base_ttft_ms=args.base_ttft_ms, ttft_step_ms=args.ttft_step_ms,
        max_price=args.max_price, max_latency_ms=args.max_latency_ms,
        latency_ms=args.latency_ms,
        cap_net_admin=False if args.no_cap_net_admin else None,
        warm_nodes=tuple(args.warm_nodes), forge_nodes=tuple(args.forge_nodes)))

    result: dict = {"n_nodes": n_nodes, "repeat": repeat, "ok": False, "error": None,
                    "peer_ids": peer_ids, "planned_ips": ips, "project": project,
                    "network": network, "compose_file": str(compose_path),
                    "latency_ms": args.latency_ms}
    containers: list[SwarmContainer] = []
    notes: list[str] = []
    try:
        # A previous run that was killed mid-flight leaves containers and a
        # network behind that would silently join this one.
        _dc_safe(notes, compose_path, project, "down", "-v", "--remove-orphans",
                 "-t", "3", timeout=180)

        t0 = time.time()
        _dc(compose_path, project, "create", "--quiet-pull", timeout=600)
        result["container_create_ms"] = round((time.time() - t0) * 1000, 1)

        containers = [SwarmContainer(f"node{i}", f"{project}-node{i}", log_dir)
                      for i in range(n_nodes)]

        # Sequenced exactly as run_network sequences its processes: the
        # bootstrap must be listening before providers dial, because
        # discovery.node's _connect_bootstrap does not retry.
        t_spawn = time.time()
        _dc(compose_path, project, "start", "node0", timeout=120)
        result["bootstrap_start_ms"] = round((time.time() - t_spawn) * 1000, 1)
        containers[0].follow()
        if containers[0].wait_for("ready", args.timeout) is None:
            raise RuntimeError("bootstrap container never reported ready")

        t_prov = time.time()
        _dc(compose_path, project, "start", *[f"node{i}" for i in range(1, n_nodes)],
            timeout=180)
        result["providers_start_ms"] = round((time.time() - t_prov) * 1000, 1)
        for c in containers[1:]:
            c.follow()
        for c in containers[1:]:
            if c.wait_for("ready", args.timeout) is None:
                raise RuntimeError(f"{c.name} never reported ready")

        net = network_ips(network)
        result["network_inspect"] = net
        addrs = [v.split("/")[0] for v in (net.get("containers") or {}).values()]
        result["distinct_ips"] = len(set(addrs))
        result["observed_ips"] = sorted(addrs)
        if sorted(addrs) != sorted(ips):
            raise RuntimeError(
                f"docker reports addresses {sorted(addrs)}, compose asked for {sorted(ips)}")

        mesh_ev = containers[0].wait_for("mesh", args.timeout)
        if mesh_ev is None or not mesh_ev.get("reached"):
            raise RuntimeError(
                f"requester mesh never reached {n_nodes - 1} peers: {mesh_ev}")

        closed = containers[0].wait_for("auction_closed", args.timeout)
        if closed is None:
            raise RuntimeError("auction never closed")

        published = containers[0].find("job_published")
        bids = [e for e in containers[0].all_of("bid_received")
                if e["job_id"] == closed["job_id"]]
        tpub = published["ts_ms"]
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
            "first_bid_ms": min((e["ts_ms"] - tpub for e in bids), default=None),
            "last_bid_ms": max((e["ts_ms"] - tpub for e in bids), default=None),
            "broadcast_to_award_ms": closed["ts_ms"] - tpub,
            "auction_ms": closed["auction_ms"],
            "ranked": closed["bids"],
            "award": closed["award"],
            "dropped_by_requester": _count_drops(containers[0], closed["job_id"]),
        })

        winner = (closed.get("award") or {}).get("winner_peer_id")
        if not winner:
            result["winner_ack"] = "no_award"
        else:
            match = next((c for c in containers[1:]
                          if (c.find("ready") or {}).get("peer_id") == winner), None)
            if match is None:
                result["winner_ack"] = False
                raise RuntimeError(f"award winner {winner[:16]} is not one of the "
                                   "launched containers")
            result["winner_ack"] = match.wait_for("won", 5.0) is not None
            if not result["winner_ack"]:
                raise RuntimeError(f"winner {winner[:16]} never acknowledged the award")
    except Exception as exc:
        result["ok"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        result["events"] = {c.name: c.snapshot() for c in containers}
        result["ready"] = {c.name: c.find("ready") for c in containers}
        result["dht_lookups"] = {c.name: c.all_of("dht_lookup") for c in containers}
        result["container_events"] = {c.name: c.find("container") for c in containers}
        result["ollama_probes"] = {c.name: c.find("ollama_probe") for c in containers}

        netem = [c.find("netem") for c in containers]
        applied = [e for e in netem if e and e.get("applied")]
        skipped = [e for e in netem if e and not e.get("applied")]
        result["netem_requested_ms"] = args.latency_ms
        result["netem_applied_nodes"] = len(applied)
        result["netem_skipped_nodes"] = len(skipped)
        result["netem_missing_events"] = sum(1 for e in netem if e is None)
        # If shaping was asked for and any container did not get it, that is a
        # caveat on every timing in the row and must travel with it.
        result["netem_skip_reasons"] = sorted({
            f"{e.get('reason', '')}: {e.get('error', '')}".strip(": ")
            for e in skipped if args.latency_ms > 0})
        result["netem_qdiscs"] = sorted({e.get("qdisc", "") for e in applied})

        if containers:
            for c in containers:
                c.inspect()
                c.exited_early = c.state not in ("running",)
            _dc_safe(notes, compose_path, project, "stop", "-t", "3", timeout=180)
            for c in containers:
                c.close()
            health = [c.health() for c in containers]
            result["node_health"] = health
            result["nodes_exited_early"] = sum(1 for h in health if h["exited_early"])
            # 143 is SIGTERM from `compose stop`, the expected way a node ends.
            result["nodes_nonzero_exit"] = sum(
                1 for h in health if h["returncode"] not in (0, 143, None))
            result["stderr_bytes"] = sum(h["stderr_bytes"] for h in health)
            result["unparsed_stdout"] = sum(h["unparsed_stdout"] for h in health)
        if not args.keep:
            before = len(notes)
            _dc_safe(notes, compose_path, project, "down", "-v", "--remove-orphans",
                     "-t", "3", timeout=180)
            if len(notes) > before:
                _force_remove_network(notes, network)
        result["teardown_notes"] = notes
    return result


# --------------------------------------------------------------------------
# single-host baseline + comparison
# --------------------------------------------------------------------------

SINGLE_HOST_EXPERIMENT = "swarm-baseline-single-host"


def run_single_host_baseline(args: argparse.Namespace) -> tuple[Optional[Path], str]:
    """Run discovery.run_network with matched parameters, as a subprocess.

    Invoked rather than imported so the baseline is the same code path a reader
    would run themselves, and so a crash in it cannot take this process with
    it. Returns the run directory it produced, or None plus the reason."""
    before = {p.name for p in C.RESULTS_DIR.glob(f"{SINGLE_HOST_EXPERIMENT}-*")}
    cmd = [sys.executable, "-m", "discovery.run_network",
           "--nodes", *[str(n) for n in args.nodes],
           "--repeats", str(args.repeats),
           "--model", args.model,
           "--prompt", args.prompt,
           "--bid-window", str(args.bid_window),
           "--settle-s", str(args.settle_s),
           "--base-price", str(args.base_price),
           "--price-step", str(args.price_step),
           "--base-ttft-ms", str(args.base_ttft_ms),
           "--ttft-step-ms", str(args.ttft_step_ms),
           "--max-price", str(args.max_price),
           "--max-latency-ms", str(args.max_latency_ms),
           "--timeout", str(args.timeout),
           "--ttl", str(args.ttl),
           "--experiment", SINGLE_HOST_EXPERIMENT]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(C.REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    out = subprocess.run(cmd, cwd=str(C.REPO_ROOT), env=env,
                         capture_output=True, text=True, timeout=3600)
    after = {p.name for p in C.RESULTS_DIR.glob(f"{SINGLE_HOST_EXPERIMENT}-*")}
    new = sorted(after - before)
    if not new:
        return None, f"run_network produced no run directory (rc={out.returncode})"
    run_dir = C.RESULTS_DIR / new[-1]
    if not (run_dir / "auctions.csv").exists():
        return None, f"{run_dir.name} has no auctions.csv (rc={out.returncode})"
    return run_dir, f"rc={out.returncode}"


def _read_auctions(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _num(row: dict, key: str) -> Optional[float]:
    raw = row.get(key, "")
    if raw in ("", None):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def compare(container_rows: list[dict], process_rows: list[dict]) -> list[dict]:
    """Per node count, the same four metrics under both topologies.

    Only successful auctions contribute; the counts of what contributed are in
    the row so a thin cell cannot pass as a confident one."""
    def ok(rows):
        return [r for r in rows if str(r.get("ok", "")).lower() == "true"]

    cont, proc = ok(container_rows), ok(process_rows)
    counts = sorted({int(float(r["n_nodes"])) for r in cont + proc})
    out: list[dict] = []
    for n in counts:
        row: dict = {"n_nodes": n,
                     "n_container_auctions": sum(1 for r in cont
                                                 if int(float(r["n_nodes"])) == n),
                     "n_process_auctions": sum(1 for r in proc
                                               if int(float(r["n_nodes"])) == n)}
        for key, _label in METRICS:
            for tag, rows in (("container", cont), ("process", proc)):
                vals = [v for v in (_num(r, key) for r in rows
                                    if int(float(r["n_nodes"])) == n) if v is not None]
                row[f"{key}_{tag}_mean"] = round(statistics.mean(vals), 1) if vals else ""
                row[f"{key}_{tag}_sd"] = (round(statistics.stdev(vals), 1)
                                          if len(vals) > 1 else "")
            cm, pm = row[f"{key}_container_mean"], row[f"{key}_process_mean"]
            row[f"{key}_delta_mean"] = (round(cm - pm, 1)
                                        if cm != "" and pm != "" else "")
        out.append(row)
    return out


def comparison_markdown(rows: list[dict]) -> str:
    head = ("| nodes | auctions (c/p) | "
            + " | ".join(f"{l} container | {l} process | delta" for _k, l in METRICS)
            + " |")
    rule = "|---" * (2 + 3 * len(METRICS)) + "|"
    lines = [head, rule]
    for r in rows:
        cells = []
        for k, _l in METRICS:
            cells += [f"{r[f'{k}_container_mean']} ± {r[f'{k}_container_sd']}",
                      f"{r[f'{k}_process_mean']} ± {r[f'{k}_process_sd']}",
                      f"{r[f'{k}_delta_mean']:+}" if r[f"{k}_delta_mean"] != "" else ""]
        lines.append(f"| {r['n_nodes']} | "
                     f"{r['n_container_auctions']}/{r['n_process_auctions']} | "
                     + " | ".join(cells) + " |")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="discovery.run_swarm",
                                description=__doc__.splitlines()[0])
    p.add_argument("--nodes", type=int, nargs="+", default=[3, 4, 5])
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--build", action="store_true", help="build the node image first")
    p.add_argument("--image", default=DEFAULT_IMAGE)
    p.add_argument("--project", default=DEFAULT_PROJECT)
    p.add_argument("--subnet", default=DEFAULT_SUBNET)
    p.add_argument("--ip-offset", type=int, default=DEFAULT_IP_OFFSET)
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--key-dir", default=str(DEFAULT_KEY_DIR))
    p.add_argument("--model", default=C.OLLAMA_MODEL)
    p.add_argument("--ollama-host", default="http://host.docker.internal:11434")
    p.add_argument("--prompt", default="What is the capital of France? Answer in one word.")
    p.add_argument("--bid-window", type=float, default=C.BID_WINDOW_S)
    p.add_argument("--settle-s", type=float, default=0.5)
    p.add_argument("--base-price", type=float, default=0.05)
    p.add_argument("--price-step", type=float, default=0.02)
    p.add_argument("--base-ttft-ms", type=float, default=1200.0)
    p.add_argument("--ttft-step-ms", type=float, default=100.0)
    p.add_argument("--max-price", type=float, default=0.20)
    p.add_argument("--max-latency-ms", type=int, default=30_000)
    p.add_argument("--warm-nodes", type=int, nargs="*", default=[])
    p.add_argument("--forge-nodes", type=int, nargs="*", default=[])
    p.add_argument("--latency-ms", type=float, default=0.0,
                   help="tc netem delay applied inside each container; needs "
                        "NET_ADMIN. If it cannot be applied the run records the "
                        "skip and continues unshaped")
    p.add_argument("--no-cap-net-admin", action="store_true",
                   help="withhold NET_ADMIN even when --latency-ms is set, to "
                        "exercise the recorded-skip path")
    p.add_argument("--timeout", type=float, default=60.0)
    p.add_argument("--ttl", type=float, default=120.0)
    p.add_argument("--keep", action="store_true", help="leave containers running")
    p.add_argument("--compare-single-host", action="store_true",
                   help="also run discovery.run_network with matched parameters "
                        "and write comparison.csv")
    p.add_argument("--experiment", default="exp2-swarm-containers")
    # Utility modes; each does its one job and exits.
    p.add_argument("--write-compose", action="store_true",
                   help="regenerate deploy/grid/compose.yml and exit")
    p.add_argument("--write-keys", metavar="DIR", default=None,
                   help="create the node identities in DIR and exit")
    p.add_argument("--print-bootstrap", action="store_true",
                   help="print the bootstrap multiaddr for --nodes[0] and exit")
    return p


def _utility_modes(args: argparse.Namespace) -> Optional[int]:
    if args.write_compose:
        path = write_example_compose()
        print(f"wrote {path}")
        return 0
    if args.write_keys:
        d = Path(args.write_keys).expanduser().resolve()
        n = args.nodes[0]
        for i in range(n):
            peer_id_for(f"eg{n}c-{i}", d)
        print(f"wrote {n} identities to {d}")
        return 0
    if args.print_bootstrap:
        n = args.nodes[0]
        d = Path(args.key_dir).expanduser().resolve()
        ip = node_ips(n, args.subnet, args.ip_offset)[0]
        print(f"/ip4/{ip}/tcp/{args.port}/p2p/{peer_id_for(f'eg{n}c-0', d)}")
        return 0
    return None


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    rc = _utility_modes(args)
    if rc is not None:
        return rc

    ok, detail = docker_ok()
    if not ok:
        print(f"[run_swarm] docker unusable: {detail}", file=sys.stderr)
        return 2

    params = dict(vars(args))
    params["docker_server_version"] = detail
    failures = 0

    with RunLog(args.experiment, params) as log:
        node_logs = log.dir / "node-logs"
        node_logs.mkdir(exist_ok=True)

        if args.build:
            info = build_image(args.image)
            log.write_json("image", info)
            log.note(f"built {info['image']} in {info['build_s']}s")
            print(f"[run_swarm] built {args.image} in {info['build_s']}s", flush=True)

        container_rows: list[dict] = []
        for n in args.nodes:
            for r in range(args.repeats):
                print(f"[run_swarm] n={n} repeat={r} ...", flush=True)
                res = run_once(n, r, args, node_logs)
                log.write_json(f"events-n{n}-r{r}", res.pop("events"))
                if res.get("network_inspect"):
                    log.write_json(f"network-n{n}-r{r}", res["network_inspect"])

                if not res["ok"] or res.get("error"):
                    failures += 1
                    log.drop(f"n={n} r={r}", res["error"] or "unknown")
                    print(f"[run_swarm] FAILED n={n} r={r}: {res['error']}", flush=True)
                if res.get("nodes_nonzero_exit"):
                    log.drop(f"n={n} r={r}",
                             f"{res['nodes_nonzero_exit']} container(s) exited non-zero")
                if res.get("unparsed_stdout"):
                    log.drop(f"n={n} r={r}",
                             f"{res['unparsed_stdout']} non-JSON stdout line(s)")
                for note in res.get("teardown_notes", []) or []:
                    log.drop(f"n={n} r={r}", f"teardown: {note}")
                if args.latency_ms > 0 and res.get("netem_skipped_nodes"):
                    log.drop(f"n={n} r={r}",
                             f"netem {args.latency_ms}ms requested but skipped on "
                             f"{res['netem_skipped_nodes']} container(s): "
                             f"{res.get('netem_skip_reasons')}")

                health = {h["name"]: h for h in (res.pop("node_health", None) or [])}
                cev = res.pop("container_events", None) or {}
                probes = res.pop("ollama_probes", None) or {}
                for name, ready in (res.pop("ready", None) or {}).items():
                    h = health.get(name, {})
                    ce = cev.get(name) or {}
                    pr = probes.get(name) or {}
                    if ready:
                        log.append("nodes", {
                            "n_nodes": n, "repeat": r, "name": name,
                            "container": h.get("container", ""),
                            "advertise_ip": ce.get("advertise_ip", ""),
                            "role": ready["role"], "peer_id": ready["peer_id"],
                            "wallet": ready["wallet"], "price": ready["price"],
                            "warm": ready["warm"], "model": ready["model"],
                            "tier": ready["tier"], "ttft_ms": ready.get("ttft_ms", ""),
                            "forge_bids": ready["forge_bids"],
                            "mesh": ready["mesh"], "meshed": ready["meshed"],
                            "bootstrap_ok": ready.get("bootstrap_ok", ""),
                            "bootstrap_failed": ready.get("bootstrap_failed", ""),
                            "routing_table": ready["routing_table"],
                            "ollama_reachable": pr.get("reachable", ""),
                            "ollama_probe_ms": pr.get("probe_ms", ""),
                            "state": h.get("state", ""),
                            "returncode": h.get("returncode", ""),
                            "exited_early": h.get("exited_early", ""),
                            "oom_killed": h.get("oom_killed", ""),
                            "stderr_bytes": h.get("stderr_bytes", ""),
                            "unparsed_stdout": h.get("unparsed_stdout", "")})
                    else:
                        log.drop(f"n={n} r={r}", f"{name} never reported ready "
                                                 f"(state={h.get('state')})")

                for prober, rows in (res.pop("dht_lookups", None) or {}).items():
                    for row in rows:
                        log.append("dht_lookups", {
                            "n_nodes": n, "repeat": r, "prober": prober,
                            "peer": row["peer"], "source": row["source"],
                            "ok": row["ok"], "attempts": row.get("attempts", ""),
                            "wallet": row["wallet"],
                            "models": ";".join(row["models"] or [])})

                award = res.get("award") or {}
                net = res.get("network_inspect") or {}
                row = {
                    "n_nodes": n, "repeat": r, "ok": res["ok"],
                    "error": res.get("error") or "",
                    "topology": "container",
                    "image": args.image, "subnet": args.subnet,
                    "network_id": net.get("network_id", ""),
                    "gateway": net.get("gateway", ""),
                    "distinct_ips": res.get("distinct_ips", ""),
                    "observed_ips": ";".join(res.get("observed_ips", []) or []),
                    "port_per_node": args.port,
                    "model": args.model, "max_price": args.max_price,
                    "bid_window_s": args.bid_window,
                    "warm_start_bonus": C.WARM_START_BONUS,
                    "job_id": res.get("job_id", ""),
                    "container_create_ms": res.get("container_create_ms", ""),
                    "bootstrap_start_ms": res.get("bootstrap_start_ms", ""),
                    "providers_start_ms": res.get("providers_start_ms", ""),
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
                    "netem_requested_ms": res.get("netem_requested_ms", ""),
                    "netem_applied_nodes": res.get("netem_applied_nodes", ""),
                    "netem_skipped_nodes": res.get("netem_skipped_nodes", ""),
                    "netem_skip_reasons": ";".join(res.get("netem_skip_reasons", []) or []),
                    "netem_qdiscs": ";".join(res.get("netem_qdiscs", []) or []),
                    "nodes_exited_early": res.get("nodes_exited_early", ""),
                    "nodes_nonzero_exit": res.get("nodes_nonzero_exit", ""),
                    "stderr_bytes": res.get("stderr_bytes", ""),
                    "unparsed_stdout": res.get("unparsed_stdout", ""),
                    "teardown_notes": " | ".join(res.get("teardown_notes", []) or []),
                }
                log.append("auctions", row)
                container_rows.append({k: str(v) for k, v in row.items()})

                for rank, b in enumerate(res.get("ranked") or []):
                    log.append("bids", {
                        "n_nodes": n, "repeat": r, "job_id": res.get("job_id", ""),
                        "rank": rank, "bidder": b["peer"], "price": b["price"],
                        "effective": b["effective"], "warm": b["warm"],
                        "ttft_ms": b["ttft_ms"]})

                if res["ok"]:
                    print(f"[run_swarm] n={n} r={r} "
                          f"ips={res.get('distinct_ips')} "
                          f"bids={res['n_eligible']} "
                          f"winner={award.get('winner_peer_id', '')[:16]}... "
                          f"clearing={award.get('clearing_price')} "
                          f"broadcast->award={res['broadcast_to_award_ms']}ms "
                          f"last_bid={res['last_bid_ms']}ms "
                          f"mesh_ready={res['mesh_ready_ms']}ms", flush=True)

        if args.compare_single_host:
            print("[run_swarm] running the single-host process baseline ...", flush=True)
            base_dir, detail = run_single_host_baseline(args)
            if base_dir is None:
                # No baseline is a missing comparison, not a silently one-sided
                # table: it is recorded and nothing is written.
                log.drop("single-host baseline", detail)
                print(f"[run_swarm] baseline unavailable: {detail}", file=sys.stderr)
            else:
                proc_rows = _read_auctions(base_dir / "auctions.csv")
                rows = compare(container_rows, proc_rows)
                log.write_table("comparison", rows)
                log.write_json("comparison_markdown",
                               {"table": comparison_markdown(rows),
                                "single_host_run": base_dir.name,
                                "single_host_detail": detail})
                print("\n" + comparison_markdown(rows) + "\n", flush=True)

        print(f"[run_swarm] results in {log.dir}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
