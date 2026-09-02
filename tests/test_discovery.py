"""Tests for the parts of the discovery layer that do not need a live network.

The three-node end-to-end path is exercised by `python -m discovery.run_network`;
what is pinned down here is everything that decides whether a *hostile* message
is accepted: the DHT namespace validator, heartbeat signature handling, and the
determinism of a node's PeerID across restarts.
"""

from __future__ import annotations

import pytest
import trio
from libp2p.crypto.secp256k1 import create_new_key_pair
from libp2p.peer.id import ID
from libp2p.records.utils import InvalidRecordType

from discovery.heartbeat import HeartbeatService, parse_udp_endpoint, udp_multiaddr
from discovery.node import EdgeGridNode, NodeRecordValidator
from edgegrid import market
from edgegrid.identity import Identity
from edgegrid.schemas import (DHT_NODE_PREFIX, TOPIC_BIDS, Bid, HardwareTier,
                              Heartbeat, JobAward, JobRequest, NodeRecord)


def make_record(ident: Identity, peer_id: str, *, updated_ms: int = 1_000,
                sign: bool = True, **kw) -> NodeRecord:
    rec = NodeRecord(
        peer_id=peer_id, wallet_address=ident.address, pubkey_hex=ident.pubkey_hex,
        multiaddrs=[f"/ip4/127.0.0.1/tcp/4001/p2p/{peer_id}",
                    udp_multiaddr("127.0.0.1", 14001)],
        tier=HardwareTier.CPU, models=["qwen3-vl:2b-instruct"],
        updated_ms=updated_ms, **kw)
    if sign:
        ident.sign_message(rec)
    return rec


# --------------------------------------------------------------------------
# identity / peer id
# --------------------------------------------------------------------------

def test_peer_id_is_stable_across_restarts(tmp_path):
    """A node that restarts must keep its PeerID, or it loses its DHT key, its
    reputation and its stake. The libp2p key is derived from the persisted
    secp256k1 identity precisely so that holds."""
    first = Identity.load_or_create("node-restart", tmp_path)
    second = Identity.load_or_create("node-restart", tmp_path)
    assert first.seed_bytes == second.seed_bytes
    pid = lambda i: str(ID.from_pubkey(create_new_key_pair(i.seed_bytes).public_key))
    assert pid(first) == pid(second)
    assert pid(first) != pid(Identity.load_or_create("node-other", tmp_path))


# --------------------------------------------------------------------------
# DHT record validation
# --------------------------------------------------------------------------

def test_validator_accepts_a_correctly_signed_record():
    ident = Identity.generate()
    rec = make_record(ident, "peer-1")
    NodeRecordValidator().validate(DHT_NODE_PREFIX + "peer-1", rec.to_bytes())


def test_validator_rejects_an_unsigned_record():
    rec = make_record(Identity.generate(), "peer-1", sign=False)
    with pytest.raises(InvalidRecordType):
        NodeRecordValidator().validate(DHT_NODE_PREFIX + "peer-1", rec.to_bytes())


def test_validator_rejects_a_record_signed_by_another_key():
    """Otherwise anyone could publish a record claiming someone else's wallet and
    redirect that node's payments."""
    victim, impostor = Identity.generate(), Identity.generate()
    rec = make_record(victim, "peer-1", sign=False)
    impostor.sign_message(rec)
    with pytest.raises(InvalidRecordType):
        NodeRecordValidator().validate(DHT_NODE_PREFIX + "peer-1", rec.to_bytes())


def test_validator_rejects_a_record_stored_under_the_wrong_key():
    ident = Identity.generate()
    rec = make_record(ident, "peer-1")
    with pytest.raises(InvalidRecordType):
        NodeRecordValidator().validate(DHT_NODE_PREFIX + "peer-2", rec.to_bytes())


def test_validator_rejects_a_tampered_record():
    ident = Identity.generate()
    rec = make_record(ident, "peer-1")
    rec.stake = 10_000.0
    with pytest.raises(InvalidRecordType):
        NodeRecordValidator().validate(DHT_NODE_PREFIX + "peer-1", rec.to_bytes())


def test_validator_rejects_a_foreign_namespace_and_garbage():
    v = NodeRecordValidator()
    rec = make_record(Identity.generate(), "peer-1")
    with pytest.raises(InvalidRecordType):
        v.validate("/pk/peer-1", rec.to_bytes())
    with pytest.raises(InvalidRecordType):
        v.validate(DHT_NODE_PREFIX + "peer-1", b"not json")


def test_validator_select_prefers_the_newest_record():
    ident = Identity.generate()
    old = make_record(ident, "peer-1", updated_ms=1_000).to_bytes()
    new = make_record(ident, "peer-1", updated_ms=2_000).to_bytes()
    v = NodeRecordValidator()
    assert v.select(DHT_NODE_PREFIX + "peer-1", [new, old]) == 0
    assert v.select(DHT_NODE_PREFIX + "peer-1", [old, new]) == 1


# --------------------------------------------------------------------------
# heartbeat
# --------------------------------------------------------------------------

def test_parse_udp_endpoint_picks_the_udp_multiaddr():
    rec = make_record(Identity.generate(), "peer-1")
    assert parse_udp_endpoint(rec.multiaddrs) == ("127.0.0.1", 14001)
    assert parse_udp_endpoint(["/ip4/127.0.0.1/tcp/4001"]) is None
    assert parse_udp_endpoint(["nonsense"]) is None


def _service(known: dict[str, str]) -> HeartbeatService:
    return HeartbeatService(Identity.generate(), "me", 0, wallet_fn=known.get)


def _beat(ident: Identity, peer_id: str, seq: int, warm=("m",)) -> bytes:
    hb = Heartbeat(peer_id=peer_id, seq=seq, warm_models=list(warm))
    ident.sign_message(hb)
    return hb.to_bytes()


def test_heartbeat_from_a_known_peer_is_verified():
    peer = Identity.generate()
    svc = _service({"peer-1": peer.address})
    live = svc.ingest(_beat(peer, "peer-1", 1), ("127.0.0.1", 1))
    assert live is not None and live.verified
    assert svc.warm_models_of("peer-1") == ["m"]
    assert svc.stats["recv"] == 1 and svc.stats["unverified"] == 0


def test_heartbeat_signed_by_the_wrong_key_is_dropped_and_counted():
    peer, impostor = Identity.generate(), Identity.generate()
    svc = _service({"peer-1": peer.address})
    assert svc.ingest(_beat(impostor, "peer-1", 1), ("127.0.0.1", 1)) is None
    assert svc.stats["bad_signature"] == 1
    assert svc.peers == {}


def test_heartbeat_from_an_unknown_peer_is_flagged_not_trusted():
    """We have no DHT record for this peer yet, so liveness is usable but its
    origin is unconfirmed. Recorded as such rather than silently trusted."""
    svc = _service({})
    live = svc.ingest(_beat(Identity.generate(), "peer-x", 1), ("127.0.0.1", 1))
    assert live is not None and not live.verified
    assert svc.stats["unverified"] == 1


def test_unsigned_and_unparseable_heartbeats_are_counted():
    svc = _service({})
    assert svc.ingest(b"{}", ("127.0.0.1", 1)) is None
    assert svc.stats["bad_parse"] == 1
    assert svc.ingest(Heartbeat(peer_id="p", seq=1).to_bytes(), ("127.0.0.1", 1)) is None
    assert svc.stats["bad_signature"] == 1


def test_replayed_older_heartbeat_does_not_overwrite_newer_state():
    peer = Identity.generate()
    svc = _service({"peer-1": peer.address})
    svc.ingest(_beat(peer, "peer-1", 5, warm=("new",)), ("127.0.0.1", 1))
    svc.ingest(_beat(peer, "peer-1", 2, warm=("old",)), ("127.0.0.1", 1))
    assert svc.peers["peer-1"].seq == 5
    assert svc.warm_models_of("peer-1") == ["new"]
    assert svc.stats["stale_seq"] == 1


def test_liveness_expires_with_the_ttl():
    peer = Identity.generate()
    svc = _service({"peer-1": peer.address})
    svc.ingest(_beat(peer, "peer-1", 1), ("127.0.0.1", 1))
    assert svc.is_alive("peer-1")
    svc.peers["peer-1"].last_seen_ms -= int(svc.ttl_s * 1000) + 1
    assert not svc.is_alive("peer-1")
    assert svc.warm_models_of("peer-1") == []


def test_own_heartbeat_is_ignored():
    ident = Identity.generate()
    svc = HeartbeatService(ident, "me", 0)
    assert svc.ingest(_beat(ident, "me", 1), ("127.0.0.1", 1)) is None
    assert svc.stats["self"] == 1


# --------------------------------------------------------------------------
# node message handlers
#
# These are the paths a hostile peer actually reaches, and until now they were
# only ever exercised by the live harness - where a message that is quietly
# discarded looks exactly like a message that was never sent. Each test below
# asserts both halves of the contract: the message does not take effect, AND
# the node leaves a counted, emitted trace of having thrown it away.
# --------------------------------------------------------------------------

class FakePubsub:
    """Collects publishes instead of putting them on a mesh."""

    def __init__(self):
        self.published: list[tuple[str, bytes]] = []

    async def publish(self, topic: str, data: bytes) -> None:
        self.published.append((topic, data))


def make_node(tmp_path, name="handler-node", **kw):
    node = EdgeGridNode(name, 49999, key_dir=str(tmp_path), **kw)
    node.events = []
    node._emit_fn = node.events.append
    node.pubsub = FakePubsub()
    return node


def signed_job(ident: Identity, *, model="qwen3-vl:2b-instruct", **kw) -> JobRequest:
    job = JobRequest(prompt="p", model=model, requester_peer_id="peer-req",
                     requester_wallet=ident.address, **kw)
    ident.sign_message(job)
    return job


def dropped(node, why: str) -> list[dict]:
    return [e for e in node.events if e.get("event") == "dropped" and e.get("why") == why]


def test_node_bids_on_a_valid_job(tmp_path):
    node = make_node(tmp_path, model="qwen3-vl:2b-instruct", price=0.07)
    job = signed_job(Identity.generate())
    trio.run(node._on_job, job.to_bytes(), "peer-req")
    assert node.stats["bids_sent"] == 1
    topic, raw = node.pubsub.published[0]
    bid = Bid.from_bytes(raw)
    assert topic == TOPIC_BIDS
    assert bid.job_id == job.job_id and bid.price == 0.07
    # The bid must verify against the wallet it claims, or a requester drops it.
    assert market.exclusion_reason(bid, job) is None


def test_job_with_a_forged_signature_is_counted_and_emitted(tmp_path):
    node = make_node(tmp_path)
    victim, impostor = Identity.generate(), Identity.generate()
    job = JobRequest(prompt="p", model=node.model, requester_peer_id="peer-req",
                     requester_wallet=victim.address)
    impostor.sign_message(job)
    trio.run(node._on_job, job.to_bytes(), "peer-req")
    assert node.stats["jobs_bad_sig"] == 1
    assert node.stats["bids_sent"] == 0
    assert len(dropped(node, "bad_signature")) == 1


def test_unparseable_messages_are_emitted_not_only_counted(tmp_path):
    """Parse failures used to bump a counter and return. The harness reads the
    `dropped` stream, so a garbage-flooded node showed up as a quiet one."""
    node = make_node(tmp_path)
    trio.run(node._on_job, b"not json", "peer-x")
    trio.run(node._on_bid, b"{]", "peer-x")
    trio.run(node._on_award, b"", "peer-x")
    assert node.stats["jobs_bad_parse"] == 1
    assert node.stats["bids_bad_parse"] == 1
    assert node.stats["awards_bad_parse"] == 1
    assert len(dropped(node, "bad_parse")) == 3
    assert {e["kind"] for e in dropped(node, "bad_parse")} == {"job", "bid", "award"}


def test_node_declines_a_job_for_a_model_it_does_not_serve(tmp_path):
    node = make_node(tmp_path, model="qwen3-vl:2b-instruct")
    job = signed_job(Identity.generate(), model="some-other-model")
    trio.run(node._on_job, job.to_bytes(), "peer-req")
    assert node.stats["jobs_declined_model"] == 1
    assert node.stats["bids_sent"] == 0
    assert [e for e in node.events if e.get("event") == "declined"]


def test_forged_bid_never_reaches_the_auction(tmp_path):
    """The live counterpart of this is `--forge-nodes`: a provider signing with
    a throwaway key while claiming its real wallet."""
    node = make_node(tmp_path, role="requester")
    job = signed_job(node.identity)
    job.requester_peer_id = node.peer_id
    node.identity.sign_message(job)
    node.jobs[job.job_id] = job
    node.open_auctions[job.job_id] = []

    victim, impostor = Identity.generate(), Identity.generate()
    bid = market.bid_for(job, peer_id="p-forged", wallet=victim.address,
                         price=0.001, estimated_ttft_ms=10.0)
    impostor.sign_message(bid)
    trio.run(node._on_bid, bid.to_bytes(), "p-forged")
    assert node.open_auctions[job.job_id] == []
    assert node.stats["bids_bad_sig"] == 1
    assert len(dropped(node, "bad_signature")) == 1


def test_bid_after_the_window_closes_is_counted_late(tmp_path):
    node = make_node(tmp_path, role="requester")
    job = signed_job(node.identity)
    job.requester_peer_id = node.peer_id
    node.identity.sign_message(job)
    node.jobs[job.job_id] = job   # window deliberately never opened

    ident = Identity.generate()
    bid = market.bid_for(job, peer_id="p", wallet=ident.address, price=0.05,
                         estimated_ttft_ms=100.0)
    ident.sign_message(bid)
    trio.run(node._on_bid, bid.to_bytes(), "p")
    assert node.stats["bids_late"] == 1
    assert len(dropped(node, "window_closed")) == 1


def test_bid_for_an_unknown_job_is_recorded(tmp_path):
    node = make_node(tmp_path, role="requester")
    other = signed_job(Identity.generate())
    ident = Identity.generate()
    bid = market.bid_for(other, peer_id="p", wallet=ident.address, price=0.05,
                         estimated_ttft_ms=100.0)
    ident.sign_message(bid)
    trio.run(node._on_bid, bid.to_bytes(), "p")
    assert node.stats["bids_unknown_job"] == 1
    assert len(dropped(node, "unknown_job")) == 1


def test_award_signed_by_someone_other_than_the_requester_is_rejected(tmp_path):
    """Otherwise any peer could publish an award naming itself the winner and
    collect on a job it never bid for."""
    node = make_node(tmp_path)
    requester = Identity.generate()
    job = signed_job(requester)
    node.jobs[job.job_id] = job

    award = JobAward(job_id=job.job_id, winner_peer_id=node.peer_id,
                     clearing_price=0.09, winning_bid_price=0.07, n_bids=2,
                     auction_ms=2000.0)
    Identity.generate().sign_message(award)
    trio.run(node._on_award, award.to_bytes(), "peer-req")
    assert node.stats["awards_bad_sig"] == 1
    assert node.won == []
    assert len(dropped(node, "bad_signature")) == 1

    requester.sign_message(award)
    trio.run(node._on_award, award.to_bytes(), "peer-req")
    assert node.won and node.won[0].clearing_price == 0.09


def test_award_for_an_unseen_job_cannot_be_verified(tmp_path):
    node = make_node(tmp_path)
    award = JobAward(job_id="never-seen", winner_peer_id=node.peer_id,
                     clearing_price=0.09, winning_bid_price=0.07, n_bids=1,
                     auction_ms=1.0)
    Identity.generate().sign_message(award)
    trio.run(node._on_award, award.to_bytes(), "peer-x")
    assert node.stats["awards_unknown_job"] == 1
    assert node.won == []


def test_probe_records_rejects_a_zero_attempt_budget(tmp_path):
    """It used to fall through the loop and raise NameError on `rec`."""
    node = make_node(tmp_path)
    with pytest.raises(ValueError):
        trio.run(lambda: node.probe_records(["peer-x"], attempts=0))


def test_unverified_peers_cannot_claim_a_warm_start_bonus():
    """`warm_models_of` feeds C.WARM_START_BONUS, which is a discount on a
    rival's score. A peer we hold no signed record for is live-but-unverified,
    and taking its word for `warm` would sell a 15% edge for one UDP packet."""
    stranger = Identity.generate()
    svc = HeartbeatService(Identity.generate(), "me", 0)   # no known wallets
    live = svc.ingest(_beat(stranger, "peer-x", 1, warm=("m",)), ("127.0.0.1", 1))
    assert live is not None and not live.verified
    assert svc.is_alive("peer-x")                       # liveness still usable
    assert svc.warm_models_of("peer-x") == []           # but not its warm claim
    assert svc.warm_models_of("peer-x", require_verified=False) == ["m"]


def test_local_state_failure_is_recorded_not_passed_off_as_an_idle_machine(monkeypatch):
    """All-zero psutil readings are indistinguishable from an idle host once
    they are on the wire, so the sender has to count the failure."""
    import discovery.heartbeat as hbmod

    def boom():
        raise OSError("psutil unavailable")

    monkeypatch.setattr(hbmod.psutil, "virtual_memory", boom)
    svc = HeartbeatService(Identity.generate(), "me", 0)
    hb = svc.build()
    assert hb.healthy is False
    assert svc.stats["local_state_errors"] == 1
    assert "psutil unavailable" in svc.last_local_state_error


# --------------------------------------------------------------------------
# the run_network harness itself
#
# The harness is where a run's numbers are assembled, so a fault it hides is a
# fault that reaches the results table. These use a real subprocess, because
# what is being checked is exactly the parts a mock would paper over.
# --------------------------------------------------------------------------

import os
import subprocess
import sys

from discovery import run_network


def _tiny_node(tmp_path, script: str) -> run_network.NodeProc:
    env = dict(os.environ)
    proc = run_network.NodeProc("tiny", [sys.executable, "-c", script], tmp_path, env)
    proc.proc.wait(timeout=30)
    proc._reader.join(timeout=10)
    proc.stop()
    return proc


def test_nodeproc_counts_stdout_it_cannot_parse(tmp_path):
    """Non-JSON on a node's stdout used to be dropped on the floor, so a node
    printing warnings and a node printing nothing looked identical."""
    proc = _tiny_node(tmp_path, (
        'print(\'{"event": "ready", "peer_id": "p"}\'); '
        'print("libp2p: something went wrong"); print("[1, 2]")'))
    assert proc.find("ready") == {"event": "ready", "peer_id": "p"}
    assert proc.health()["unparsed_stdout"] == 2


def test_nodeproc_records_a_nonzero_exit(tmp_path):
    """A node that dies mid-run just stops bidding. Without the exit code the
    auction merely looks thin, with nothing saying why."""
    proc = _tiny_node(tmp_path, 'import sys; sys.exit(3)')
    h = proc.health()
    assert h["returncode"] == 3 and h["exited_early"] is True


def test_nodeproc_records_stderr_volume(tmp_path):
    proc = _tiny_node(tmp_path, 'import sys; sys.stderr.write("Value not found\\n")')
    assert proc.health()["stderr_bytes"] == len("Value not found\n")


def test_count_drops_groups_wire_rejections_by_kind_and_reason(tmp_path):
    proc = _tiny_node(tmp_path, (
        'print(\'{"event": "dropped", "kind": "bid", "why": "bad_signature", '
        '"job_id": "J"}\'); '
        'print(\'{"event": "dropped", "kind": "bid", "why": "bad_signature", '
        '"job_id": "J"}\'); '
        'print(\'{"event": "dropped", "kind": "job", "why": "bad_parse", '
        '"job_id": null}\'); '
        'print(\'{"event": "dropped", "kind": "bid", "why": "window_closed", '
        '"job_id": "OTHER"}\')'))
    assert run_network._count_drops(proc, "J") == {
        "bid:bad_signature": 2, "job:bad_parse": 1}


def test_summarize_pools_runs_and_reports_spread(tmp_path):
    """The README table is generated, not transcribed. What matters is that
    pooling reaches across run directories and that a failed auction is
    excluded from the statistics while still being counted as a source."""
    from discovery import summarize

    header = "n_nodes,repeat,ok,n_eligible,last_bid_ms\n"
    (tmp_path / "exp-a").mkdir()
    (tmp_path / "exp-a" / "auctions.csv").write_text(
        header + "3,0,True,2,10\n3,1,True,2,20\n")
    (tmp_path / "exp-b").mkdir()
    (tmp_path / "exp-b" / "auctions.csv").write_text(
        header + "3,0,True,2,30\n3,1,False,0,\n")

    rows = summarize.load_runs("exp", tmp_path)
    assert len(rows) == 4
    stats, sources = summarize.summarize(rows)
    assert [s["run_id"] for s in sources] == ["exp-a", "exp-b"]
    assert sum(s["failed"] for s in sources) == 1
    assert len(stats) == 1
    assert stats[0]["n_auctions"] == 3            # the failed one is not averaged
    assert stats[0]["n_runs"] == 2
    assert stats[0]["last_bid_ms_mean"] == 20.0   # mean of 10, 20, 30
    assert stats[0]["last_bid_ms_sd"] == 10.0
    assert "±" in summarize.markdown(stats)


def test_summarize_ignores_a_directory_with_no_auctions(tmp_path):
    from discovery import summarize

    (tmp_path / "exp-empty").mkdir()
    assert summarize.load_runs("exp", tmp_path) == []


def test_validator_select_refuses_to_pick_a_garbage_record():
    """`select` used to return index 0 when nothing parsed, handing the caller
    the garbage record as the DHT's best known value."""
    v = NodeRecordValidator()
    with pytest.raises(InvalidRecordType):
        v.select(DHT_NODE_PREFIX + "peer-1", [b"junk", b"also junk"])
    good = make_record(Identity.generate(), "peer-1", updated_ms=5).to_bytes()
    assert v.select(DHT_NODE_PREFIX + "peer-1", [b"junk", good]) == 1
