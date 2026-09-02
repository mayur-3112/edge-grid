# Containerised multi-host deployment

Every timing in Chapter 8 up to now was taken with all nodes as separate OS
processes on one host. Chapter 8 says so, repeatedly and correctly, because it
is the single largest threat to the validity of those numbers: N processes on
one machine all bind `127.0.0.1`, so "the network" between peers is a kernel
memcpy on a shared loopback interface. There is no bridge, no forwarding
decision, no per-peer interface queue, and no way to shape a link.

This directory replaces that with N containers, each in its own network
namespace, each holding its own address on a private bridge.

## What this is

Concretely, and each of these is checked or recorded on every run:

* **Distinct routable addresses.** Each node gets a fixed address on a
  user-defined bridge (`10.77.0.0/24` by default). `discovery/run_swarm.py`
  reads them back from `docker network inspect` — not from its own compose file
  — and **fails the run** if what Docker reports differs from what was asked
  for. The inspect output is written to `network-n<N>-r<R>.json` beside the
  results.
* **Every node binds the same TCP port.** All five nodes below listen on 4001.
  On shared loopback that is impossible; it is the cleanest single piece of
  evidence that the namespaces really are separate.
* **Real bridge forwarding.** Packets leave a container through a veth pair,
  cross the `edgegrid-swarm-*-net` bridge and enter another veth — the same
  path a container takes to any other container, including the NAT-style
  masquerade rule Docker installs for egress.
* **A shapeable link.** `--latency-ms` attaches `tc netem` to each container's
  `eth0`. There is no interface to attach that to when peers share loopback.
* **Reachability of the host.** The container reaches the host's Ollama through
  `host.docker.internal` / `host-gateway`, and the entrypoint probes it and
  records the answer per node in `nodes.csv`.

## What this is not

It is **not** a LAN deployment and must never be described as one. It is:

* one kernel, one scheduler, one CPU — 16 cores shared by every node,
* one clock, so no clock-skew between peers,
* no physical NIC, no switch, no wide-area path, no MTU or duplex negotiation,
* no independent failure domains: one OOM kills the whole experiment.

The honest description is *"each peer in its own network namespace with its own
address on a Linux bridge, on one machine"*. It removes the loopback shortcut.
It does not turn one machine into four.

## Files

| file | what it is |
|---|---|
| `Dockerfile` | Two-stage build of the node image. Stage 1 compiles wheels (`libgmp-dev` for fastecdsa); stage 2 keeps only `libgmp10` and `iproute2`. |
| `Dockerfile.dockerignore` | Keeps the ~1 GB `.venv`, `.git` and `node_modules` out of the build context. BuildKit reads it because of the `<dockerfile>.dockerignore` name, so the repo root needs no `.dockerignore`. |
| `requirements-node.txt` | The subset of the repo's `requirements.txt` that `discovery.node` imports. Every line must appear verbatim in `requirements.txt`; a test enforces it. |
| `entrypoint.sh` | Verifies the advertised address, applies (or records the failure to apply) `tc netem`, probes Ollama, then execs `discovery.node`. |
| `compose.yml` | A generated 3-node example for `docker compose up`. Regenerate with `python -m discovery.run_swarm --write-compose`. |

`discovery/run_swarm.py` is the measurement harness. Use it rather than
`docker compose up` for anything you intend to quote: `compose up` starts every
container at once, and `discovery.node`'s bootstrap dial does not retry, so the
providers can come up before the bootstrap peer is listening. `run_swarm`
sequences the bootstrap ahead of the providers exactly as `run_network` does.

## Running it

```bash
# build the image (from the repo root)
python -m discovery.run_swarm --build --nodes 3

# a swarm, measured, with a paired single-host baseline
python -m discovery.run_swarm --nodes 3 4 5 --repeats 3 --compare-single-host

# with 25 ms of injected one-way latency on every container
python -m discovery.run_swarm --nodes 3 --repeats 2 --latency-ms 25
```

Results land in `docs/results/exp2-swarm-containers-*/` with the same table
names `run_network` writes (`auctions.csv`, `nodes.csv`, `bids.csv`,
`dht_lookups.csv`), plus `network-n*.json` and, with `--compare-single-host`,
`comparison.csv`.

To drive the example compose file by hand:

```bash
python -m discovery.run_swarm --write-keys ./deploy/grid/keys --nodes 3
export EG_BOOTSTRAP="$(python -m discovery.run_swarm --print-bootstrap --nodes 3 \
                         --key-dir ./deploy/grid/keys)"
docker compose -f deploy/grid/compose.yml up
```

The example carries no PeerID because a PeerID cannot be known without a private
key, and no key is committed. Identities are generated on the host and
bind-mounted **read-only**, so a container can never mint a wallet the launcher
did not record.

## Evidence

### Five containers, five addresses, one port

`docker network inspect edgegrid-swarm-n5-net`, from
`docs/results/exp2-swarm-containers-20260902T164317Z/network-n5-r0.json`:

```json
{
  "network": "edgegrid-swarm-n5-net",
  "network_id": "214ba46cb814",
  "driver": "bridge",
  "subnet": "10.77.0.0/24",
  "gateway": "10.77.0.1",
  "containers": {
    "edgegrid-swarm-n5-node0": "10.77.0.10/24",
    "edgegrid-swarm-n5-node1": "10.77.0.11/24",
    "edgegrid-swarm-n5-node2": "10.77.0.12/24",
    "edgegrid-swarm-n5-node3": "10.77.0.13/24",
    "edgegrid-swarm-n5-node4": "10.77.0.14/24"
  }
}
```

and the multiaddrs the five nodes advertised to each other in that same run:

```
node0  /ip4/10.77.0.10/tcp/4001/p2p/16Uiu2HAkubxkPFC9ZiRy9eo49Wu1w3QvYkM5yR8wVMrsPrhNJy3b
node1  /ip4/10.77.0.11/tcp/4001/p2p/16Uiu2HAmEnYRHpXfHiQyBqkgVvMCcmfuEq8g6nC6a9tMiDP5AfQ5
node2  /ip4/10.77.0.12/tcp/4001/p2p/16Uiu2HAm3iZKLm8RxWe6vZT9FmTD6AP1B8TdbC6eGAB87UXrRixF
node3  /ip4/10.77.0.13/tcp/4001/p2p/16Uiu2HAmJPuK77QJGb2QgUWKLRWzEnYXVvjpL2n6a94atVHpe8tG
node4  /ip4/10.77.0.14/tcp/4001/p2p/16Uiu2HAkvYBt1H5taG2DcRK9NodVk9MjGBEUda2hfpSgCac2LyvT
```

Five distinct addresses, all on port 4001.

The GossipSub mesh forms across those namespaces: across the 9 auctions of that
run, all 27 provider rows in `nodes.csv` carry `meshed=True`, `bootstrap_ok=1`
and `bootstrap_failed=0`. The DHT lookups are genuinely remote too — all 60
provider→provider lookups in `dht_lookups.csv` resolved with `source=network`
and `ok=True`, meaning the record was fetched through the bootstrap peer rather
than read out of a local store. (The requester's own 27 lookups are `local`, as
expected: every provider dialled it, so it already held their records.)

### Containers vs. single-host processes

Nine auctions per topology, 3/4/5 nodes × 3 repeats, run back to back on the
same machine by `--compare-single-host`. Container run
`exp2-swarm-containers-20260902T164317Z`, process baseline
`swarm-baseline-single-host-20260902T164542Z`. Mean ± sd, all 18 auctions
succeeded, nothing dropped.

| nodes | auctions (c/p) | first bid — container | first bid — process | Δ | last bid — container | last bid — process | Δ | broadcast→award — container | broadcast→award — process | Δ |
|---|---|---|---|---|---|---|---|---|---|---|
| 3 | 3/3 | 14.0 ± 2.6 | 12.7 ± 1.5 | +1.3 | 18.0 ± 3.6 | 16.7 ± 2.3 | +1.3 | 2003.7 ± 0.6 | 2003.3 ± 0.6 | +0.4 |
| 4 | 3/3 | 18.3 ± 4.0 | 14.3 ± 1.2 | +4.0 | 26.7 ± 4.7 | 26.0 ± 2.6 | +0.7 | 2005.3 ± 1.2 | 2006.0 ± 1.0 | −0.7 |
| 5 | 3/3 | 22.0 ± 7.0 | 15.0 ± 3.0 | +7.0 | 43.7 ± 24.8 | 30.0 ± 6.6 | +13.7 | 2006.3 ± 1.5 | 2005.7 ± 2.1 | +0.6 |

**What this says.** Moving from shared loopback to a real bridge costs a few
milliseconds, and the cost grows with node count (+1.3 ms at 3 nodes, +7.0 ms at
5 for the first bid). The single-host figures were therefore optimistic, but not
by an amount that changes any conclusion: `broadcast_to_award_ms` is unchanged
within noise, because it is dominated by the fixed 2 s bid window, and even at 5
nodes the last bid still arrives ~44 ms after the job goes out, two orders of
magnitude inside that window. Note also the container spread at 5 nodes (± 24.8
against ± 6.6): sharing one CPU across five containers is measurably noisier
than across five processes.

**One caveat, stated because it is easy to misread.** `mesh_ready_ms` is *not*
comparable across the two harnesses and is left out of the table above. It is
measured from the moment the launcher asks for the bootstrap to start, so in the
container case it includes container start-up that the process case does not
have. It was 10.3 s / 10.4 s / 12.9 s (3/4/5 nodes) for containers against
7.4 s / 8.0 s / 7.5 s for processes, of which 1.7 s / 1.8 s / 2.9 s is
`container_create_ms + bootstrap_start_ms + providers_start_ms` — all three are
recorded per row so the reader can subtract. Even after subtracting, the
container mesh is slower to form (8.6 s / 8.6 s / 10.1 s), which is a real
effect and not an artefact: the nodes are contending for the same 16 cores
through more layers. The other three metrics are
timestamped entirely inside already-running nodes and carry no container
overhead at all, which is why the comparison rests on them.

### Injected latency

`--latency-ms` attaches `tc netem` inside each container. n=3, 2 repeats per
level, unshaped row from the comparison run above.

| injected one-way delay | auctions | first bid (ms) | last bid (ms) | netem applied / skipped |
|---|---|---|---|---|
| none | 3 | 14.0 ± 2.6 | 18.0 ± 3.6 | 0 / 9 (not requested) |
| 10 ms | 2 | 44.5 ± 6.4 | 51.0 ± 5.7 | 6 / 0 |
| 25 ms | 2 | 71.0 ± 7.1 | 73.5 ± 6.4 | 6 / 0 |
| 50 ms | 2 | 114.0 ± 5.7 | 117.5 ± 9.2 | 6 / 0 |

The added delay tracks the injected value at roughly 2× — one hop out for the
job and one back for the bid, each crossing one shaped egress queue — with a
constant overhead on top that is most visible at 10 ms. The qdisc is read back
with `tc qdisc show` after it is attached and the string is stored in the
`netem_qdiscs` column, so the table is backed by the shaping the kernel actually
reports, not by the shaping that was requested.

**When it cannot be applied it is recorded, never assumed.** `tc netem` needs
`NET_ADMIN`, which the launcher grants only when shaping is requested.
`--no-cap-net-admin` withholds it deliberately, and the result is:

```
ok                 = True
netem_requested_ms = 25.0
netem_applied_nodes = 0
netem_skipped_nodes = 3
netem_skip_reasons = tc failed (NET_ADMIN missing?): RTNETLINK answers: Operation not permitted
first_bid_ms       = 14
last_bid_ms        = 18
```

with a matching entry in `manifest.json`'s `dropped` list. The run continues
unshaped and the timings show it (14 ms, exactly the unshaped figure) rather
than a row silently claiming 25 ms it never had.

## Known environment limitation

On this machine Ollama listens on `127.0.0.1:11434` only, so no container can
reach it whatever address it is given — `ollama_reachable=False` for all 36 node
rows in `nodes.csv`, refused after a mean of 38.8 ms. This affects nothing that is
measured here: `discovery.node` never calls Ollama and the auction is pure
protocol. To make the host's inference server reachable from the swarm, start it
as `OLLAMA_HOST=0.0.0.0:11434 ollama serve`; `host.docker.internal` already
resolves correctly (to `172.17.0.1`) from inside the containers.

## Root inside the container

The node runs as root in the container. `tc netem` needs `NET_ADMIN`, and a
capability added to a container is not in a non-root process's permitted set
without ambient-capability gymnastics; separately, the identity directory is
bind-mounted from a host user's `0600` key files, which an arbitrary in-image
UID could not read. The container publishes no ports, joins an isolated
user-defined bridge, and mounts exactly one host path read-only. That is the
trade, stated rather than hidden.
