"""Tests for the chain backend.

The error paths are the point of most of these. A settlement layer that quietly
degrades from a real chain to a simulation produces numbers nobody can
interpret, so every way the chain can be unusable has to raise, say why, and
never hand back a `Ledger` instead.

The on-chain integration tests skip - loudly, with the reason - when no node is
reachable. They are never silently replaced by a simulation.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest

from edgegrid import config as C
from edgegrid import da
from edgegrid.chain import (
    ChainBackend,
    ChainIdMismatch,
    DeploymentNotFound,
    RpcUnreachable,
    chain_available,
    devnet_lock,
    get_backend,
    job_key,
)
from edgegrid.ledger import Ledger, ManualClock, from_wei, to_wei
from edgegrid.schemas import EscrowState, VerdictKind
from web3.exceptions import ContractLogicError

CHAIN_OK, CHAIN_WHY = chain_available()
requires_chain = pytest.mark.skipif(
    not CHAIN_OK, reason=f"no chain backend available: {CHAIN_WHY}")

STAKE = 10.0
PRICE = 0.05


def _job(prefix: str) -> str:
    """A job id unique per test *and per process*. Deriving it from the block
    number, as these tests used to, collides when two sessions share one devnet
    - and the collision shows up as an unrelated EscrowExists revert."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


# --------------------------------------------------------------------------
# backend selection - no silent fallback
# --------------------------------------------------------------------------

class TestBackendSelection:
    def test_sim_backend_is_the_local_ledger(self):
        b = get_backend("sim", min_stake=1.0)
        assert isinstance(b, Ledger)
        assert b.backend == "sim"

    def test_an_unknown_preference_raises(self):
        with pytest.raises(ValueError, match="chain.*sim"):
            get_backend("auto")

    def test_missing_deployment_raises_instead_of_simulating(self, tmp_path):
        with pytest.raises(DeploymentNotFound) as e:
            get_backend("chain", deployment_file=tmp_path / "nope.json")
        assert "deploy.js" in str(e.value)          # the error says what to do

    def test_unreachable_rpc_raises(self, tmp_path):
        if not C.DEPLOYMENT_FILE.exists():
            pytest.skip("no deployment.json to point at a dead RPC")
        with pytest.raises(RpcUnreachable) as e:
            get_backend("chain", rpc_url="http://127.0.0.1:1", 
                        deployment_file=C.DEPLOYMENT_FILE)
        assert "hardhat node" in str(e.value)

    @requires_chain
    def test_wrong_chain_id_raises(self, tmp_path):
        payload = json.loads(C.DEPLOYMENT_FILE.read_text())
        payload["chainId"] = payload["chainId"] + 1
        f = tmp_path / "deployment.json"
        f.write_text(json.dumps(payload))
        with pytest.raises(ChainIdMismatch):
            ChainBackend(deployment_file=f)

    @requires_chain
    def test_stale_addresses_are_detected(self, tmp_path):
        payload = json.loads(C.DEPLOYMENT_FILE.read_text())
        payload["contracts"]["NodeRegistry"] = "0x000000000000000000000000000000000000dEaD"
        f = tmp_path / "deployment.json"
        f.write_text(json.dumps(payload))
        from edgegrid.chain import ContractMissing
        with pytest.raises(ContractMissing):
            ChainBackend(deployment_file=f)


def test_job_key_is_stable_and_distinct():
    assert job_key("job-1") == job_key("job-1")
    assert job_key("job-1") != job_key("job-2")
    assert len(job_key("job-1")) == 32


# --------------------------------------------------------------------------
# on-chain integration
# --------------------------------------------------------------------------

# Accounts 0-5 belong to the deployer, the treasury and
# contracts/scripts/lifecycle.py; these tests take the next four.
_ROLE_BASE = 6


@pytest.fixture(scope="session")
def devnet():
    """Exclusive use of the devnet for the whole session.

    These tests step the chain clock with `evm_increaseTime`, which moves it for
    every other process on the node, and they send from node-unlocked accounts
    another session could also be using. Two sessions sharing one devnet
    produced deadline and nonce failures that looked like real defects; the lock
    is the only way to make them independent.
    """
    with devnet_lock():
        yield


@pytest.fixture
def chain(devnet):
    backend = ChainBackend()
    w3 = backend.w3
    accounts = [w3.to_checksum_address(a) for a in w3.eth.accounts]
    assert len(accounts) >= _ROLE_BASE + 4, (
        f"the node offers {len(accounts)} accounts, these tests need {_ROLE_BASE + 4}")
    roles = {"requester": accounts[_ROLE_BASE], "provider": accounts[_ROLE_BASE + 1],
             "watcher": accounts[_ROLE_BASE + 2], "validator": accounts[_ROLE_BASE + 3]}
    if not backend.is_active(roles["provider"]):
        backend.stake(roles["provider"], STAKE)
    return backend, roles


def _da_block(tmp_path, payload: bytes, name: str):
    layer = da.DALayer(root_dir=tmp_path / name)
    for filler in (b"neighbour one", b"neighbour two"):
        layer.submit_blob(filler, seal=False)
    blob = layer.submit_blob(payload, seal=False)
    layer.seal_block()
    meta = layer.get_blob_meta(blob.blob_id)
    proof, root = layer.inclusion_proof(blob.blob_id)
    return meta, proof, root


@requires_chain
class TestOnChainLifecycle:
    def test_settles_to_the_provider_after_the_challenge_window(self, chain, tmp_path):
        backend, roles = chain
        job = _job("pytest-honest")
        meta, proof, root = _da_block(tmp_path, b"the honest output", "honest")

        backend.open_escrow(job, roles["requester"], roles["provider"], PRICE)
        backend.record_commitment(job, roles["provider"], meta.commitment,
                                  meta.blob_id, root, meta.index)
        assert backend.escrow_state(job) is EscrowState.AWAITING_VERIFICATION

        with pytest.raises(ContractLogicError, match="ChallengeWindowOpen"):
            backend.release(job, sender=roles["requester"])

        backend.w3.provider.make_request("evm_increaseTime", [backend.challenge_window_s + 60])
        backend.w3.provider.make_request("evm_mine", [])

        rec = backend.release(job, sender=roles["requester"])
        assert rec.state is EscrowState.SETTLED
        assert rec.provider_payout == PRICE
        assert rec.tx_hash and rec.gas_used > 0        # real transaction, real gas
        backend.check_invariants()

    def test_da_proof_from_python_is_verified_by_the_evm(self, chain, tmp_path):
        """The Merkle proof is produced by edgegrid.da and checked in Solidity.
        If the two hash schemes ever drift, this is what catches it."""
        backend, roles = chain
        job = _job("pytest-fraud")
        meta, proof, root = _da_block(tmp_path, b"what the provider really produced", "fraud")
        claimed = hashlib.sha256(b"what the provider claimed to produce").hexdigest()
        assert claimed != meta.commitment

        backend.open_escrow(job, roles["requester"], roles["provider"], PRICE)
        backend.record_commitment(job, roles["provider"], claimed, meta.blob_id, root, meta.index)

        rec = backend.prove_data_mismatch(job, meta.data, roles["watcher"], proof)
        assert rec.state is EscrowState.SLASHED
        assert rec.slash_amount == PRICE
        assert rec.validator_reward == pytest.approx(PRICE * 0.8)
        assert rec.treasury_amount == pytest.approx(PRICE * 0.2)
        assert rec.requester_refund == PRICE
        assert rec.gas_used > 0
        backend.check_invariants()

    def test_an_honest_reveal_cannot_slash(self, chain, tmp_path):
        backend, roles = chain
        job = _job("pytest-honest-reveal")
        meta, proof, root = _da_block(tmp_path, b"an output that matches", "reveal")

        backend.open_escrow(job, roles["requester"], roles["provider"], PRICE)
        backend.record_commitment(job, roles["provider"], meta.commitment,
                                  meta.blob_id, root, meta.index)
        stake_before = backend.slashable_of(roles["provider"])
        with pytest.raises(ContractLogicError, match="NoMismatch"):
            backend.prove_data_mismatch(job, meta.data, roles["watcher"], proof)
        assert backend.slashable_of(roles["provider"]) == stake_before

    def test_rows_carry_the_chain_provenance(self, chain, tmp_path):
        backend, roles = chain
        job = _job("pytest-rows")
        meta, proof, root = _da_block(tmp_path, b"row provenance", "rows")
        claimed = hashlib.sha256(b"something else entirely").hexdigest()
        backend.open_escrow(job, roles["requester"], roles["provider"], PRICE)
        backend.record_commitment(job, roles["provider"], claimed, meta.blob_id, root, meta.index)
        backend.prove_data_mismatch(job, meta.data, roles["watcher"], proof)

        row = backend.rows()[-1]
        assert row["backend"] == "chain"
        assert row["tx_hash"].startswith(("0x",)) or len(row["tx_hash"]) == 64
        assert row["gas_used"] > 0


@requires_chain
def test_sim_and_chain_agree_on_the_slash_split(chain, tmp_path):
    """The Python ledger and the contracts must compute identical settlements
    for identical inputs, or the simulation is not a stand-in for the chain."""
    backend, roles = chain
    job = _job("pytest-parity")
    meta, proof, root = _da_block(tmp_path, b"parity output", "parity")
    claimed = hashlib.sha256(b"a different parity output").hexdigest()

    backend.open_escrow(job, roles["requester"], roles["provider"], PRICE)
    backend.record_commitment(job, roles["provider"], claimed, meta.blob_id, root, meta.index)
    on_chain = backend.prove_data_mismatch(job, meta.data, roles["watcher"], proof)

    clock = ManualClock(0)
    sim = Ledger(min_stake=STAKE, challenge_window_s=backend.challenge_window_s, clock=clock)
    sim.stake("provider", STAKE)
    sim.open_escrow(job, "requester", "provider", PRICE)
    sim.record_commitment(job, "provider", claimed, meta.blob_id, root, meta.index)
    in_sim = sim.prove_data_mismatch(job, meta.data, "watcher", proof)

    for field in ("amount", "slash_amount", "validator_reward", "treasury_amount",
                  "provider_payout", "requester_refund", "fully_covered"):
        assert getattr(on_chain, field) == getattr(in_sim, field), field
    assert on_chain.state is in_sim.state is EscrowState.SLASHED


# --------------------------------------------------------------------------
# configuration and provenance
# --------------------------------------------------------------------------

class TestBackendKeywords:
    """A keyword the chosen backend cannot honour must raise, not be dropped.

    `get_backend("chain", min_stake=...)` used to return a backend running the
    *deployed* minimum while the caller's code read as though it had set one.
    """

    def test_chain_rejects_simulation_only_parameters(self):
        with pytest.raises(ValueError, match="deployed contracts"):
            get_backend("chain", min_stake=1.0)
        with pytest.raises(ValueError, match="deployed contracts"):
            get_backend("chain", challenge_window_s=1.0)

    def test_sim_rejects_chain_only_parameters(self):
        with pytest.raises(ValueError, match="configure the chain"):
            get_backend("sim", rpc_url="http://127.0.0.1:8545")

    def test_a_typo_is_not_swallowed(self):
        with pytest.raises(ValueError, match="unknown keyword"):
            get_backend("chain", rpc_urlll="http://127.0.0.1:8545")
        with pytest.raises(TypeError):
            get_backend("sim", min_stakee=1.0)


@requires_chain
def test_config_and_deployment_must_agree(monkeypatch):
    """A chain deployed with different economics than this process is
    configured for is not a backend, it is a different experiment."""
    from edgegrid.chain import ParamMismatch

    monkeypatch.setattr(C, "MIN_STAKE", C.MIN_STAKE + 1.0)
    with pytest.raises(ParamMismatch, match="min_stake_wei"):
        ChainBackend()


@requires_chain
def test_invariants_hold_for_an_amount_floats_cannot_represent(chain, tmp_path):
    """Regression: `check_invariants` used to rebuild wei out of the float view
    with `to_wei(from_wei(x))`, which is not the identity above ~15 significant
    digits. This escrow slashes 905306727466127488 wei and splits it into two
    parts that each move by tens of wei through a float64, so the old check
    raised on a chain that was behaving perfectly.
    """
    backend, roles = chain
    awkward = 0.9053067274661275
    slash_wei = to_wei(awkward)
    reward_wei = (slash_wei * 8000) // 10000
    assert to_wei(from_wei(reward_wei)) != reward_wei      # the float view is lossy

    job = _job("pytest-awkward")
    meta, proof, root = _da_block(tmp_path, b"awkward amount output", "awkward")
    claimed = hashlib.sha256(b"a different awkward output").hexdigest()
    backend.open_escrow(job, roles["requester"], roles["provider"], awkward)
    backend.record_commitment(job, roles["provider"], claimed, meta.blob_id, root, meta.index)
    backend.prove_data_mismatch(job, meta.data, roles["watcher"], proof)

    w = backend.record_wei[-1]
    assert w["slash_amount_wei"] == slash_wei
    assert w["validator_reward_wei"] + w["treasury_amount_wei"] == slash_wei
    assert w["provider_payout_wei"] + w["requester_refund_wei"] == w["amount_wei"]
    backend.check_invariants()          # exact integers, so this must not raise


@requires_chain
def test_chain_rows_match_the_sim_row_shape(chain, tmp_path):
    """Both backends have to write the same columns, or settlements.csv means
    something different depending on which one produced it."""
    backend, roles = chain
    job = _job("pytest-shape")
    meta, proof, root = _da_block(tmp_path, b"row shape output", "shape")
    claimed = hashlib.sha256(b"row shape claim").hexdigest()
    backend.open_escrow(job, roles["requester"], roles["provider"], PRICE)
    backend.record_commitment(job, roles["provider"], claimed, meta.blob_id, root, meta.index)
    backend.prove_data_mismatch(job, meta.data, roles["watcher"], proof)
    chain_row = backend.rows()[-1]

    clock = ManualClock(0)
    sim = Ledger(min_stake=STAKE, challenge_window_s=backend.challenge_window_s, clock=clock)
    sim.stake("provider", STAKE)
    sim.open_escrow(job, "requester", "provider", PRICE)
    sim.record_commitment(job, "provider", claimed, meta.blob_id, root, meta.index)
    sim.prove_data_mismatch(job, meta.data, "watcher", proof)
    sim_row = sim.rows()[-1]

    assert set(chain_row) == set(sim_row)
    assert chain_row["resolution"] == sim_row["resolution"] == "data_mismatch_proof"
    assert chain_row["reporter"] == roles["watcher"]
    assert chain_row["slash_amount_wei"] == sim_row["slash_amount_wei"]
    assert chain_row["backend"] == "chain" and sim_row["backend"] == "sim"


@requires_chain
def test_a_verdict_row_says_it_came_from_an_oracle(chain, tmp_path):
    """A slash from a judge must never read as a cryptographic proof."""
    backend, roles = chain
    validator = roles["validator"]
    if not backend.is_active(validator):
        backend.stake(validator, STAKE)
    backend.register_validator(validator)

    job = _job("pytest-oracle")
    meta, proof, root = _da_block(tmp_path, b"oracle path output", "oracle")
    backend.open_escrow(job, roles["requester"], roles["provider"], PRICE)
    backend.record_commitment(job, roles["provider"], meta.commitment,
                              meta.blob_id, root, meta.index)
    rec = backend.submit_verdict(job, validator, VerdictKind.FAIL, 1, "fabricated citation")

    assert rec.state is EscrowState.SLASHED
    row = backend.rows()[-1]
    assert row["resolution"] == "validator_verdict"
    assert row["reporter"] == validator
    assert row["verdict"] == "fail" and row["quality_score"] == 1
    # and the honest DA blob is still exactly what the provider committed: the
    # slash rests on an assertion, which is precisely why the row has to say so
    assert hashlib.sha256(meta.data).hexdigest() == meta.commitment
    backend.check_invariants()


@requires_chain
def test_both_backends_refuse_an_unproven_challenge(chain, tmp_path):
    """The sim used to slash on arbitrary bytes with no proof; the chain never
    could. Both now raise the same error before a transaction is even built."""
    from edgegrid.ledger import MissingInclusionProof

    backend, roles = chain
    job = _job("pytest-unproven")
    meta, proof, root = _da_block(tmp_path, b"unproven challenge output", "unproven")
    claimed = hashlib.sha256(b"claimed something else").hexdigest()
    backend.open_escrow(job, roles["requester"], roles["provider"], PRICE)
    backend.record_commitment(job, roles["provider"], claimed, meta.blob_id, root, meta.index)
    stake_before = backend.slashable_of(roles["provider"])
    with pytest.raises(MissingInclusionProof):
        backend.prove_data_mismatch(job, b"arbitrary bytes", roles["watcher"])
    assert backend.slashable_of(roles["provider"]) == stake_before

    # and a well-formed proof for the wrong bytes is rejected by the EVM itself
    with pytest.raises(ContractLogicError, match="BadInclusionProof"):
        backend.prove_data_mismatch(job, b"arbitrary bytes", roles["watcher"], proof)
    assert backend.slashable_of(roles["provider"]) == stake_before

    sim = Ledger(min_stake=STAKE, challenge_window_s=backend.challenge_window_s,
                 clock=ManualClock(0))
    sim.stake("provider", STAKE)
    sim.open_escrow(job, "requester", "provider", PRICE)
    sim.record_commitment(job, "provider", claimed, meta.blob_id, root, meta.index)
    with pytest.raises(MissingInclusionProof):
        sim.prove_data_mismatch(job, b"arbitrary bytes", "attacker")
    assert sim.stakes["provider"] == STAKE


@requires_chain
def test_a_row_is_timestamped_by_the_block_not_the_wall_clock(chain, tmp_path):
    """Scenario A steps the devnet clock past a one-hour challenge window. A row
    stamped with this process's wall clock would put the settlement before its
    own challenge deadline."""
    backend, roles = chain
    job = _job("pytest-clock")
    meta, proof, root = _da_block(tmp_path, b"clock output", "clock")
    backend.open_escrow(job, roles["requester"], roles["provider"], PRICE)
    backend.record_commitment(job, roles["provider"], meta.commitment,
                              meta.blob_id, root, meta.index)
    backend.w3.provider.make_request("evm_increaseTime", [backend.challenge_window_s + 60])
    backend.w3.provider.make_request("evm_mine", [])
    rec = backend.release(job, sender=roles["requester"])

    block = backend.w3.eth.get_block(
        backend.w3.eth.get_transaction_receipt(rec.tx_hash)["blockNumber"])
    assert rec.created_ms == int(block["timestamp"]) * 1000
    assert rec.created_ms >= rec.challenge_deadline_ms
