# Discovery + hybrid market protocol

Owns Phase-1 Modules 1 and 2: peer discovery over Kademlia, and the sealed-bid
second-price auction that routes a job to a provider. Everything described here
runs; the numbers below are produced by `python -m discovery.summarize`, which
pools every `exp2-auction-convergence-*` run on disk rather than quoting one.

## What is here

| file | what it is |
|---|---|
| `node.py` | `EdgeGridNode` - libp2p host, KadDHT (SERVER), GossipSub over the four market topics, UDP heartbeat, plus a CLI. One node per OS process. |
| `heartbeat.py` | Signed UDP liveness. Carries the warm-model set that feeds `WARM_START_BONUS`. |
| `run_network.py` | Launches N nodes as N real processes, drives one auction, writes results through `RunLog`. This is Experiment 2's harness. |
| `summarize.py` | Pools every recorded run of an experiment into one table with dispersion, naming its sources. |
| `../edgegrid/market.py` | The auction itself: pure functions, no I/O. Scoring, exclusion reasons, second-price clearing. |

Message shapes are `edgegrid/schemas.py` and nothing else. Config is
`edgegrid/config.py`; nothing in this directory reads `os.environ` directly.

## The split: DHT vs heartbeat

The DHT holds what changes slowly and must be globally reachable - wallet, tier,
core count, which models a node serves, its multiaddrs. One signed `NodeRecord`
per node at `/edgegrid/node/<peer_id>`.

Liveness and warm-model state change every few seconds and are only interesting
to nearby peers, so they ride on signed UDP heartbeats instead. Pushing them
through Kademlia would republish records constantly and still return stale
values. A node advertises its `/ip4/<ip>/udp/<port>` endpoint inside its
`NodeRecord`, so the DHT bootstraps the heartbeat mesh and the heartbeat mesh
then tracks liveness by itself.

## The auction

Procurement (reverse) auction: providers bid the price they want to be paid, the
requester takes the best score, the winner is paid the **second** price. Bidding
your true reserve is dominant - your bid decides whether you win, never what you
are paid.

* `max_latency_ms`, `min_tier` and `max_price` are hard constraints. A bid that
  misses one scores `inf` and is reported with a reason string, never silently
  treated as merely expensive.
* `WARM_START_BONUS` (15%) is a handicap on the **score**, never on the money.
  The clearing price is the winner's threshold price - the highest it could have
  bid and still won - which is `runner_up_effective / winner_discount_factor`.
  With a cold winner that is exactly the runner-up's price.
* One eligible bid means no runner-up, so the clearing price is the requester's
  own reserve, `job.max_price`. Paying the winner's own bid instead would be a
  first-price rule and would reward shading in exactly the monopoly case.
* Invariant, always: `winning_bid_price <= clearing_price <= max_price`. A
  reverse auction paying its winner less than the winner's own bid would not be
  individually rational.
* Ties break on `(effective_price, estimated_ttft_ms, bidder_peer_id)`, with the
  effective price quantised, so two runs over the same bids agree exactly.
* Repeat bids from one peer collapse to that peer's newest revision, chosen by
  the bid's own signed `created_ms` (exact ties by the hash of the bid), so
  nobody can be their own runner-up and set their own price - and so the result
  does not turn on which copy gossip happened to deliver last. The superseded
  copies are reported in `rejected` as `superseded` rather than discarded:
  `n_eligible + len(rejected) == n_received` holds for every auction, and
  `evaluate` raises rather than return an outcome where it does not.

## Security posture

Every inbound `JobRequest`, `Bid` and `JobAward` is checked against the wallet
the message itself claims, and a failure is counted and emitted as a `dropped`
event - never dropped quietly. A `NodeRecord` is validated by a DHT namespace
validator on both `put_value` and every record returned by `get_value`, so a
forged record cannot enter the value store at all. Heartbeats from a peer whose
DHT record we already hold must match it; heartbeats from an unknown peer are
accepted but flagged `verified=False` rather than silently trusted, and
`warm_models_of` returns nothing for such a peer by default - the warm set is
the one heartbeat field that moves money, and a 15% edge must not be available
to anyone who can send a UDP packet.

Nothing in this directory discards an inbound message without leaving two
traces: a `stats` counter and a `dropped` event carrying `kind`, `job_id` and
`why`. That includes parse failures, bids for jobs we never saw, and bids that
arrive after the window has closed. `run_network` folds them into the
`dropped_at_wire` column, so a run where a provider was rejected shows *why* it
contributed no bid rather than simply showing a thinner auction.

## Running it

```bash
# one node
python -m discovery.node --name alice --port 4001 --role provider --price 0.07

# a second node that dials it (PeerID is deterministic, so this address is stable)
python -m discovery.node --name bob --port 4002 --role requester \
    --bootstrap /ip4/127.0.0.1/tcp/4001/p2p/<alice-peer-id> \
    --wait-peers 1 --job-after 0.5 --ttl 20

# the whole Experiment 2 sweep
python -m discovery.run_network --nodes 3 4 5 --repeats 3

# the warm-start bonus overturning a cheaper cold bid
python -m discovery.run_network --nodes 5 --base-price 0.05 --price-step 0.005 \
    --warm-nodes 2 --experiment exp2-warm-bonus

# a provider signing bids with the wrong key, to watch the rejection path
python -m discovery.run_network --nodes 4 --forge-nodes 2 --experiment exp2-forged-bids

# pool every recorded run into the table above
python -m discovery.summarize

# tests
python -m pytest tests/test_market.py tests/test_discovery.py -q
```

Each run also records what the node *processes* did, independently of what they
said on stdout: `returncode`, `exited_early`, `stderr_bytes` and
`unparsed_stdout` per node in `nodes.csv`, and their totals per auction. A node
that crashed after reporting ready simply stops bidding, and without those
columns the run would show only a thinner auction with no reason attached.

`run_network.py` uses a star topology on purpose: providers dial only the
bootstrap peer and never each other, so a provider's DHT lookup of another
provider's record *cannot* be answered from its own store and has to be routed.
In the recorded run, 87 of 114 lookups were served over the network and 27 were
local replicas; all 114 succeeded.

## Experiment 2 - auction convergence, 3 -> 5 nodes

57 auctions pooled over 8 separate `run_network` launches on this host, every
one successful, no dropped rows. Regenerate with `python -m discovery.summarize`;
it writes the pooled table plus a `sources.csv` naming every run it read.
Timestamps are stamped inside the node processes, not at the reading end of a
pipe. Values are mean ± sd across auctions.

| nodes | auctions | bids | first bid (ms) | last bid (ms) | broadcast -> award (ms) | mesh ready (ms) |
|---|---|---|---|---|---|---|
| 3 | 19 | 2 | 16.9 ± 6.8 | 21.3 ± 9.2 | 2007.6 ± 7.9 | 7899.4 ± 1605.7 |
| 4 | 19 | 3 | 22.3 ± 14.1 | 32.6 ± 20.2 | 2007.7 ± 3.9 | 8028.0 ± 1695.7 |
| 5 | 19 | 4 | 21.1 ± 9.4 | 36.7 ± 18.8 | 2007.1 ± 3.0 | 8154.5 ± 1618.0 |

What this does and does not show:

* **`broadcast -> award` is 2007 ms at every node count, ± 8 ms.** That is the
  auction's fixed cost: the requester waits out the whole `BID_WINDOW_S = 2.0`
  by design. It is the one number here that is tight enough to quote on its own.
* **The bid window is enormously oversized for a network this small.** Every bid
  is in within ~90 ms in the worst auction of 57, against a 2000 ms window. On a
  LAN of this size the window could be cut by an order of magnitude without
  losing a bid.
* **`last bid` does not measurably scale with node count over 3 -> 5.** The
  means rise (21 -> 33 -> 37 ms) but the standard deviations are 9 - 20 ms and
  the distributions overlap heavily; individual runs disagree about the
  ordering. Three to five nodes on one host is too narrow a range, and this host
  too noisy, to claim a scaling law. An earlier single run of nine auctions read
  as a clean monotone 11 -> 13 -> 23 ms; pooling seven more runs shows that was
  the quietest run rather than the trend.
* **`mesh ready` is not a protocol number at all.** It is flat within noise
  across node counts and is dominated by sequential process startup and the ~2 s
  Python import of libp2p per node.

## Known gaps

* One requester per launch. Concurrent auctions across several requesters are
  supported by the code (auctions are keyed by `job_id`) but are not exercised.
* `NodeRecord.tokens_per_sec` and `vram_gb` are advertised as 0.0. There is no
  NVIDIA GPU on this host and no benchmark loop wired in yet; they are reported
  as zero rather than guessed.
* The award is published to `TOPIC_AWARDS` and the winner acknowledges it, but
  handing the job to the inference track over `PROTOCOL_INFERENCE` is that
  track's boundary and is not implemented here.
* Bids are quoted from the `--price` flag. Real cost modelling (tokens, queue
  depth, energy) belongs to a later phase.
