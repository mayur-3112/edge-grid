"""UDP liveness heartbeats.

The DHT and the heartbeat carry deliberately different things (Phase-1 design,
Module 1). A DHT record is slow, replicated and expensive to refresh: it holds
the facts that rarely change - wallet, tier, core count, which models a node is
willing to serve. Liveness is the opposite: it changes every few seconds, is only
interesting to nearby peers, and is worthless once stale. Pushing that through
Kademlia would republish records constantly and still hand you a value that was
true a minute ago.

So liveness rides on plain signed UDP datagrams at `C.HEARTBEAT_INTERVAL_S`. A
peer is alive if we heard from it inside the TTL, and its *warm* model set - the
one thing that actually moves the auction, via `C.WARM_START_BONUS` - comes from
here, never from the DHT.

Endpoints are learned, not configured: a node advertises `/ip4/<ip>/udp/<port>`
in its `NodeRecord.multiaddrs`, so the DHT bootstraps the heartbeat mesh and the
heartbeat mesh then tracks liveness on its own.

Every datagram is signed with the node's secp256k1 key. A datagram whose signer
does not match the wallet in that peer's DHT record is dropped and counted; one
from a peer we have no record for yet is accepted but flagged `verified=False`,
so a caller can tell "trusted and live" from "live, source unconfirmed".
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Optional

import psutil
import trio
from multiaddr import Multiaddr

from edgegrid import config as C
from edgegrid.identity import Identity, recover_address
from edgegrid.schemas import Heartbeat, now_ms

MAX_DATAGRAM = 8192


def udp_multiaddr(ip: str, port: int) -> str:
    return f"/ip4/{ip}/udp/{port}"


def parse_udp_endpoint(multiaddrs: list[str]) -> Optional[tuple[str, int]]:
    """First `/ip4/<ip>/udp/<port>` endpoint in a NodeRecord's multiaddr list."""
    for raw in multiaddrs:
        try:
            ma = Multiaddr(raw)
            ip = ma.value_for_protocol("ip4")
            port = ma.value_for_protocol("udp")
        except Exception:
            continue
        if ip and port:
            return ip, int(port)
    return None


def local_state(peer_id: str, seq: int, warm_models: list[str],
                inflight: int = 0,
                on_error: Optional[Callable[[str], None]] = None) -> Heartbeat:
    """Real machine state from psutil. Never fabricated - if psutil cannot read a
    figure the heartbeat says so by carrying 0.0 and `healthy=False`.

    A heartbeat of all-zeros is indistinguishable from a genuinely idle machine
    once it is on the wire, so `on_error` is how the sender records that the
    zeros are a psutil failure rather than a measurement. `HeartbeatService`
    wires it to a counter; a caller that passes nothing gets the zeros with no
    way to tell, which is why it is a parameter rather than a default."""
    try:
        vm = psutil.virtual_memory()
        load1 = psutil.getloadavg()[0]
        return Heartbeat(
            peer_id=peer_id,
            seq=seq,
            ram_available_gb=round(vm.available / (1024 ** 3), 3),
            vram_available_gb=0.0,  # no NVIDIA GPU on this host; reported, not guessed
            cpu_percent=psutil.cpu_percent(interval=None),
            load1=round(load1, 3),
            warm_models=list(warm_models),
            inflight=inflight,
            healthy=True,
        )
    except Exception as exc:
        if on_error is not None:
            on_error(f"{type(exc).__name__}: {exc}")
        return Heartbeat(peer_id=peer_id, seq=seq, warm_models=list(warm_models),
                         inflight=inflight, healthy=False)


@dataclass
class PeerLiveness:
    """What we currently believe about one peer, and how sure we are of it."""

    peer_id: str
    seq: int
    last_seen_ms: int
    warm_models: list[str]
    ram_available_gb: float
    vram_available_gb: float
    cpu_percent: float
    load1: float
    inflight: int
    healthy: bool
    verified: bool
    signer: str
    endpoint: tuple[str, int]

    def age_ms(self, now: Optional[int] = None) -> int:
        return (now if now is not None else now_ms()) - self.last_seen_ms


class HeartbeatService:
    """Sends our own heartbeat to every known peer endpoint and tracks theirs.

    `peers_fn` returns the current endpoint set as {peer_id: (ip, port)}; the node
    refreshes it from DHT records, so the service itself does no discovery.
    `wallet_fn` maps a peer id to its expected wallet address, or None if unknown.
    """

    def __init__(self, identity: Identity, peer_id: str, port: int, *,
                 bind_ip: str = "0.0.0.0",
                 peers_fn: Callable[[], dict[str, tuple[str, int]]] = dict,
                 wallet_fn: Callable[[str], Optional[str]] = lambda _p: None,
                 warm_models_fn: Callable[[], list[str]] = list,
                 inflight_fn: Callable[[], int] = lambda: 0,
                 interval_s: float = C.HEARTBEAT_INTERVAL_S,
                 ttl_s: Optional[float] = None):
        self.identity = identity
        self.peer_id = peer_id
        self.port = port
        self.bind_ip = bind_ip
        self.peers_fn = peers_fn
        self.wallet_fn = wallet_fn
        self.warm_models_fn = warm_models_fn
        self.inflight_fn = inflight_fn
        self.interval_s = interval_s
        self.ttl_s = ttl_s if ttl_s is not None else interval_s * 3
        self.seq = 0
        self.peers: dict[str, PeerLiveness] = {}
        self.stats = {"sent": 0, "recv": 0, "bad_parse": 0, "bad_signature": 0,
                      "unverified": 0, "self": 0, "send_errors": 0, "stale_seq": 0,
                      "local_state_errors": 0}
        # Last error text for the two counted-but-otherwise-opaque failures, so a
        # postmortem does not have to guess what "send_errors: 12" meant.
        self.last_send_error: Optional[str] = None
        self.last_local_state_error: Optional[str] = None
        self._sock: Optional[trio.socket.SocketType] = None
        self._ready = trio.Event()

    # -- lifecycle -------------------------------------------------------

    async def run(self) -> None:
        """Bind and serve until cancelled. Run inside a nursery."""
        sock = trio.socket.socket(trio.socket.AF_INET, trio.socket.SOCK_DGRAM)
        sock.setsockopt(trio.socket.SOL_SOCKET, trio.socket.SO_REUSEADDR, 1)
        await sock.bind((self.bind_ip, self.port))
        self._sock = sock
        self._ready.set()
        try:
            async with trio.open_nursery() as nursery:
                nursery.start_soon(self._recv_loop)
                nursery.start_soon(self._send_loop)
        finally:
            sock.close()
            self._sock = None

    async def wait_ready(self) -> None:
        await self._ready.wait()

    # -- send ------------------------------------------------------------

    def _note_local_state_error(self, err: str) -> None:
        self.stats["local_state_errors"] += 1
        self.last_local_state_error = err

    def build(self) -> Heartbeat:
        self.seq += 1
        hb = local_state(self.peer_id, self.seq, self.warm_models_fn(),
                         self.inflight_fn(), on_error=self._note_local_state_error)
        self.identity.sign_message(hb)
        return hb

    async def send_once(self) -> int:
        """One round of heartbeats. Returns how many datagrams went out."""
        endpoints = {pid: ep for pid, ep in self.peers_fn().items() if pid != self.peer_id}
        if not endpoints:
            return 0
        payload = self.build().to_bytes()
        sent = 0
        for ep in endpoints.values():
            try:
                await self._sock.sendto(payload, ep)
                sent += 1
            except Exception as exc:
                self.stats["send_errors"] += 1
                self.last_send_error = f"{ep}: {type(exc).__name__}: {exc}"
        self.stats["sent"] += sent
        return sent

    async def _send_loop(self) -> None:
        while True:
            await self.send_once()
            await trio.sleep(self.interval_s)

    # -- receive ---------------------------------------------------------

    async def _recv_loop(self) -> None:
        while True:
            data, addr = await self._sock.recvfrom(MAX_DATAGRAM)
            self.ingest(data, addr)

    def ingest(self, data: bytes, addr: tuple[str, int]) -> Optional[PeerLiveness]:
        """Validate and record one datagram. Every rejection is counted, never
        silent. Exposed separately from the socket so it can be unit tested."""
        try:
            hb = Heartbeat.from_bytes(data)
        except Exception:
            self.stats["bad_parse"] += 1
            return None
        if hb.peer_id == self.peer_id:
            self.stats["self"] += 1
            return None
        if not hb.signature:
            self.stats["bad_signature"] += 1
            return None
        try:
            signer = recover_address(hb.canonical(), hb.signature)
        except Exception:
            self.stats["bad_signature"] += 1
            return None

        expected = self.wallet_fn(hb.peer_id)
        verified = expected is not None and signer.lower() == expected.lower()
        if expected is not None and not verified:
            self.stats["bad_signature"] += 1
            return None
        if not verified:
            self.stats["unverified"] += 1

        prev = self.peers.get(hb.peer_id)
        if prev is not None and hb.seq < prev.seq and prev.signer == signer:
            # Out-of-order or replayed datagram; the newer state already stands.
            self.stats["stale_seq"] += 1
            return prev

        self.stats["recv"] += 1
        live = PeerLiveness(
            peer_id=hb.peer_id, seq=hb.seq, last_seen_ms=now_ms(),
            warm_models=list(hb.warm_models),
            ram_available_gb=hb.ram_available_gb,
            vram_available_gb=hb.vram_available_gb,
            cpu_percent=hb.cpu_percent, load1=hb.load1, inflight=hb.inflight,
            healthy=hb.healthy, verified=verified, signer=signer, endpoint=addr,
        )
        self.peers[hb.peer_id] = live
        return live

    # -- queries ---------------------------------------------------------

    def alive(self, ttl_s: Optional[float] = None) -> dict[str, PeerLiveness]:
        ttl_ms = int((ttl_s if ttl_s is not None else self.ttl_s) * 1000)
        now = now_ms()
        return {p: L for p, L in self.peers.items()
                if L.healthy and L.age_ms(now) <= ttl_ms}

    def warm_models_of(self, peer_id: str, ttl_s: Optional[float] = None, *,
                       require_verified: bool = True) -> list[str]:
        """The peer's warm set, empty if we have no signed claim to it.

        This is the one heartbeat field that moves money: it feeds
        `C.WARM_START_BONUS`, which is a discount on a rival's score. A peer we
        have no DHT record for is tracked as live but `verified=False`, and
        taking its unverified word for "I am warm" would let anyone who can
        send a UDP packet hand themselves a 15% edge. Ask for it explicitly
        with `require_verified=False` if you only want liveness telemetry."""
        live = self.alive(ttl_s).get(peer_id)
        if live is None or (require_verified and not live.verified):
            return []
        return list(live.warm_models)

    def is_alive(self, peer_id: str, ttl_s: Optional[float] = None) -> bool:
        return peer_id in self.alive(ttl_s)


async def _selftest() -> None:
    """Two services on loopback exchanging real signed datagrams."""
    a_id, b_id = Identity.generate(), Identity.generate()
    a_peer, b_peer = "peerA", "peerB"
    pa, pb = 46810, 46811
    endpoints = {a_peer: ("127.0.0.1", pa), b_peer: ("127.0.0.1", pb)}
    wallets = {a_peer: a_id.address, b_peer: b_id.address}

    a = HeartbeatService(a_id, a_peer, pa, bind_ip="127.0.0.1",
                         peers_fn=lambda: endpoints, wallet_fn=wallets.get,
                         warm_models_fn=lambda: ["qwen3-vl:2b-instruct"],
                         interval_s=0.5)
    b = HeartbeatService(b_id, b_peer, pb, bind_ip="127.0.0.1",
                         peers_fn=lambda: endpoints, wallet_fn=wallets.get,
                         interval_s=0.5)

    async with trio.open_nursery() as nursery:
        nursery.start_soon(a.run)
        nursery.start_soon(b.run)
        await a.wait_ready()
        await b.wait_ready()
        await trio.sleep(1.6)
        print("A sees:", {p: (L.seq, L.verified, L.warm_models) for p, L in a.alive().items()})
        print("B sees:", {p: (L.seq, L.verified, L.warm_models) for p, L in b.alive().items()})
        print("A stats:", a.stats)
        print("B stats:", b.stats)
        assert b.is_alive(a_peer) and a.is_alive(b_peer), "heartbeats did not flow"
        assert b.warm_models_of(a_peer) == ["qwen3-vl:2b-instruct"]
        nursery.cancel_scope.cancel()
    print("heartbeat selftest OK")


if __name__ == "__main__":
    trio.run(_selftest)
