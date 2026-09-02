# Local IPFS (kubo) node

The node `edgegrid.weights` publishes to and fetches from. It is a real kubo
daemon, not a stand-in: the point of content addressing is that a CID commits to
the bytes, and that property is only demonstrated when the bytes round-trip
through software this project did not write.

```
make ipfs-up      # start it, wait for the HTTP API to answer
make ipfs-down    # stop and remove the container; the blockstore survives
make ipfs-logs    # tail the daemon
```

`make ipfs-up` prints the daemon's version once the API answers, and fails with
a non-zero status if it does not answer within 40 seconds.

## What it exposes

| Port | Bound to        | What it is                                       |
|------|-----------------|--------------------------------------------------|
| 5001 | `127.0.0.1`     | HTTP API. `add`, `cat`, `pin`, `files/stat`.     |
| 8080 | `127.0.0.1`     | Read-only gateway (`/ipfs/<cid>`).               |
| 4001 | all interfaces  | libp2p swarm - the port that is meant to be public. |

The API is an unauthenticated control plane: anything that can reach it can pin,
unpin, and reconfigure the node. It is therefore published to loopback only.
Binding it to `wlo1` would hand control of the node to the LAN. Inside the
container the API is rebound to `0.0.0.0` by `container-init.d/001-bind-api.sh`,
because a published port cannot reach a service listening on the container's own
loopback; the host-side binding is what keeps it local.

Override a port that something else already owns:

```
IPFS_GATEWAY_PORT=8088 make ipfs-up     # this machine already runs something on 8080
IPFS_API_PORT=5002 IPFS_SWARM_PORT=4002 make ipfs-up
```

Point the client at a non-default API with `IPFS_API_URL`, which
`edgegrid.weights` and `inference.weights_cli` both read:

```
IPFS_API_URL=http://127.0.0.1:5002 python -m inference.weights_cli status
```

## State

Blocks and the node's identity live in the named volume `edgegrid_ipfs_data`, so
the repository working tree stays clean and `make ipfs-down` does not throw the
blockstore away. To start over from an empty node:

```
make ipfs-down && docker volume rm edgegrid_ipfs_data
```

## Checking it by hand

```
curl -s -X POST http://127.0.0.1:5001/api/v0/version
curl -s -X POST -F file=@somefile http://127.0.0.1:5001/api/v0/add
curl -s http://127.0.0.1:8080/ipfs/<cid>          # gateway, if 8080 is yours
python -m inference.weights_cli status
```

## Note on what a single node proves

One daemon on one host demonstrates content addressing, pinning and verified
retrieval; it does not demonstrate peer-to-peer distribution, because there is
one peer. The `weights` experiment's cold-fetch timings are therefore reported
as client-side fetch-and-verify cost over a loopback API, and are labelled that
way in the run log rather than presented as transfer measurements. Adding a
second kubo container and connecting the two with `swarm connect` is the
experiment that would measure distribution; nothing in `edgegrid.weights`
changes for it.
