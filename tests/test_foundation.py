"""Tests for the shared foundation: schemas, identity, DA layer, run records.

Every other track codes against these four modules, so a regression here is a
regression everywhere. The DA and signature tests are adversarial on purpose:
they assert that forgery and tampering FAIL, which is the only property that
makes the verification module worth anything.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from pydantic import ValidationError

from edgegrid.da import DALayer, merkle_root, verify_proof
from edgegrid.identity import Identity, recover_address, verify, verify_message
from edgegrid.schemas import (
    Bid, Commitment, EscrowState, HardwareTier, InferenceResult, JobRequest,
    SettlementRecord, Verdict, VerdictKind, sha256_hex,
)


# ---------------------------------------------------------------- schemas

def test_extra_fields_are_rejected():
    """A track that adds a field must fail loudly, not drift silently."""
    with pytest.raises(ValidationError):
        JobRequest(prompt="p", model="m", requester_peer_id="x", surprise="boom")


def test_canonical_excludes_signature_and_is_order_stable():
    # created_ms is a now_ms() default factory, so two constructions differ
    # whenever the millisecond ticks between these two lines. Pin it: what is
    # under test is that `signature` is excluded, not the wall clock.
    a = JobRequest(job_id="j", prompt="p", model="m", requester_peer_id="x",
                   created_ms=1_700_000_000_000)
    b = JobRequest(job_id="j", prompt="p", model="m", requester_peer_id="x",
                   created_ms=1_700_000_000_000)
    b.signature = "deadbeef"
    assert a.canonical() == b.canonical(), "signature must not affect signed bytes"
    assert b'"signature"' not in a.canonical()
    # keys sorted -> byte-identical across constructions
    assert a.canonical() == JobRequest.from_bytes(a.to_bytes()).canonical()


def test_round_trip_preserves_enums():
    b = Bid(job_id="j", bidder_peer_id="p", price=0.5, estimated_ttft_ms=700.0,
            tier=HardwareTier.DISCRETE_GPU)
    assert Bid.from_bytes(b.to_bytes()).tier is HardwareTier.DISCRETE_GPU


def test_verdict_has_a_third_value():
    """ERROR must exist and be distinct. A judge outage is not a fraud finding."""
    assert {v.value for v in VerdictKind} == {"pass", "fail", "error"}
    v = Verdict(job_id="j", verdict=VerdictKind.ERROR, judge_backend="ollama",
                judge_model="qwen3-vl:2b-instruct")
    assert v.verdict is not VerdictKind.FAIL
    assert v.quality_score is None


def test_quality_score_is_bounded():
    for bad in (0, 6):
        with pytest.raises(ValidationError):
            Verdict(job_id="j", verdict=VerdictKind.PASS, quality_score=bad,
                    judge_backend="mock", judge_model="m")


def test_settlement_record_carries_the_split():
    r = SettlementRecord(job_id="j", provider_peer_id="p", amount=1.0,
                         state=EscrowState.SLASHED, slashed=True, slash_amount=1.0,
                         validator_reward=0.8, treasury_amount=0.2)
    assert r.validator_reward + r.treasury_amount == pytest.approx(r.slash_amount)


def test_output_hash_helper_matches_sha256():
    out = "Ocean tides are caused by the Moon."
    assert InferenceResult.hash_output(out) == hashlib.sha256(out.encode()).hexdigest()
    assert sha256_hex(out) == InferenceResult.hash_output(out)


# --------------------------------------------------------------- identity

def test_identity_is_deterministic_from_key():
    a, b = Identity.from_hex("aa" * 32), Identity.from_hex("aa" * 32)
    assert a.address == b.address and a.pubkey_hex == b.pubkey_hex


def test_address_is_checksummed_and_well_formed():
    addr = Identity.generate().address
    assert addr.startswith("0x") and len(addr) == 42
    assert addr != addr.lower(), "must be EIP-55 checksummed"


def test_load_or_create_persists_and_is_private(tmp_path):
    first = Identity.load_or_create("node-a", tmp_path)
    again = Identity.load_or_create("node-a", tmp_path)
    assert first.address == again.address, "identity must survive a restart"
    assert (tmp_path / "node-a.key").stat().st_mode & 0o077 == 0, "key must not be group/world readable"


def test_signature_verifies_and_recovers(job, requester):
    assert verify_message(job, requester.address)
    assert recover_address(job.canonical(), job.signature).lower() == requester.address.lower()


def test_tampering_invalidates_signature(job, requester):
    job.prompt = "something else entirely"
    assert not verify_message(job, requester.address)


def test_wrong_signer_is_rejected(job, provider):
    assert not verify_message(job, provider.address)


def test_malformed_signature_is_false_not_an_exception(requester):
    for junk in ("", "zz", "00" * 10, "not-hex-at-all"):
        assert verify(b"payload", junk, requester.address) is False


def test_unsigned_message_does_not_verify(requester):
    j = JobRequest(prompt="p", model="m", requester_peer_id="x")
    assert j.signature is None
    assert not verify_message(j, requester.address)


# --------------------------------------------------------------------- DA

def test_blob_round_trip_and_hash(da):
    payload = b"the moon causes tides"
    blob = da.submit_blob(payload)
    assert da.get_blob(blob.blob_id) == payload
    assert da.verify_blob(blob.blob_id, hashlib.sha256(payload).hexdigest())


def test_wrong_expected_hash_fails_verification(da):
    blob = da.submit_blob(b"honest output")
    assert not da.verify_blob(blob.blob_id, sha256_hex("different output"))


def test_missing_blob_fails_closed(da):
    assert da.get_blob("0" * 32) is None
    assert not da.verify_blob("0" * 32, sha256_hex("anything"))


@pytest.mark.parametrize("n", [1, 2, 3, 4, 5, 8, 9, 16, 17])
def test_inclusion_proofs_hold_for_any_batch_size(tmp_path, n):
    """Odd-length levels duplicate the tail; that path must still verify."""
    da = DALayer(tmp_path / f"da{n}")
    payloads = [f"output-{i}".encode() for i in range(n)]
    blobs = [da.submit_blob(p, seal=False) for p in payloads]
    da.seal_block()
    for payload, blob in zip(payloads, blobs):
        proof, root = da.inclusion_proof(blob.blob_id)
        assert verify_proof(payload, proof, root), f"n={n} failed for {payload!r}"


def test_proof_rejects_a_forged_payload(da):
    real = b"the honest answer"
    blob = da.submit_blob(real)
    proof, root = da.inclusion_proof(blob.blob_id)
    assert verify_proof(real, proof, root)
    assert not verify_proof(b"the forged answer", proof, root)


def test_proof_from_one_block_does_not_validate_in_another(tmp_path):
    da = DALayer(tmp_path / "da")
    a = da.submit_blob(b"block one payload")
    proof_a, root_a = da.inclusion_proof(a.blob_id)
    b = da.submit_blob(b"block two payload")
    _, root_b = da.inclusion_proof(b.blob_id)
    assert root_a != root_b
    assert not verify_proof(b"block one payload", proof_a, root_b)


def test_merkle_leaves_are_domain_separated():
    """A leaf must not be reinterpretable as an internal node."""
    x, y = b"a", b"b"
    two = merkle_root([x, y])
    # the concatenation of two leaf hashes must not itself hash to the same root
    assert merkle_root([x + y]) != two


def test_da_survives_reopen(tmp_path):
    d = tmp_path / "da"
    blob_id = DALayer(d).submit_blob(b"persisted output").blob_id
    reopened = DALayer(d)
    assert reopened.get_blob(blob_id) == b"persisted output"
    assert reopened.verify_blob(blob_id, sha256_hex("persisted output"))


def test_commitment_binds_output_to_blob(da, provider):
    out = "Nitrogen is about 78% of the atmosphere."
    blob = da.submit_blob(out)
    c = Commitment(job_id="j", provider_peer_id="p", output_hash=sha256_hex(out),
                   namespace=blob.namespace, blob_ref=blob.blob_id,
                   blob_height=blob.height)
    provider.sign_message(c)
    assert verify_message(c, provider.address)
    assert da.verify_blob(c.blob_ref, c.output_hash), "the whole point of the commitment"


# ----------------------------------------------------------------- runlog

def test_runlog_isolates_runs_and_records_provenance(tmp_path):
    from edgegrid.runlog import RunLog

    dirs = []
    for i in range(2):
        with RunLog("exp", {"i": i}, results_dir=tmp_path) as rl:
            rl.append("rows", {"n": i})
            dirs.append(rl.dir)
    assert dirs[0] != dirs[1], "a second run must never overwrite the first"
    for d in dirs:
        cfg = json.loads((d / "config.json").read_text())
        assert cfg["git_sha"] and "config" in cfg
        assert "GROQ_API_KEY" not in cfg["config"], "secrets must not be snapshotted"


def test_runlog_counts_dropped_rows(tmp_path):
    from edgegrid.runlog import RunLog

    with RunLog("exp", results_dir=tmp_path) as rl:
        rl.append("rows", {"a": 1})
        rl.append("rows", {"a": 2, "unexpected": 3})
        rl.drop("q9", "judge timed out")
    m = json.loads((rl.dir / "manifest.json").read_text())
    assert m["n_dropped"] == 2, "silent truncation is the bug this prevents"
    assert m["rows"]["rows"] == 2


def test_runlog_marks_failed_runs(tmp_path):
    from edgegrid.runlog import RunLog

    rl = RunLog("exp", results_dir=tmp_path)
    with pytest.raises(RuntimeError):
        with rl:
            raise RuntimeError("judge unreachable")
    m = json.loads((rl.dir / "manifest.json").read_text())
    assert m["status"] == "error" and "judge unreachable" in m["error"]
    assert RunLog.latest("exp", tmp_path) is None, "a failed run is not the latest good run"
