"""An Edge Grid node: Kademlia discovery + GossipSub market, over py-libp2p.

This is the process that everything else in the system attaches to. One node is
one OS process and owns:

  * a libp2p host whose PeerID is derived deterministically from the node's
    secp256k1 identity, so a restart keeps the same address, stake and DHT key,
  * a Kademlia DHT in SERVER mode holding this node's signed `NodeRecord` at
    `/edgegrid/node/<peer_id>` - the slow-changing capability advertisement,
  * a GossipSub mesh over the four market topics,
  * a UDP heartbeat (see heartbeat.py) for liveness and warm-model state.

Three things here are easy to get wrong and cost real debugging time:

1. `GossipSub.run()` is its own anyio service and nothing starts it for you.
   Starting only `Pubsub` gives you a mesh that never grafts and publishes that
   are silently never delivered. Both services must be started, and a subscribe
   must happen before peers connect, or the remote never learns we want the
   topic. `_wait_for_mesh` then blocks until the mesh actually has a peer in it,
   because publishing into an empty mesh is a silent no-op.

2. Every inbound message is signature-checked against the wallet the message
   itself claims, and a failure is counted and emitted, never dropped quietly.
   The DHT namespace validator does the same for records, so a forged
   `NodeRecord` cannot even enter the value store.

3. The DHT routing table is not populated just because a transport connection
   exists. `_maintenance_loop` explicitly adds connected peers to it; without
   that, `put_value` has nobody to replicate to and `get_value` has nobody to
   ask, and the DHT silently degrades into a local dict.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Callable, Optional

import psutil
import trio
from libp2p import new_host
from libp2p.crypto.secp256k1 import create_new_key_pair
from libp2p.kad_dht import DHTMode, KadDHT
from libp2p.peer.id import ID
from libp2p.peer.peerinfo import info_from_p2p_addr
from libp2p.pubsub.gossipsub import PROTOCOL_ID as GOSSIPSUB_PROTOCOL_ID
from libp2p.pubsub.gossipsub import GossipSub
from libp2p.pubsub.pubsub import Pubsub
from libp2p.records.utils import InvalidRecordType
from libp2p.records.validator import Validator
from libp2p.tools.anyio_service.context import background_trio_service
from multiaddr import Multiaddr

from discovery.heartbeat import HeartbeatService, parse_udp_endpoint, udp_multiaddr
from edgegrid import config as C
from edgegrid import market
from edgegrid.identity import Identity, verify_message
from edgegrid.schemas import (ALL_TOPICS, DHT_NODE_PREFIX, TOPIC_AWARDS, TOPIC_BIDS,
                              TOPIC_COMMITMENTS, TOPIC_JOBS, Bid, HardwareTier,
                              JobAward, JobRequest, NodeRecord, now_ms)

DHT_NAMESPACE = DHT_NODE_PREFIX.strip("/").split("/")[0]   # "edgegrid"
ROLES = ("requester", "provider", "both")


# --------------------------------------------------------------------------
# DHT record validation
# --------------------------------------------------------------------------

class NodeRecordValidator(Validator):
    """Namespace validator for `/edgegrid/node/<peer_id>`.

    Applied by py-libp2p on both `put_value` and every record returned by
    `get_value`, so an unsigned or mismatched record is rejected at the DHT
    boundary rather than by whoever happens to read it later."""

    def validate(self, key: str, value: bytes) -> None:
        if not key.startswith(DHT_NODE_PREFIX):
            raise InvalidRecordType(f"not an edgegrid node key: {key}")
        want_peer = key[len(DHT_NODE_PREFIX):]
        try:
            rec = NodeRecord.from_bytes(value)
        except Exception as exc:
            raise InvalidRecordType(f"unparseable NodeRecord: {exc}") from exc
        if rec.peer_id != want_peer:
            raise InvalidRecordType("record peer_id does not match its DHT key")
        if not verify_message(rec, rec.wallet_address):
            raise InvalidRecordType("record signature does not match wallet_address")

    def select(self, key: str, values: list[bytes]) -> int:
        """Newest `updated_ms` wins; ties keep the incumbent order.

        py-libp2p only calls this with records `validate` has already accepted,
        so an unparseable one here means the two disagree. Returning index 0
        anyway would hand back that garbage record as the best value, which is
        the one outcome a selector must never produce - so it raises."""
        best, best_ts = 0, None
        for i, raw in enumerate(values):
            try:
                ts = NodeRecord.from_bytes(raw).updated_ms
            except Exception:
                continue
            if best_ts is None or ts > best_ts:
                best, best_ts = i, ts
        if best_ts is None:
            raise InvalidRecordType(
                f"no parseable NodeRecord among {len(values)} candidates for {key}")
        return best


# --------------------------------------------------------------------------
# the node
# --------------------------------------------------------------------------

def _stdout_emit(event: dict) -> None:
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


class EdgeGridNode:
    def __init__(self, name: str, port: int, *,
                 bootstrap: Optional[list[str]] = None,
                 listen_ip: str = "0.0.0.0",
                 advertise_ip: str = "127.0.0.1",
                 model: str = C.OLLAMA_MODEL,
                 price: float = 0.05,
                 role: str = "both",
                 warm: bool = False,
                 tier: HardwareTier = HardwareTier.CPU,
                 stake: float = C.MIN_STAKE,
                 forge_bids: bool = False,
                 ttft_ms: float = 1500.0,
                 tokens_per_sec: float = 0.0,
                 hb_port: Optional[int] = None,
                 key_dir: Optional[str] = None,
                 bid_window_s: float = C.BID_WINDOW_S,
                 mesh_wait_s: float = C.MESH_WAIT_S,
                 gossip_degree: tuple[int, int, int] = (6, 2, 12),
                 emit: Optional[Callable[[dict], None]] = None):
        if role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}, got {role!r}")
        self.name = name
        self.port = port
        self.bootstrap = list(bootstrap or [])
        self.listen_ip = listen_ip
        self.advertise_ip = advertise_ip
        self.model = model
        self.models = [model]
        self.price = price
        self.role = role
        self.warm = warm
        self.tier = tier
        self.stake = stake
        self.ttft_ms = ttft_ms
        self.tokens_per_sec = tokens_per_sec
        self.hb_port = hb_port if hb_port is not None else port + 10_000
        self.bid_window_s = bid_window_s
        self.mesh_wait_s = mesh_wait_s
        # Gossipsub fanout. The library defaults assume a large network and would
        # prune a small one back below its own size, so a job could reach only
        # some providers and the auction would look thin for no visible reason.
        # degree_high is kept above any node count this harness launches.
        self.gossip_degree = gossip_degree
        self._emit_fn = emit or _stdout_emit

        self.identity = Identity.load_or_create(name, key_dir)
        # Test affordance: sign bids with a throwaway key while still claiming
        # the real wallet, so an honest requester's rejection path can be
        # exercised on a live network rather than only in unit tests.
        self.forge_bids = forge_bids
        self._bid_signer = Identity.generate() if forge_bids else self.identity
        self.key_pair = create_new_key_pair(self.identity.seed_bytes)
        # Known before the host starts, which is what lets a launcher compute a
        # bootstrap multiaddr without first booting the bootstrap node.
        self.peer_id = str(ID.from_pubkey(self.key_pair.public_key))

        self.host = None
        self.dht: Optional[KadDHT] = None
        self.pubsub: Optional[Pubsub] = None
        self.gossipsub: Optional[GossipSub] = None
        self.subs: dict[str, object] = {}

        self.records: dict[str, NodeRecord] = {}
        self.jobs: dict[str, JobRequest] = {}
        self.open_auctions: dict[str, list[Bid]] = {}
        self.awards: dict[str, JobAward] = {}
        self.won: list[JobAward] = []

        self.stats = {
            "jobs_seen": 0, "jobs_bad_sig": 0, "jobs_bad_parse": 0,
            "jobs_declined_model": 0, "bids_sent": 0,
            "bids_seen": 0, "bids_bad_sig": 0, "bids_bad_parse": 0, "bids_unknown_job": 0,
            "bids_late": 0, "awards_seen": 0, "awards_bad_sig": 0, "awards_bad_parse": 0,
            "awards_unknown_job": 0, "dht_puts": 0, "dht_gets_ok": 0,
            "dht_gets_missing": 0, "dht_gets_invalid": 0,
            "bootstrap_ok": 0, "bootstrap_failed": 0, "maintenance_errors": 0,
            "handler_errors": 0, "sender_unparsed": 0,
        }

        self.heartbeat = HeartbeatService(
            self.identity, self.peer_id, self.hb_port, bind_ip=self.listen_ip,
            peers_fn=self._heartbeat_endpoints,
            wallet_fn=lambda pid: (self.records[pid].wallet_address
                                   if pid in self.records else None),
            warm_models_fn=lambda: list(self.warm_models),
        )
        self._ready = trio.Event()

    # -- events ----------------------------------------------------------

    def emit(self, event: dict) -> None:
        """Emit one structured event. `ts_ms` is stamped here, in the node's own
        process, so a launcher measuring broadcast->award is not measuring its
        own pipe-read scheduling."""
        event.setdefault("ts_ms", now_ms())
        event.setdefault("node", self.name)
        self._emit_fn(event)

    def _dropped(self, kind: str, job_id: Optional[str], why: str,
                 detail: str = "") -> None:
        """Emit one rejection. Every path that discards an inbound message goes
        through here, so a `dropped` event and the matching `stats` counter can
        never disagree, and the harness can account for a message it saw sent
        but never saw scored."""
        self.emit({"event": "dropped", "node": self.name, "kind": kind,
                   "job_id": job_id, "why": why, "detail": detail})

    # -- identity / advertisement ---------------------------------------

    @property
    def warm_models(self) -> list[str]:
        return list(self.models) if self.warm else []

    @property
    def is_provider(self) -> bool:
        return self.role in ("provider", "both")

    @property
    def is_requester(self) -> bool:
        return self.role in ("requester", "both")

    def dial_multiaddr(self) -> str:
        """The address other nodes should dial. Built from `advertise_ip`, not
        from `host.get_addrs()`, because a node bound to 0.0.0.0 reports
        /ip4/0.0.0.0/... which nobody can dial."""
        return f"/ip4/{self.advertise_ip}/tcp/{self.port}/p2p/{self.peer_id}"

    def node_record(self) -> NodeRecord:
        rec = NodeRecord(
            peer_id=self.peer_id,
            wallet_address=self.identity.address,
            pubkey_hex=self.identity.pubkey_hex,
            multiaddrs=[self.dial_multiaddr(),
                        udp_multiaddr(self.advertise_ip, self.hb_port)],
            tier=self.tier,
            models=list(self.models),
            warm_models=self.warm_models,
            cpu_count=psutil.cpu_count(logical=True) or 0,
            ram_gb=round(psutil.virtual_memory().total / (1024 ** 3), 2),
            vram_gb=0.0,
            tokens_per_sec=self.tokens_per_sec,
            stake=self.stake,
            updated_ms=now_ms(),
        )
        self.identity.sign_message(rec)
        return rec

    # -- DHT -------------------------------------------------------------

    async def publish_record(self) -> NodeRecord:
        """Write our signed NodeRecord into the DHT at DHT_NODE_PREFIX+peer_id."""
        rec = self.node_record()
        await self.dht.put_value(DHT_NODE_PREFIX + self.peer_id, rec.to_bytes())
        self.records[self.peer_id] = rec
        self.stats["dht_puts"] += 1
        return rec

    async def lookup_record(self, peer_id: str) -> tuple[Optional[NodeRecord], str]:
        """Read a peer's NodeRecord out of the DHT.

        Returns (record, source) where source is 'local' when the value was
        already replicated into our own store, 'network' when it had to be
        fetched from a peer, and 'missing'/'invalid' on failure. The distinction
        matters for proving the DHT actually works rather than just echoing
        values we put there ourselves."""
        key = DHT_NODE_PREFIX + peer_id
        had_local = self.dht.value_store.get(key.encode("utf-8")) is not None
        raw = await self.dht.get_value(key)
        if raw is None:
            self.stats["dht_gets_missing"] += 1
            return None, "missing"
        try:
            rec = NodeRecord.from_bytes(raw)
        except Exception:
            self.stats["dht_gets_invalid"] += 1
            return None, "invalid"
        if rec.peer_id != peer_id or not verify_message(rec, rec.wallet_address):
            self.stats["dht_gets_invalid"] += 1
            return None, "invalid"
        self.records[peer_id] = rec
        self.stats["dht_gets_ok"] += 1
        return rec, "local" if had_local else "network"

    def _heartbeat_endpoints(self) -> dict[str, tuple[str, int]]:
        out: dict[str, tuple[str, int]] = {}
        for pid, rec in self.records.items():
            ep = parse_udp_endpoint(rec.multiaddrs)
            if ep:
                out[pid] = ep
        return out

    # -- market ----------------------------------------------------------

    async def broadcast_job(self, prompt: str, *, model: Optional[str] = None,
                            max_tokens: int = 256,
                            max_price: float = 1.0,
                            max_latency_ms: int = 30_000,
                            min_tier: HardwareTier = HardwareTier.CPU) -> JobRequest:
        job = JobRequest(
            prompt=prompt, model=model or self.model, max_tokens=max_tokens,
            requester_peer_id=self.peer_id, requester_wallet=self.identity.address,
            max_price=max_price, max_latency_ms=max_latency_ms, min_tier=min_tier,
        )
        self.identity.sign_message(job)
        self.jobs[job.job_id] = job
        self.open_auctions[job.job_id] = []
        await self.pubsub.publish(TOPIC_JOBS, job.to_bytes())
        self.emit({"event": "job_published", "node": self.name, "job_id": job.job_id,
                   "model": job.model, "max_price": job.max_price,
                   "max_latency_ms": job.max_latency_ms})
        return job

    async def collect_bids(self, job_id: str, window_s: Optional[float] = None) -> list[Bid]:
        """Hold the bid window open, then close it. Bids that arrive after the
        window are counted as late rather than silently accepted."""
        await trio.sleep(window_s if window_s is not None else self.bid_window_s)
        return self.open_auctions.pop(job_id, [])

    async def run_auction(self, prompt: str, **job_kwargs) -> market.AuctionOutcome:
        """Broadcast, collect for the bid window, clear at the second price,
        publish the signed award. Returns the whole outcome, rejections included."""
        t0 = time.perf_counter()
        job = await self.broadcast_job(prompt, **job_kwargs)
        bids = await self.collect_bids(job.job_id)
        auction_ms = (time.perf_counter() - t0) * 1000.0
        outcome = market.evaluate(bids, job, auction_ms=auction_ms)
        if outcome.award is not None:
            self.identity.sign_message(outcome.award)
            self.awards[job.job_id] = outcome.award
            await self.pubsub.publish(TOPIC_AWARDS, outcome.award.to_bytes())
        self.emit({
            "event": "auction_closed", "node": self.name, "job_id": job.job_id,
            "auction_ms": round(auction_ms, 2),
            "n_received": outcome.n_received, "n_eligible": outcome.n_eligible,
            "n_accounted": outcome.n_accounted,
            "rejected": outcome.reason_counts(),
            "bids": [{"peer": sb.bid.bidder_peer_id, "price": sb.bid.price,
                      "effective": round(sb.effective, 6), "warm": sb.bid.warm,
                      "ttft_ms": sb.bid.estimated_ttft_ms} for sb in outcome.ranked],
            "award": (json.loads(outcome.award.model_dump_json())
                      if outcome.award else None),
        })
        return outcome

    # -- inbound handlers ------------------------------------------------

    async def _on_job(self, data: bytes, sender: str) -> None:
        try:
            job = JobRequest.from_bytes(data)
        except Exception as exc:
            self.stats["jobs_bad_parse"] += 1
            self._dropped("job", None, "bad_parse", f"{type(exc).__name__}: {exc}")
            return
        if not verify_message(job, job.requester_wallet):
            self.stats["jobs_bad_sig"] += 1
            self._dropped("job", job.job_id, "bad_signature")
            return
        self.stats["jobs_seen"] += 1
        self.jobs[job.job_id] = job
        if not self.is_provider or job.requester_peer_id == self.peer_id:
            return
        if job.model not in self.models:
            self.stats["jobs_declined_model"] += 1
            self.emit({"event": "declined", "node": self.name, "job_id": job.job_id,
                       "why": "model_not_served", "want": job.model,
                       "have": self.models})
            return
        bid = market.bid_for(
            job, peer_id=self.peer_id, wallet=self.identity.address,
            price=self.price, estimated_ttft_ms=self.ttft_ms,
            warm=job.model in self.warm_models, tier=self.tier, stake=self.stake)
        self._bid_signer.sign_message(bid)
        await self.pubsub.publish(TOPIC_BIDS, bid.to_bytes())
        self.stats["bids_sent"] += 1
        self.emit({"event": "bid_sent", "node": self.name, "job_id": job.job_id,
                   "price": bid.price, "warm": bid.warm,
                   "ttft_ms": bid.estimated_ttft_ms, "forged": self.forge_bids})

    async def _on_bid(self, data: bytes, sender: str) -> None:
        try:
            bid = Bid.from_bytes(data)
        except Exception as exc:
            self.stats["bids_bad_parse"] += 1
            self._dropped("bid", None, "bad_parse", f"{type(exc).__name__}: {exc}")
            return
        if not verify_message(bid, bid.bidder_wallet):
            self.stats["bids_bad_sig"] += 1
            self._dropped("bid", bid.job_id, "bad_signature")
            return
        self.stats["bids_seen"] += 1
        job = self.jobs.get(bid.job_id)
        if job is None:
            # A bid for a job we never saw: either we missed the job on the
            # mesh, or the bidder invented a job_id. Either way it cannot be
            # scored, and it must not vanish - a requester that quietly loses
            # bids reports a thin auction with no explanation for why.
            self.stats["bids_unknown_job"] += 1
            self._dropped("bid", bid.job_id, "unknown_job")
            return
        if job.requester_peer_id != self.peer_id:
            return  # somebody else's auction; we relay it but do not score it
        window = self.open_auctions.get(bid.job_id)
        if window is None:
            self.stats["bids_late"] += 1
            self._dropped("bid", bid.job_id, "window_closed")
            return
        window.append(bid)
        self.emit({"event": "bid_received", "node": self.name, "job_id": bid.job_id,
                   "from": bid.bidder_peer_id, "price": bid.price, "warm": bid.warm})

    async def _on_award(self, data: bytes, sender: str) -> None:
        try:
            award = JobAward.from_bytes(data)
        except Exception as exc:
            self.stats["awards_bad_parse"] += 1
            self._dropped("award", None, "bad_parse", f"{type(exc).__name__}: {exc}")
            return
        job = self.jobs.get(award.job_id)
        if job is None:
            # An award for a job we never saw cannot be attributed to a requester
            # key, so it is unverifiable rather than merely unknown.
            self.stats["awards_unknown_job"] += 1
            self._dropped("award", award.job_id, "unknown_job")
            return
        if not verify_message(award, job.requester_wallet):
            self.stats["awards_bad_sig"] += 1
            self._dropped("award", award.job_id, "bad_signature")
            return
        self.stats["awards_seen"] += 1
        self.awards[award.job_id] = award
        if award.winner_peer_id == self.peer_id:
            self.won.append(award)
            self.emit({"event": "won", "node": self.name, "job_id": award.job_id,
                       "clearing_price": award.clearing_price,
                       "own_bid": award.winning_bid_price})

    async def _on_commitment(self, data: bytes, sender: str) -> None:
        # Owned by the verification track; this node only relays and counts.
        return

    # -- plumbing --------------------------------------------------------

    async def _read_topic(self, topic: str, handler) -> None:
        sub = self.subs[topic]
        while True:
            msg = await sub.get()
            try:
                sender = str(ID(msg.from_id))
            except Exception as exc:
                # Without a sender we cannot tell our own echo from a peer's
                # message, so the self-suppression below is not applied. Count
                # it: a run where this fires has a requester that may be
                # scoring its own traffic, and that must be visible.
                self.stats["sender_unparsed"] += 1
                self.emit({"event": "sender_unparsed", "node": self.name,
                           "topic": topic, "error": f"{type(exc).__name__}: {exc}"})
                sender = ""
            if sender == self.peer_id:
                continue  # our own publish, echoed back to our subscription
            try:
                await handler(msg.data, sender)
            except Exception as exc:
                self.stats["handler_errors"] += 1
                self.emit({"event": "handler_error", "node": self.name,
                           "topic": topic, "error": f"{type(exc).__name__}: {exc}"})

    async def _connect_bootstrap(self) -> None:
        for addr in self.bootstrap:
            info = info_from_p2p_addr(Multiaddr(addr))
            try:
                await self.host.connect(info)
                await self.dht.add_peer(info.peer_id)
                self.stats["bootstrap_ok"] += 1
                self.emit({"event": "connected", "node": self.name,
                           "peer": str(info.peer_id)})
            except Exception as exc:
                self.stats["bootstrap_failed"] += 1
                self.emit({"event": "connect_failed", "node": self.name,
                           "addr": addr, "error": f"{type(exc).__name__}: {exc}"})

    async def _wait_for_mesh(self, topic: str = TOPIC_JOBS,
                             timeout_s: Optional[float] = None) -> bool:
        """Block until the gossipsub mesh for `topic` has a peer.

        Publishing before the mesh grafts is a silent no-op, which is exactly the
        failure this whole module exists to avoid."""
        deadline = trio.current_time() + (timeout_s if timeout_s is not None
                                          else self.mesh_wait_s)
        while trio.current_time() < deadline:
            if self.gossipsub.mesh.get(topic):
                return True
            await trio.sleep(0.1)
        return bool(self.gossipsub.mesh.get(topic))

    def mesh_size(self, topic: str = TOPIC_JOBS) -> int:
        return len(self.gossipsub.mesh.get(topic, ()))

    async def wait_for_peers(self, k: int, timeout_s: float,
                             topic: str = TOPIC_JOBS) -> bool:
        """Block until `k` peers are in the mesh for `topic`, or time out.

        Returns whether the target was reached; the caller must record a False
        rather than proceeding as if the network were complete."""
        deadline = trio.current_time() + timeout_s
        while trio.current_time() < deadline:
            if self.mesh_size(topic) >= k:
                return True
            await trio.sleep(0.05)
        return self.mesh_size(topic) >= k

    async def probe_records(self, peer_ids: list[str], *, attempts: int = 6,
                            delay_s: float = 0.5) -> list[dict]:
        """Look each peer up in the DHT and emit what came back, including
        whether it was already local or had to be fetched from a peer.

        A miss is retried a bounded number of times, because a peer that only
        just joined may not have published yet - propagation is not instant. The
        number of attempts used is recorded, so a slow lookup is visible in the
        results rather than hidden by the retry."""
        if attempts < 1:
            raise ValueError(f"attempts must be >= 1, got {attempts}")
        out = []
        for pid in peer_ids:
            if pid == self.peer_id:
                continue
            rec, source, attempt = None, "missing", 0
            for attempt in range(1, attempts + 1):
                rec, source = await self.lookup_record(pid)
                if rec is not None or attempt == attempts:
                    break
                await trio.sleep(delay_s)
            row = {"event": "dht_lookup", "peer": pid, "source": source,
                   "ok": rec is not None, "attempts": attempt,
                   "wallet": rec.wallet_address if rec else None,
                   "models": rec.models if rec else None,
                   "tier": int(rec.tier) if rec else None,
                   "multiaddrs": rec.multiaddrs if rec else None}
            self.emit(row)
            out.append(row)
        return out

    async def _maintenance_loop(self, interval_s: float = 5.0) -> None:
        """Keep the DHT routing table in step with the transport, republish our
        own record, and pull records for peers we have not resolved yet."""
        while True:
            try:
                for pid in self.host.get_connected_peers():
                    await self.dht.add_peer(pid)
                    if str(pid) not in self.records:
                        await self.lookup_record(str(pid))
                await self.publish_record()
            except Exception as exc:
                self.stats["maintenance_errors"] += 1
                self.emit({"event": "maintenance_error", "node": self.name,
                           "error": f"{type(exc).__name__}: {exc}"})
            await trio.sleep(interval_s)

    # -- lifecycle -------------------------------------------------------

    async def serve(self, *, ready: Optional[trio.Event] = None) -> None:
        """Start everything and run until cancelled."""
        # `announce_addrs` is what identify tells peers to dial us on. Without it
        # a node bound to 0.0.0.0 advertises /ip4/0.0.0.0/... and every peer's
        # DHT store-at-peer call fails against an unroutable address.
        self.host = new_host(
            key_pair=self.key_pair,
            announce_addrs=[Multiaddr(f"/ip4/{self.advertise_ip}/tcp/{self.port}")])
        listen = Multiaddr(f"/ip4/{self.listen_ip}/tcp/{self.port}")
        async with self.host.run(listen_addrs=[listen]):
            self.dht = KadDHT(self.host, DHTMode.SERVER)
            self.dht.register_validator(DHT_NAMESPACE, NodeRecordValidator())

            degree, degree_low, degree_high = self.gossip_degree
            self.gossipsub = GossipSub(
                protocols=[GOSSIPSUB_PROTOCOL_ID],
                degree=degree, degree_low=degree_low, degree_high=degree_high,
                heartbeat_interval=C.GOSSIP_HEARTBEAT_S,
                heartbeat_initial_delay=0.5,
            )
            # strict_signing off: authenticity is carried by our own secp256k1
            # signature inside every payload, checked against the wallet the
            # message claims, which is the key settlement actually uses.
            self.pubsub = Pubsub(self.host, self.gossipsub, strict_signing=False)

            # BOTH services. GossipSub.run() is not started by Pubsub, and
            # without it the heartbeat never fires and nothing is ever delivered.
            async with (background_trio_service(self.dht),
                        background_trio_service(self.pubsub),
                        background_trio_service(self.gossipsub)):
                await self.pubsub.wait_until_ready()
                # Subscribe before connecting, so peers learn our topics in the
                # first exchange rather than a heartbeat later.
                for topic in ALL_TOPICS:
                    self.subs[topic] = await self.pubsub.subscribe(topic)

                async with trio.open_nursery() as nursery:
                    nursery.start_soon(self._read_topic, TOPIC_JOBS, self._on_job)
                    nursery.start_soon(self._read_topic, TOPIC_BIDS, self._on_bid)
                    nursery.start_soon(self._read_topic, TOPIC_AWARDS, self._on_award)
                    nursery.start_soon(self._read_topic, TOPIC_COMMITMENTS,
                                       self._on_commitment)
                    nursery.start_soon(self.heartbeat.run)

                    await self._connect_bootstrap()
                    await self.publish_record()
                    meshed = await self._wait_for_mesh()
                    nursery.start_soon(self._maintenance_loop)

                    self.emit({
                        "event": "ready", "node": self.name, "role": self.role,
                        "peer_id": self.peer_id, "wallet": self.identity.address,
                        "multiaddr": self.dial_multiaddr(),
                        "hb_port": self.hb_port, "model": self.model,
                        "price": self.price, "warm": self.warm,
                        "forge_bids": self.forge_bids,
                        "tier": int(self.tier), "mesh": self.mesh_size(),
                        "meshed": meshed, "ttft_ms": self.ttft_ms,
                        "n_bootstrap": len(self.bootstrap),
                        "bootstrap_ok": self.stats["bootstrap_ok"],
                        "bootstrap_failed": self.stats["bootstrap_failed"],
                        "routing_table": self.dht.get_routing_table_size(),
                    })
                    self._ready.set()
                    if ready is not None:
                        ready.set()
                    await trio.sleep_forever()

    async def wait_ready(self) -> None:
        await self._ready.wait()

    def summary(self) -> dict:
        return {"event": "summary", "node": self.name, "peer_id": self.peer_id,
                "stats": dict(self.stats),
                "records_known": sorted(self.records),
                "heartbeat": dict(self.heartbeat.stats),
                "alive_peers": sorted(self.heartbeat.alive())}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="discovery.node", description=__doc__.splitlines()[0])
    p.add_argument("--name", required=True, help="identity name; keys persist under EDGEGRID_KEY_DIR")
    p.add_argument("--port", type=int, default=4001, help="libp2p TCP port")
    p.add_argument("--bootstrap", action="append", default=[],
                   help="peer multiaddr to dial on start (repeatable)")
    p.add_argument("--model", default=C.OLLAMA_MODEL, help="model this node serves / requests")
    p.add_argument("--price", type=float, default=0.05, help="bid price in GRID (provider)")
    p.add_argument("--role", choices=ROLES, default="both")
    p.add_argument("--listen-ip", default="0.0.0.0")
    p.add_argument("--advertise-ip", default="127.0.0.1",
                   help="address other nodes dial; must not be 0.0.0.0")
    p.add_argument("--hb-port", type=int, default=None, help="UDP heartbeat port (default port+10000)")
    p.add_argument("--key-dir", default=None)
    p.add_argument("--warm", action="store_true", help="model already resident in memory")
    p.add_argument("--tier", type=int, choices=[1, 2, 3], default=1)
    p.add_argument("--stake", type=float, default=C.MIN_STAKE)
    p.add_argument("--forge-bids", action="store_true",
                   help="sign bids with a throwaway key (to exercise rejection)")
    p.add_argument("--ttft-ms", type=float, default=1500.0, help="TTFT this node bids")
    p.add_argument("--bid-window", type=float, default=C.BID_WINDOW_S)
    p.add_argument("--mesh-wait", type=float, default=C.MESH_WAIT_S)
    p.add_argument("--wait-peers", type=int, default=0,
                   help="wait for this many mesh peers before probing / auctioning")
    p.add_argument("--wait-peers-timeout", type=float, default=20.0)
    p.add_argument("--dht-probe", action="append", default=[],
                   help="peer id to look up in the DHT once the mesh is up (repeatable)")
    p.add_argument("--job-after", type=float, default=None,
                   help="requester only: seconds after the mesh is up to run one auction")
    p.add_argument("--prompt", default="What is the capital of France? Answer in one word.")
    p.add_argument("--max-price", type=float, default=1.0,
                   help="requester reserve. This is a real ceiling, not a big "
                        "safe number: a lone bidder is paid exactly this")
    p.add_argument("--max-latency-ms", type=int, default=30_000)
    p.add_argument("--min-tier", type=int, choices=[1, 2, 3], default=1)
    p.add_argument("--ttl", type=float, default=0.0, help="exit after N seconds (0 = run forever)")
    return p


async def _main(args: argparse.Namespace) -> None:
    node = EdgeGridNode(
        args.name, args.port, bootstrap=args.bootstrap, listen_ip=args.listen_ip,
        advertise_ip=args.advertise_ip, model=args.model, price=args.price,
        role=args.role, warm=args.warm, tier=HardwareTier(args.tier),
        stake=args.stake, ttft_ms=args.ttft_ms, hb_port=args.hb_port,
        forge_bids=args.forge_bids,
        key_dir=args.key_dir, bid_window_s=args.bid_window, mesh_wait_s=args.mesh_wait,
    )
    async with trio.open_nursery() as nursery:
        nursery.start_soon(node.serve)
        await node.wait_ready()

        if args.wait_peers > 0:
            reached = await node.wait_for_peers(args.wait_peers, args.wait_peers_timeout)
            node.emit({"event": "mesh", "want_peers": args.wait_peers,
                       "mesh": node.mesh_size(), "reached": reached,
                       "routing_table": node.dht.get_routing_table_size()})

        if args.dht_probe:
            await node.probe_records(args.dht_probe)

        if args.job_after is not None and node.is_requester:
            await trio.sleep(args.job_after)
            await node.run_auction(
                args.prompt, max_price=args.max_price,
                max_latency_ms=args.max_latency_ms,
                min_tier=HardwareTier(args.min_tier))

        if args.ttl > 0:
            await trio.sleep(args.ttl)
            node.emit(node.summary())
            nursery.cancel_scope.cancel()
        else:
            await trio.sleep_forever()


def main(argv: Optional[list[str]] = None) -> None:
    args = build_parser().parse_args(argv)
    if args.advertise_ip == "0.0.0.0":
        raise SystemExit("--advertise-ip must be a dialable address, not 0.0.0.0")
    trio.run(_main, args)


if __name__ == "__main__":
    main()
