"""The bridge from the Python settlement API to the deployed contracts.

`edgegrid.ledger.Ledger` and `ChainBackend` expose the same surface, so an
experiment can run either against a local simulation or against a real chain and
produce the same `SettlementRecord`s. What this module refuses to do is guess:

  * if `contracts/deployment.json` is missing, it raises and tells you to run
    the deploy script;
  * if the RPC is unreachable, it raises and tells you to start the node;
  * if the deployment's chain id does not match the node's, or there is no code
    at a recorded address (the usual symptom of restarting `hardhat node` and
    keeping a stale deployment.json), it raises;
  * it never falls back to simulation. `get_backend(prefer=...)` is the single
    place the choice is made, and the caller has to make it.

Every record carries the backend that produced it - `Ledger.rows()` and
`ChainBackend.rows()` both tag each row, and on-chain rows additionally carry the
transaction hash and gas used, which is the evidence the simulation cannot fake.

Units: 1 GRID == 1 ether == 10**18 wei, so the float amounts in the schemas and
the integer amounts on chain are the same quantity in different clothes.
"""

from __future__ import annotations

import fcntl
import json
import math
import re
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Optional

from web3 import Web3
from web3.logs import DISCARD

from edgegrid import config as C
from edgegrid.ledger import (
    Ledger,
    LedgerError,
    MissingInclusionProof,
    NothingToWithdraw,
    from_wei,
    to_wei,
)
from edgegrid.schemas import EscrowState, SettlementRecord, VerdictKind

__all__ = [
    "ChainBackend", "ChainUnavailable", "DeploymentNotFound", "RpcUnreachable",
    "ChainIdMismatch", "ContractMissing", "NoSigner", "ParamMismatch", "get_backend",
    "chain_available", "job_key", "devnet_lock",
]

# Solidity enum order in Interfaces.sol, index 0 == "no escrow was ever opened".
_ESCROW_STATES: tuple[Optional[EscrowState], ...] = (
    None,
    EscrowState.OPEN,
    EscrowState.AWAITING_VERIFICATION,
    EscrowState.SETTLED,
    EscrowState.SLASHED,
    EscrowState.REFUNDED,
)

# VerificationContract.VerdictKind
_VERDICT_TO_ENUM = {VerdictKind.PASS: 1, VerdictKind.FAIL: 2, VerdictKind.ERROR: 3}
_RESOLUTION = {0: "", 1: "data_mismatch_proof", 2: "validator_verdict"}
_ENUM_TO_VERDICT = {1: VerdictKind.PASS, 2: VerdictKind.FAIL, 3: VerdictKind.ERROR}


class ChainUnavailable(RuntimeError):
    """The chain backend cannot be used. Always says what to do about it."""


class DeploymentNotFound(ChainUnavailable): ...
class RpcUnreachable(ChainUnavailable): ...
class ChainIdMismatch(ChainUnavailable): ...
class ContractMissing(ChainUnavailable): ...
class NoSigner(ChainUnavailable): ...


class ParamMismatch(ChainUnavailable):
    """The deployed economic parameters differ from `edgegrid.config`.

    The two backends are only comparable if they run the same numbers. A
    deployment made with a 3600s challenge window read by a process whose
    config says 60s would produce a settlement table nobody could interpret,
    and the divergence would be invisible in the output, so it raises here
    instead of at the point where somebody trusts the comparison.
    """


@contextmanager
def devnet_lock(rpc_url: str = C.RPC_URL, timeout_s: float = 600.0):
    """Hold exclusive use of a devnet for the duration of the block.

    `evm_increaseTime` moves the chain's clock for *everybody* on it, so two
    processes running challenge-window scenarios against one node corrupt each
    other's deadlines - and, sharing unlocked accounts, each other's nonces.
    Nothing about that is detectable from inside a single process, so anything
    that time-travels a devnet takes this lock: `contracts/scripts/lifecycle.py`
    and the on-chain tests both do.
    """
    key = re.sub(r"[^A-Za-z0-9]+", "-", rpc_url).strip("-")
    path = Path(tempfile.gettempdir()) / f"edgegrid-devnet-{key}.lock"
    deadline = time.monotonic() + timeout_s
    with open(path, "w") as fh:
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise TimeoutError(
                        f"another process has held {path} for {timeout_s}s; a devnet run "
                        f"is stuck or was killed without releasing it")
                time.sleep(0.2)
        try:
            yield path
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def job_key(job_id: str) -> bytes:
    """job_id string -> the bytes32 the contracts index by."""
    return Web3.keccak(text=job_id)


# --------------------------------------------------------------------------
# availability
# --------------------------------------------------------------------------

def chain_available(rpc_url: str = C.RPC_URL,
                    deployment_file: Path = C.DEPLOYMENT_FILE) -> tuple[bool, str]:
    """Non-raising probe. Returns (ok, reason) so a caller can report why the
    chain backend is unusable instead of silently doing something else."""
    try:
        ChainBackend(rpc_url=rpc_url, deployment_file=deployment_file)
        return True, "ok"
    except ChainUnavailable as e:
        return False, str(e)


_CHAIN_KWARGS = frozenset(("rpc_url", "deployment_file", "private_keys", "default_sender"))
_CHAIN_ONLY_KWARGS = _CHAIN_KWARGS
_SIM_ONLY_KWARGS = frozenset(("min_stake", "challenge_window_s", "award_timeout_s",
                              "unbonding_period_s", "validator_share", "treasury", "clock"))


def get_backend(prefer: str = "chain", **kwargs):
    """Return the settlement backend the caller asked for.

    `prefer="chain"` raises if the chain is not usable; `prefer="sim"` returns
    the local ledger. There is deliberately no "auto": a run that quietly
    downgraded from a real chain to a simulation would report numbers nobody
    could interpret.

    A keyword the chosen backend cannot honour raises rather than being
    dropped. `min_stake` on the chain backend, for instance, lives in the
    deployed constructor argument and cannot be changed from Python; accepting
    and ignoring it would leave a run configured differently from how it reads.
    """
    if prefer == "sim":
        rejected = sorted(set(kwargs) & _CHAIN_ONLY_KWARGS)
        if rejected:
            raise ValueError(
                f"get_backend('sim') cannot honour {rejected}: those configure the chain "
                f"backend. Silently dropping them would leave a run configured differently "
                f"from how it reads.")
        return Ledger(**kwargs)
    if prefer == "chain":
        rejected = sorted(set(kwargs) & _SIM_ONLY_KWARGS)
        if rejected:
            raise ValueError(
                f"get_backend('chain') cannot honour {rejected}: those parameters live in "
                f"the deployed contracts, not in this process. Redeploy with different "
                f"constructor arguments instead.")
        unknown = sorted(set(kwargs) - _CHAIN_KWARGS)
        if unknown:
            raise ValueError(f"get_backend('chain') got unknown keyword(s) {unknown}")
        return ChainBackend(**kwargs)
    raise ValueError(f'prefer must be "chain" or "sim", got {prefer!r}')


# --------------------------------------------------------------------------
# chain backend
# --------------------------------------------------------------------------

class ChainBackend:
    """`Ledger`'s interface, executed as real transactions."""

    backend = "chain"

    def __init__(self, rpc_url: str = C.RPC_URL,
                 deployment_file: Path = C.DEPLOYMENT_FILE,
                 private_keys: Optional[dict[str, str]] = None,
                 default_sender: Optional[str] = None):
        self.deployment_file = Path(deployment_file)
        if not self.deployment_file.exists():
            raise DeploymentNotFound(
                f"no deployment at {self.deployment_file}. Start a node and deploy:\n"
                f"  cd contracts && npx hardhat node &\n"
                f"  cd contracts && npx hardhat run scripts/deploy.js --network localhost")
        self.deployment = json.loads(self.deployment_file.read_text())

        self.rpc_url = rpc_url
        self.w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 30}))
        if not self.w3.is_connected():
            raise RpcUnreachable(
                f"no JSON-RPC at {rpc_url}. Start the node:  cd contracts && npx hardhat node")

        chain_id = self.w3.eth.chain_id
        if chain_id != self.deployment["chainId"]:
            raise ChainIdMismatch(
                f"{self.deployment_file.name} was written for chainId "
                f"{self.deployment['chainId']} but {rpc_url} reports {chain_id}")

        addrs = self.deployment["contracts"]
        abis = self.deployment["abis"]
        for name, addr in addrs.items():
            if self.w3.eth.get_code(Web3.to_checksum_address(addr)) in (b"", b"0x"):
                raise ContractMissing(
                    f"no contract code at {name} {addr} on {rpc_url}. The node was probably "
                    f"restarted since deployment - redeploy:\n"
                    f"  cd contracts && npx hardhat run scripts/deploy.js --network localhost")

        self.registry = self._contract("NodeRegistry", addrs, abis)
        self.marketplace = self._contract("Marketplace", addrs, abis)
        self.verification = self._contract("VerificationContract", addrs, abis)
        self.models = self._contract("ModelRegistry", addrs, abis)

        self.treasury = Web3.to_checksum_address(self.deployment["treasury"])
        self.owner = Web3.to_checksum_address(self.deployment["deployer"])
        self.min_stake_wei = int(self.deployment["params"]["minStakeWei"])
        self.challenge_window_s = int(self.deployment["params"]["challengeWindowS"])
        self.validator_bps = int(self.deployment["params"]["validatorSlashBps"])
        self.award_timeout_s = int(self.deployment["params"]["awardTimeoutS"])
        self._check_params_match_config()

        self._keys = {Web3.to_checksum_address(k): v for k, v in (private_keys or {}).items()}
        self.default_sender = Web3.to_checksum_address(default_sender) if default_sender else None

        self.records: list[SettlementRecord] = []
        # The exact integers behind each record, read straight out of the event
        # logs. `SettlementRecord` carries floats and a float64 cannot hold 18
        # significant digits, so re-deriving wei from the record with to_wei()
        # is lossy for any amount that is not a round binary fraction - it made
        # check_invariants() capable of raising on a chain that was correct.
        # These are the authority here exactly as `Ledger.record_wei` is there.
        self.record_wei: list[dict[str, int]] = []
        self.record_meta: list[dict] = []
        self.verdict_detail: dict[str, dict] = {}
        self.gas_used: dict[str, int] = {}          # label -> gas, for the results table
        self._job_ids: dict[bytes, str] = {}        # bytes32 -> original job_id string

        # A deployment outlives any one process, so conservation is checked as a
        # delta against the state this instance started from rather than against
        # absolute totals that include somebody else's earlier run. Everything
        # is read at one fixed block so an account discovered later - including
        # one another process created - can still be baselined correctly.
        self._baseline_block = int(self.w3.eth.block_number)
        at = {"block_identifier": self._baseline_block}
        self._baseline = {
            "block": self._baseline_block,
            "registry_balance": int(self.w3.eth.get_balance(self.registry.address,
                                                            self._baseline_block)),
            "marketplace_balance": int(self.w3.eth.get_balance(self.marketplace.address,
                                                               self._baseline_block)),
            "total_staked": int(self.registry.functions.totalStaked().call(**at)),
            "total_unbonding": int(self.registry.functions.totalUnbonding().call(**at)),
            "total_slashed": int(self.registry.functions.totalSlashed().call(**at)),
            "total_escrowed": int(self.marketplace.functions.totalEscrowed().call(**at)),
            "total_paid": int(self.marketplace.functions.totalPaidToProviders().call(**at)),
            "total_refunded": int(
                self.marketplace.functions.totalRefundedToRequesters().call(**at)),
        }
        self._credited_baseline: dict[str, tuple[int, int]] = {}
        for a in (self.treasury, self.owner):
            self._touch(a)

    def _check_params_match_config(self) -> None:
        """Refuse to run when the chain was deployed with different economics
        than this process is configured for. Read from the chain itself, not
        from deployment.json, so an edited or stale file cannot hide it."""
        on_chain = {
            "min_stake_wei": int(self.registry.functions.minStake().call()),
            "challenge_window_s": int(self.verification.functions.challengeWindow().call()),
            "validator_slash_bps": int(self.registry.functions.VALIDATOR_SLASH_BPS().call()),
            "award_timeout_s": int(self.marketplace.functions.awardTimeout().call()),
        }
        configured = {
            "min_stake_wei": to_wei(C.MIN_STAKE),
            "challenge_window_s": int(C.CHALLENGE_WINDOW_S),
            "validator_slash_bps": int(round(C.VALIDATOR_SLASH_SHARE * 10_000)),
            "award_timeout_s": on_chain["award_timeout_s"],   # no config knob for this one
        }
        self.params = on_chain
        differences = [f"{k}: chain={on_chain[k]} config={configured[k]}"
                       for k in on_chain if on_chain[k] != configured[k]]
        if differences:
            raise ParamMismatch(
                "the deployed contracts do not match edgegrid.config: "
                + "; ".join(differences)
                + ".\n  Redeploy with matching values, e.g.\n"
                f"  cd contracts && MIN_STAKE={C.MIN_STAKE} "
                f"CHALLENGE_WINDOW_S={int(C.CHALLENGE_WINDOW_S)} "
                "npx hardhat run scripts/deploy.js --network localhost")
        # deployment.json is only a cache of what the chain says; if it drifted
        # from the live contracts the gas table beside it is describing some
        # other deployment, so that is an error too.
        if int(self.deployment["params"]["minStakeWei"]) != on_chain["min_stake_wei"] or \
                int(self.deployment["params"]["challengeWindowS"]) != on_chain["challenge_window_s"]:
            raise ParamMismatch(
                f"{self.deployment_file.name} records "
                f"minStakeWei={self.deployment['params']['minStakeWei']} "
                f"challengeWindowS={self.deployment['params']['challengeWindowS']} but the live "
                f"contracts report {on_chain['min_stake_wei']} and "
                f"{on_chain['challenge_window_s']} - redeploy to regenerate the file")

    def _contract(self, name: str, addrs: dict, abis: dict):
        return self.w3.eth.contract(address=Web3.to_checksum_address(addrs[name]), abi=abis[name])

    def _touch(self, account: str) -> str:
        """Start tracking an account, recording what it was owed at the baseline
        block. Reading at that fixed block rather than "now" is what lets
        `check_invariants` add accounts it discovers in the logs afterwards."""
        a = Web3.to_checksum_address(account)
        if a not in self._credited_baseline:
            at = {"block_identifier": self._baseline_block}
            self._credited_baseline[a] = (
                int(self.registry.functions.withdrawable(a).call(**at)),
                int(self.marketplace.functions.withdrawable(a).call(**at)),
            )
        return a

    # Event fields that name an account holding or moving value. Scanning these
    # since the baseline block is what turns the conservation check from
    # "accounts this process happened to touch" into "every holder that moved".
    _HOLDER_FIELDS = {
        "registry": {"Staked": ("node",), "UnstakeRequested": ("node",),
                     "UnstakeClaimed": ("node",), "Slashed": ("node", "reporter"),
                     "Withdrawn": ("account",)},
        "marketplace": {"EscrowOpened": ("requester", "provider"),
                        "EscrowSettled": ("provider",), "EscrowSlashed": ("requester",),
                        "EscrowRefunded": ("requester",), "Withdrawn": ("account",)},
    }

    def _discover_holders(self, to_block: int) -> int:
        """Touch every account that either contract has moved value for since
        the baseline block, so the credited side of the check is complete.

        Without this the sum ran over whatever accounts this process happened to
        transact with, and any other writer - a second test session against the
        same devnet, a script left running - showed up as a conservation
        failure rather than as the untracked holder it was.
        """
        found = 0
        for name, contract in (("registry", self.registry), ("marketplace", self.marketplace)):
            for event, fields in self._HOLDER_FIELDS[name].items():
                logs = getattr(contract.events, event)().get_logs(
                    from_block=self._baseline_block + 1, to_block=to_block)
                for log in logs:
                    for f in fields:
                        before = len(self._credited_baseline)
                        self._touch(log["args"][f])
                        found += len(self._credited_baseline) - before
        return found

    # -- transactions ----------------------------------------------------

    def _send(self, fn, sender: str, value: int = 0, label: str = "") -> dict:
        """Send one transaction and wait for it. Signs locally when a key is
        registered for `sender`, otherwise uses a node-managed account (which is
        what a local Hardhat node gives us)."""
        sender = self._touch(sender)
        tx = {"from": sender, "value": value}
        if sender in self._keys:
            # Only a locally signed transaction needs a nonce from us. Setting
            # one for a node-managed account races any other process sending
            # from the same address - two test sessions against one devnet both
            # read pending nonce 12 and the second is rejected as too low.
            tx["nonce"] = self.w3.eth.get_transaction_count(sender, "pending")
        built = fn.build_transaction(tx)
        if sender in self._keys:
            signed = self.w3.eth.account.sign_transaction(built, self._keys[sender])
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        else:
            if sender not in {Web3.to_checksum_address(a) for a in self.w3.eth.accounts}:
                raise NoSigner(
                    f"{sender} is neither unlocked on {self.rpc_url} nor in private_keys")
            tx_hash = self.w3.eth.send_transaction(built)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt["status"] != 1:
            raise LedgerError(f"transaction reverted: {receipt['transactionHash'].hex()}")
        if label:
            self.gas_used[label] = int(receipt["gasUsed"])
        return receipt

    def _sender(self, who: Optional[str]) -> str:
        if who:
            return Web3.to_checksum_address(who)
        if self.default_sender:
            return self.default_sender
        raise NoSigner("no sender given and no default_sender configured")

    # -- staking ---------------------------------------------------------

    def stake(self, node: str, amount: float) -> float:
        self._send(self.registry.functions.stake(), node, value=to_wei(amount), label="stake")
        return from_wei(self.registry.functions.stakeOf(Web3.to_checksum_address(node)).call())

    def request_unstake(self, node: str, amount: float) -> int:
        self._send(self.registry.functions.requestUnstake(to_wei(amount)), node,
                   label="requestUnstake")
        return int(self.registry.functions.unbonding(Web3.to_checksum_address(node)).call()[1]) * 1000

    def claim_unstake(self, node: str) -> float:
        before = self.registry.functions.withdrawable(Web3.to_checksum_address(node)).call()
        self._send(self.registry.functions.claimUnstake(), node, label="claimUnstake")
        after = self.registry.functions.withdrawable(Web3.to_checksum_address(node)).call()
        return from_wei(after - before)

    def withdraw(self, account: str) -> float:
        """Pull everything credited to `account`. The contracts keep two
        balances - stake-side (registry) and escrow-side (marketplace) - and
        this pulls whichever are non-zero, so the surface matches the ledger's
        single `withdraw`."""
        account = self._touch(account)
        total = 0
        for name, contract in (("registry", self.registry), ("marketplace", self.marketplace)):
            amount = contract.functions.withdrawable(account).call()
            if amount:
                self._send(contract.functions.withdraw(), account, label=f"withdraw:{name}")
                total += amount
        if total == 0:
            raise NothingToWithdraw(f"{account} has nothing to withdraw on chain")
        return from_wei(total)

    def is_active(self, node: str) -> bool:
        return bool(self.registry.functions.isActive(Web3.to_checksum_address(node)).call())

    def slashable_of(self, node: str) -> float:
        return from_wei(self.registry.functions.slashableOf(Web3.to_checksum_address(node)).call())

    @property
    def stakes(self) -> dict[str, float]:
        """Active stake for every account this backend has touched. The chain
        has no enumerable holder set, so this is scoped to what we know about -
        stated plainly rather than presented as a global view."""
        return {a: from_wei(self.registry.functions.stakeOf(a).call())
                for a in sorted(self._credited_baseline)}

    # -- escrow ----------------------------------------------------------

    def open_escrow(self, job_id: str, requester: str, provider: str, amount: float) -> dict:
        key = job_key(job_id)
        self._job_ids[key] = job_id
        receipt = self._send(
            self.marketplace.functions.openEscrow(key, self._touch(provider)),
            requester, value=to_wei(amount), label="openEscrow")
        return {"job_id": job_id, "tx_hash": receipt["transactionHash"].hex(),
                "gas_used": int(receipt["gasUsed"]), "amount": amount}

    def record_commitment(self, job_id: str, provider: str, output_hash: str, blob_ref: str,
                          merkle_root: str = "", leaf_index: int = 0) -> dict:
        """`output_hash` and `merkle_root` are hex sha256 digests from
        `edgegrid.da` - the same bytes the on-chain fraud proof recomputes."""
        key = job_key(job_id)
        self._job_ids[key] = job_id
        receipt = self._send(
            self.verification.functions.recordCommitment(
                key, bytes.fromhex(output_hash.removeprefix("0x")),
                bytes.fromhex(merkle_root.removeprefix("0x")) if merkle_root else b"\x00" * 32,
                int(leaf_index), blob_ref),
            provider, label="recordCommitment")
        deadline_s = int(self.marketplace.functions.challengeDeadlineOf(key).call())
        return {"job_id": job_id, "tx_hash": receipt["transactionHash"].hex(),
                "gas_used": int(receipt["gasUsed"]), "challenge_deadline_ms": deadline_s * 1000}

    def release(self, job_id: str, sender: Optional[str] = None) -> SettlementRecord:
        key = job_key(job_id)
        receipt = self._send(self.marketplace.functions.release(key),
                             self._sender(sender), label="release")
        return self._record_from_receipt(job_id, receipt, "challenge_window_elapsed")

    def cancel(self, job_id: str, requester: str) -> SettlementRecord:
        key = job_key(job_id)
        receipt = self._send(self.marketplace.functions.cancel(key), requester, label="cancel")
        return self._record_from_receipt(job_id, receipt, "award_timeout_cancel")

    # -- verification ----------------------------------------------------

    def register_validator(self, validator: str, sender: Optional[str] = None) -> dict:
        receipt = self._send(
            self.verification.functions.setValidator(Web3.to_checksum_address(validator), True),
            sender or self.owner, label="setValidator")
        self._touch(validator)
        return {"tx_hash": receipt["transactionHash"].hex(), "gas_used": int(receipt["gasUsed"])}

    def submit_verdict(self, job_id: str, validator: str,
                       verdict: VerdictKind, quality_score: int = 0,
                       reason: str = "") -> Optional[SettlementRecord]:
        kind = _VERDICT_TO_ENUM.get(verdict)
        if kind is None:
            raise LedgerError(f"cannot submit {verdict!r} on chain")
        reason_hash = Web3.keccak(text=reason) if reason else b"\x00" * 32
        receipt = self._send(
            self.verification.functions.submitVerdict(
                job_key(job_id), kind, int(quality_score), reason_hash),
            validator, label="submitVerdict")
        # Read the verdict back out of the log rather than trusting the argument
        # we sent: the row has to say what the chain recorded.
        logged = self.verification.events.VerdictRecorded().process_receipt(receipt, errors=DISCARD)
        logged = [e for e in logged if e["args"]["jobId"] == job_key(job_id)]
        if not logged:
            raise LedgerError(
                f"job {job_id}: submitVerdict mined in {receipt['transactionHash'].hex()} but "
                f"emitted no VerdictRecorded event - refusing to record an unwitnessed verdict")
        args = logged[0]["args"]
        self.verdict_detail[job_id] = {
            "validator": args["validator"],
            "verdict": _ENUM_TO_VERDICT[int(args["verdict"])].value,
            "quality_score": int(args["qualityScore"]),
            "reason": reason,
        }
        if verdict is VerdictKind.FAIL:
            return self._record_from_receipt(job_id, receipt, "validator_verdict")
        return None

    def prove_data_mismatch(self, job_id: str, blob_data: bytes, reporter: str,
                            proof: Optional[Iterable[tuple[str, str]]] = None) -> SettlementRecord:
        """Submit the DA blob and its Merkle path; the EVM checks both. `proof`
        is `edgegrid.da.merkle_proof` output - the side labels are dropped
        because the contract derives direction from the committed leaf index."""
        if proof is None:
            # The EVM would revert with BadInclusionProof anyway (no sibling
            # path folds to a stored root), but raising the same error the
            # simulation raises keeps the two backends' failure modes matched.
            raise MissingInclusionProof(
                f"job {job_id}: a Merkle inclusion proof is required to slash; pass "
                f"edgegrid.da.DALayer.inclusion_proof()'s path")
        siblings = [bytes.fromhex(h) for _side, h in proof]
        receipt = self._send(
            self.verification.functions.proveDataMismatch(job_key(job_id), blob_data, siblings),
            reporter, label="proveDataMismatch")
        return self._record_from_receipt(job_id, receipt, "data_mismatch_proof")

    # -- records ---------------------------------------------------------

    def _events(self, contract, name: str, receipt, key: bytes) -> list:
        """Decoded events of one type from this receipt, restricted to `key`.

        The jobId filter is not decoration: a receipt can carry logs for more
        than one escrow (any batching contract, or an attacker-controlled
        callee), and summing every EscrowSettled in the receipt would credit
        this job with somebody else's payout.
        """
        evs = getattr(contract.events, name)().process_receipt(receipt, errors=DISCARD)
        return [e for e in evs if e["args"].get("jobId") == key]

    def _record_from_receipt(self, job_id: str, receipt, resolution: str) -> SettlementRecord:
        """Build a SettlementRecord out of the events the transaction actually
        emitted. Nothing here is reconstructed from what we hoped happened, and
        `resolution` is cross-checked against the FraudConfirmed log rather than
        being taken on trust from the call site."""
        key = job_key(job_id)
        escrow = self.marketplace.functions.escrows(key).call()
        requester, provider, amount_wei = escrow[0], escrow[1], int(escrow[2])
        state = _ESCROW_STATES[int(escrow[5])]
        if state is None:
            raise LedgerError(f"job {job_id}: no escrow exists on chain for {key.hex()}")

        settled = self._events(self.marketplace, "EscrowSettled", receipt, key)
        slashed_ev = self._events(self.marketplace, "EscrowSlashed", receipt, key)
        refunded = self._events(self.marketplace, "EscrowRefunded", receipt, key)
        fraud = self._events(self.verification, "FraudConfirmed", receipt, key)

        provider_payout = sum(int(e["args"]["amount"]) for e in settled)
        requester_refund = (sum(int(e["args"]["amount"]) for e in slashed_ev)
                            + sum(int(e["args"]["amount"]) for e in refunded))
        slash = fraud[0]["args"] if fraud else None
        if slash is not None:
            on_chain_resolution = _RESOLUTION[int(slash["kind"])]
            if on_chain_resolution != resolution:
                raise LedgerError(
                    f"job {job_id}: caller labelled this settlement {resolution!r} but the "
                    f"FraudConfirmed log says {on_chain_resolution!r}")
            reporter = slash["reporter"]
        else:
            reporter = ""

        slashed_wei = int(slash["slashed"]) if slash else 0
        reward_wei = int(slash["validatorReward"]) if slash else 0
        treasury_wei = int(slash["treasuryAmount"]) if slash else 0

        rec = SettlementRecord(
            job_id=job_id,
            provider_peer_id=provider,
            requester_peer_id=requester,
            amount=from_wei(amount_wei),
            state=state,
            slashed=slashed_wei > 0,
            slash_amount=from_wei(slashed_wei),
            validator_reward=from_wei(reward_wei),
            treasury_amount=from_wei(treasury_wei),
            provider_payout=from_wei(provider_payout),
            requester_refund=from_wei(requester_refund),
            fully_covered=bool(slash["fullyCovered"]) if slash else True,
            remaining_stake=from_wei(self.registry.functions.stakeOf(provider).call()),
            challenge_deadline_ms=int(self.marketplace.functions.challengeDeadlineOf(key).call()) * 1000,
            tx_hash=receipt["transactionHash"].hex(),
            gas_used=int(receipt["gasUsed"]),
            # The block's own timestamp, not this process's wall clock. On a
            # devnet stepped forward with evm_increaseTime the two differ by
            # hours, and the row has to say when the chain thinks it settled.
            created_ms=int(self.w3.eth.get_block(receipt["blockNumber"])["timestamp"]) * 1000,
        )
        self.records.append(rec)
        self.record_wei.append({
            "job_id": job_id, "amount_wei": amount_wei,
            "provider_payout_wei": provider_payout, "requester_refund_wei": requester_refund,
            "slash_amount_wei": slashed_wei, "validator_reward_wei": reward_wei,
            "treasury_amount_wei": treasury_wei,
        })
        detail = self.verdict_detail.get(job_id, {})
        self.record_meta.append({
            "resolution": resolution,
            "reporter": reporter,
            "verdict": detail.get("verdict", ""),
            "quality_score": detail.get("quality_score", 0),
        })
        return rec

    def rows(self) -> list[dict]:
        """Same columns as `Ledger.rows()`, so a settlements.csv is readable the
        same way whichever backend produced it."""
        return [dict(r.model_dump(mode="json"), backend=self.backend, **m,
                     **{k: v for k, v in w.items() if k != "job_id"})
                for r, w, m in zip(self.records, self.record_wei, self.record_meta)]

    def total_paid_to_providers(self) -> float:
        return from_wei(self.marketplace.functions.totalPaidToProviders().call())

    def escrow_state(self, job_id: str) -> Optional[EscrowState]:
        return _ESCROW_STATES[int(self.marketplace.functions.escrowState(job_key(job_id)).call())]

    def commitment_of(self, job_id: str) -> dict:
        c = self.verification.functions.commitmentOf(job_key(job_id)).call()
        return {"provider": c[0], "output_hash": c[1].hex(), "merkle_root": c[2].hex(),
                "leaf_index": int(c[3]), "recorded_ms": int(c[4]) * 1000,
                "challenge_deadline_ms": int(c[5]) * 1000, "resolved": bool(c[6]),
                "resolution": _RESOLUTION[int(c[7])], "blob_ref": c[8]}

    # -- invariants ------------------------------------------------------

    def check_invariants(self) -> dict:
        """Assert value conservation against the chain's own balances.

        Every wei sent to a contract stays in it until somebody pulls it, so the
        change in a contract's ETH balance must equal exactly the change in what
        it owes. A Solidity mapping has no enumerable holder set, so the
        credited side is summed over an account list built two ways: the
        accounts this instance transacted with, plus every account named in a
        value-moving event either contract emitted since the baseline block.
        That second half is what makes the check complete rather than scoped -
        before it existed, any other writer on the same deployment (a second
        test session against the same devnet, say) surfaced as a conservation
        failure instead of as an account nobody had baselined.

        `accounts_checked` and `holders_discovered_from_logs` in the returned
        dict report how the list was built.
        """
        problems: list[str] = []
        # One block for the whole check. Reading "latest" per call would let a
        # transaction land between two reads and turn a correct chain into a
        # reported conservation failure.
        at_block = int(self.w3.eth.block_number)
        at = {"block_identifier": at_block}
        discovered = self._discover_holders(at_block)
        accounts = sorted(self._credited_baseline)

        reg_credited = sum(int(self.registry.functions.withdrawable(a).call(**at))
                           - self._credited_baseline[a][0] for a in accounts)
        mkt_credited = sum(int(self.marketplace.functions.withdrawable(a).call(**at))
                           - self._credited_baseline[a][1] for a in accounts)
        d_staked = (int(self.registry.functions.totalStaked().call(**at))
                    - self._baseline["total_staked"])
        d_unbonding = (int(self.registry.functions.totalUnbonding().call(**at))
                       - self._baseline["total_unbonding"])
        d_slashed = (int(self.registry.functions.totalSlashed().call(**at))
                     - self._baseline["total_slashed"])
        d_reg_balance = (int(self.w3.eth.get_balance(self.registry.address, at_block))
                         - self._baseline["registry_balance"])
        d_mkt_balance = (int(self.w3.eth.get_balance(self.marketplace.address, at_block))
                         - self._baseline["marketplace_balance"])

        # Escrow still held, from the contract's own counters rather than from
        # the jobs this instance knows about: open - paid - refunded is exact
        # and covers escrows opened by anybody.
        d_escrowed = (int(self.marketplace.functions.totalEscrowed().call(**at))
                      - self._baseline["total_escrowed"])
        d_paid = (int(self.marketplace.functions.totalPaidToProviders().call(**at))
                  - self._baseline["total_paid"])
        d_refunded = (int(self.marketplace.functions.totalRefundedToRequesters().call(**at))
                      - self._baseline["total_refunded"])
        held = d_escrowed - d_paid - d_refunded

        ours_held = 0
        for key in self._job_ids:
            e = self.marketplace.functions.escrows(key).call(**at)
            if int(e[5]) in (1, 2):          # OPEN, AWAITING_VERIFICATION
                ours_held += int(e[2])
        if ours_held > held:
            problems.append(
                f"this instance's open escrows hold {ours_held} wei but the marketplace "
                f"counters say only {held} is held in total")

        # (1) the registry holds exactly what it owes
        if d_reg_balance != d_staked + d_unbonding + reg_credited:
            problems.append(
                f"NodeRegistry balance moved {d_reg_balance} but stake moved {d_staked}, "
                f"unbonding {d_unbonding}, credited {reg_credited}")

        # (2) so does the marketplace
        if d_mkt_balance != held + mkt_credited:
            problems.append(
                f"Marketplace balance moved {d_mkt_balance} but holds {held} in escrow "
                f"and owes {mkt_credited}")

        # (3) every slashed wei landed in a reward or the treasury. Summed over
        # record_wei, never over the float view: to_wei(from_wei(x)) is not the
        # identity for a wei value that needs more than 15 significant digits,
        # so reconstructing these from the record could raise on a correct chain.
        record_slashed = sum(w["slash_amount_wei"] for w in self.record_wei)
        record_split = sum(w["validator_reward_wei"] + w["treasury_amount_wei"]
                           for w in self.record_wei)
        if record_slashed != record_split:
            problems.append(f"slash split {record_split} != slashed {record_slashed}")

        # Every wei this instance's records claim was slashed has to appear in a
        # NodeRegistry.Slashed log emitted by one of this instance's own
        # transactions. Comparing against the registry's global totalSlashed
        # instead would fail whenever anything else wrote to the deployment,
        # which is a fact about the deployment, not about these records.
        our_txs = {r.tx_hash for r in self.records}
        logged = 0
        for log in self.registry.events.Slashed().get_logs(
                from_block=self._baseline_block + 1, to_block=at_block):
            if log["transactionHash"].hex() in our_txs:
                logged += int(log["args"]["slashed"])
        if record_slashed != logged:
            problems.append(
                f"records claim {record_slashed} slashed wei but this instance's own "
                f"transactions emitted Slashed logs totalling {logged}")
        if record_slashed > d_slashed:
            problems.append(
                f"records claim {record_slashed} slashed wei but the registry's total moved "
                f"only {d_slashed}")

        # (4) every closed escrow paid exactly one party
        for w in self.record_wei:
            paid = w["provider_payout_wei"] + w["requester_refund_wei"]
            if paid != w["amount_wei"]:
                problems.append(
                    f"job {w['job_id']}: paid {paid} != escrow {w['amount_wei']} wei")

        # (5) the float view a reader sees has to agree with those integers to
        # within float64 precision; a wider gap is a unit bug, not representation.
        for rec, w in zip(self.records, self.record_wei):
            for name, k in (("amount", "amount_wei"), ("slash_amount", "slash_amount_wei"),
                            ("validator_reward", "validator_reward_wei"),
                            ("treasury_amount", "treasury_amount_wei"),
                            ("provider_payout", "provider_payout_wei"),
                            ("requester_refund", "requester_refund_wei")):
                if not math.isclose(getattr(rec, name), from_wei(w[k]),
                                    rel_tol=1e-12, abs_tol=1e-18):
                    problems.append(
                        f"job {rec.job_id}: {name}={getattr(rec, name)} does not match {w[k]} wei")

        if problems:
            from edgegrid.ledger import InvariantViolation
            raise InvariantViolation("; ".join(problems))

        return {
            "backend": self.backend,
            "chain_id": self.w3.eth.chain_id,
            "block": at_block,
            "params": dict(self.params),
            "baseline_block": self._baseline_block,
            "accounts_checked": len(accounts),
            "holders_discovered_from_logs": discovered,
            "registry_balance_delta": from_wei(d_reg_balance),
            "stake_active_delta": from_wei(d_staked),
            "stake_unbonding_delta": from_wei(d_unbonding),
            "registry_credited_delta": from_wei(reg_credited),
            "marketplace_balance_delta": from_wei(d_mkt_balance),
            "escrow_held": from_wei(held),
            "escrow_held_by_this_instance": from_wei(ours_held),
            "marketplace_credited_delta": from_wei(mkt_credited),
            "provider_payout_total": from_wei(
                int(self.marketplace.functions.totalPaidToProviders().call(**at))),
            "requester_refund_total": from_wei(
                int(self.marketplace.functions.totalRefundedToRequesters().call(**at))),
            "slashed_delta": from_wei(d_slashed),
            "slashed_by_this_instance": from_wei(record_slashed),
            "n_settlements": len(self.records),
        }
