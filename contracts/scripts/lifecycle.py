"""Run a complete job lifecycle against the deployed contracts and record it.

This is the evidence that the settlement track is real: every number it prints
comes from a mined transaction on a live chain, and the Merkle proof it submits
is produced by `edgegrid.da` and checked by the EVM, not by Python.

Three scenarios:

  A. honest      stake -> escrow -> commitment -> challenge window -> release
  B. data fraud  the provider commits a hash that does not match the DA blob;
                 anyone reveals the blob and the EVM proves the mismatch
  C. judge FAIL  an allow-listed, staked validator submits a FAIL verdict

Usage:
    cd contracts && npx hardhat node &
    cd contracts && npx hardhat run scripts/deploy.js --network localhost
    .venv/bin/python contracts/scripts/lifecycle.py

Time travel: the deployed challenge window is an hour, so scenario A advances
the devnet clock with `evm_increaseTime`. That is recorded in the run manifest
rather than glossed over - it is the one thing here that a public chain would
not let you do.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from edgegrid.chain import ChainBackend, ChainUnavailable, devnet_lock   # noqa: E402
from edgegrid.da import DALayer                                     # noqa: E402
from edgegrid.runlog import RunLog                                  # noqa: E402
from edgegrid.schemas import VerdictKind                            # noqa: E402

HONEST = "The capital of France is Paris."
FRAUD = "The capital of France is Berlin."
STAKE = 10.0
PRICE = 0.05


def advance_chain(backend: ChainBackend, seconds: int) -> None:
    backend.w3.provider.make_request("evm_increaseTime", [seconds])
    backend.w3.provider.make_request("evm_mine", [])


def da_commit(da: DALayer, payload: str, filler: list[str]) -> tuple[str, str, str, int, bytes, list]:
    """Put `payload` in a DA block alongside `filler` blobs so the inclusion
    proof has real siblings, and return everything the chain needs."""
    for f in filler[:1]:
        da.submit_blob(f, seal=False)
    blob = da.submit_blob(payload, seal=False)
    for f in filler[1:]:
        da.submit_blob(f, seal=False)
    da.seal_block()
    meta = da.get_blob_meta(blob.blob_id)
    proof, root = da.inclusion_proof(blob.blob_id)
    return blob.blob_id, meta.commitment, root, meta.index, meta.data, proof


def main() -> int:
    try:
        backend = ChainBackend()
    except ChainUnavailable as e:
        print(f"chain backend unavailable:\n{e}", file=sys.stderr)
        return 2

    w3 = backend.w3
    accounts = [w3.to_checksum_address(a) for a in w3.eth.accounts]
    requester, provider, validator, watcher = accounts[2], accounts[3], accounts[4], accounts[5]

    # This script steps the devnet clock past the challenge window, which moves
    # it for every other user of the node, so it runs one at a time.
    with devnet_lock(backend.rpc_url), RunLog("settlement-onchain", params={
        "rpc_url": backend.rpc_url, "chain_id": w3.eth.chain_id,
        "contracts": backend.deployment["contracts"], "price_grid": PRICE, "stake_grid": STAKE,
    }) as log:
        da = DALayer(root_dir=Path(log.dir) / "da")
        log.note(f"chain {w3.eth.chain_id} at {backend.rpc_url}, block {w3.eth.block_number}")

        print(f"chain id {w3.eth.chain_id} @ {backend.rpc_url}, block {w3.eth.block_number}")
        print(f"registry {backend.registry.address}  marketplace {backend.marketplace.address}")
        print(f"verification {backend.verification.address}  treasury {backend.treasury}\n")

        # -- staking -------------------------------------------------------
        for who, role in ((provider, "provider"), (validator, "validator")):
            # `gas 0` would be a default masquerading as a measurement, so the
            # gas is printed only when a stake transaction was actually sent.
            if backend.is_active(who):
                gas = "already active, no tx"
            else:
                backend.stake(who, STAKE)
                gas = f"gas {backend.gas_used['stake']}"
            print(f"stake     {role:9s} {who} = {backend.slashable_of(who)} GRID ({gas})")
        backend.register_validator(validator)
        print(f"validator allow-listed (gas {backend.gas_used['setValidator']})\n")

        # Scenario label only; every other column comes from backend.rows(), so
        # the CSV carries the backend, the exact wei, and - the thing a reader
        # cannot otherwise recover - whether each slash rested on a Merkle proof
        # or on a validator's assertion.
        scenarios: list[str] = []

        # -- A: honest job -------------------------------------------------
        job_a = f"job-honest-{w3.eth.block_number}"
        blob_id, output_hash, root, index, data, proof = da_commit(
            da, HONEST, ["unrelated blob one", "unrelated blob two"])
        open_a = backend.open_escrow(job_a, requester, provider, PRICE)
        commit_a = backend.record_commitment(job_a, provider, output_hash, blob_id, root, index)
        print(f"[A] escrow    {open_a['tx_hash']} gas {open_a['gas_used']}")
        print(f"[A] commit    {commit_a['tx_hash']} gas {commit_a['gas_used']} "
              f"root {root[:16]}... leaf {index}")
        advance_chain(backend, backend.challenge_window_s + 60)
        log.note(f"evm_increaseTime {backend.challenge_window_s + 60}s to close the challenge window")
        rec_a = backend.release(job_a, sender=requester)
        print(f"[A] release   {rec_a.tx_hash} gas {rec_a.gas_used} "
              f"state={rec_a.state.value} provider_payout={rec_a.provider_payout}")
        paid = backend.withdraw(provider)
        print(f"[A] withdraw  provider pulled {paid} GRID\n")
        scenarios.append("honest")

        # -- B: proven data fraud ------------------------------------------
        job_b = f"job-fraud-{w3.eth.block_number}"
        blob_id_b, actual_hash, root_b, index_b, data_b, proof_b = da_commit(
            da, FRAUD, ["unrelated blob three", "unrelated blob four"])
        claimed_hash = da_commit(da, HONEST, ["decoy"])[1]      # what the provider pretends it produced
        open_b = backend.open_escrow(job_b, requester, provider, PRICE)
        backend.record_commitment(job_b, provider, claimed_hash, blob_id_b, root_b, index_b)
        print(f"[B] escrow    {open_b['tx_hash']} gas {open_b['gas_used']}")
        print(f"[B] commit    claimed sha256 {claimed_hash[:16]}... "
              f"but the blob at leaf {index_b} hashes to {actual_hash[:16]}...")

        before_watcher = int(backend.registry.functions.withdrawable(watcher).call())
        before_treasury = int(backend.registry.functions.withdrawable(backend.treasury).call())
        rec_b = backend.prove_data_mismatch(job_b, data_b, watcher, proof_b)
        after_watcher = int(backend.registry.functions.withdrawable(watcher).call())
        after_treasury = int(backend.registry.functions.withdrawable(backend.treasury).call())
        print(f"[B] fraud     {rec_b.tx_hash} gas {rec_b.gas_used} state={rec_b.state.value}")
        if rec_b.slash_amount == 0:
            raise SystemExit("[B] the fraud proof confirmed but slashed nothing - "
                             "the provider held no collateral, which is not the scenario")
        print(f"[B] slash     {rec_b.slash_amount} GRID -> validator {rec_b.validator_reward} "
              f"({100 * rec_b.validator_reward / rec_b.slash_amount:.0f}%) + treasury "
              f"{rec_b.treasury_amount} ({100 * rec_b.treasury_amount / rec_b.slash_amount:.0f}%)")
        print(f"[B] credited  watcher +{(after_watcher - before_watcher) / 1e18} "
              f"treasury +{(after_treasury - before_treasury) / 1e18} GRID")
        print(f"[B] refund    requester +{rec_b.requester_refund} GRID, "
              f"provider stake now {rec_b.remaining_stake} GRID\n")
        scenarios.append("data_fraud")

        # -- C: validator FAIL verdict -------------------------------------
        # The scenario-B slash pushed the provider under the minimum stake, so the
        # marketplace now refuses to escrow against it. Topping up is the only way
        # back - that is the collateral floor doing its job, not a setup step.
        if not backend.is_active(provider):
            short = round(STAKE - backend.slashable_of(provider), 9)
            backend.stake(provider, short)
            print(f"[C] top-up    provider was under the {STAKE} GRID floor after the slash, "
                  f"restaked {short} GRID (gas {backend.gas_used['stake']})")

        job_c = f"job-verdict-{w3.eth.block_number}"
        blob_id_c, hash_c, root_c, index_c, _, _ = da_commit(da, HONEST, ["decoy five"])
        open_c = backend.open_escrow(job_c, requester, provider, PRICE)
        backend.record_commitment(job_c, provider, hash_c, blob_id_c, root_c, index_c)
        rec_c = backend.submit_verdict(job_c, validator, VerdictKind.FAIL, 1, "fabricated citation")
        print(f"[C] escrow    {open_c['tx_hash']} gas {open_c['gas_used']}")
        print(f"[C] verdict   {rec_c.tx_hash} gas {rec_c.gas_used} state={rec_c.state.value}")
        print(f"[C] slash     {rec_c.slash_amount} GRID -> validator {rec_c.validator_reward} "
              f"+ treasury {rec_c.treasury_amount}\n")
        scenarios.append("judge_fail")

        # -- accounting ----------------------------------------------------
        inv = backend.check_invariants()
        print("value conservation (checked against on-chain balances):")
        for k, v in inv.items():
            print(f"  {k:24s} {v}")

        rows = [dict(r, scenario=sc) for r, sc in zip(backend.rows(), scenarios)]
        assert len(rows) == len(scenarios), "a settlement went unrecorded"
        print("\nresolution recorded per job:")
        for r in rows:
            print(f"  {r['scenario']:11s} {r['state']:8s} resolution={r['resolution']} "
                  f"reporter={r['reporter'] or '-'}")
        log.write_table("settlements", rows)
        log.write_json("invariants", inv)
        log.write_json("gas_used", backend.gas_used)
        log.write_json("deployment", {k: backend.deployment[k] for k in
                                      ("chainId", "blockNumber", "contracts", "gasUsed",
                                       "txHashes", "params", "totalDeploymentGas")})
        log.write_json("da_stats", da.stats())
        print(f"\ngas per operation: {backend.gas_used}")
        print(f"results -> {log.dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
