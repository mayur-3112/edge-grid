# Settlement

Four Solidity contracts, deployed and exercised on a local chain, plus a Python
ledger that runs the same state machine off chain and a bridge that lets an
experiment choose between them explicitly.

This directory used to say the contracts were "design artifacts ... NOT deployed
and NOT wired into the demo". That is no longer true: `scripts/deploy.js` puts
them on a chain, `scripts/lifecycle.py` runs complete jobs through them, and
every gas number below came out of a mined receipt.

## What is here

| file | what it does |
|---|---|
| `contracts/NodeRegistry.sol` | provider collateral: stake, timelocked unstake, pull withdrawal, and the only `slash` in the system |
| `contracts/Marketplace.sol` | per-job escrow state machine, challenge-window release, refund on confirmed fraud |
| `contracts/VerificationContract.sol` | output commitments, a trustless Merkle fraud proof, an allow-listed judge verdict, and the 80/20 slash split |
| `contracts/ModelRegistry.sol` | model id → weights content hash; named in the design, never previously written |
| `contracts/Auth.sol` | minimal `Owned` and `ReentrancyGuard` (no OpenZeppelin dependency) |
| `contracts/test/ReentrantRequester.sol` | test-only attacker used to assert the reentrancy property |
| `scripts/deploy.js` | deploys all four, wires them, writes `deployment.json` with per-contract gas |
| `scripts/lifecycle.py` | runs three complete jobs **on chain** and writes a `RunLog` |
| `../edgegrid/ledger.py` | the same state machine in Python, with `check_invariants()` |
| `../edgegrid/chain.py` | the web3 bridge and `get_backend(prefer=...)` |
| `simulate.py` | **deprecated** shim over `edgegrid.ledger`, kept so `verification/run_integration.py` keeps working; a FAIL there goes through the oracle path (`submit_verdict`), because an off-chain judge's ruling is what the caller actually has |

## Escrow state machine

Identical in `Marketplace.sol` and `edgegrid/ledger.py`, and mirrored by
`edgegrid.schemas.EscrowState`:

```
OPEN ──record_commitment──▶ AWAITING_VERIFICATION ──challenge window elapses──▶ SETTLED
                                      │
                                      └──fraud confirmed──▶ SLASHED   (requester refunded,
                                                                       provider stake cut)
OPEN ──no commitment before the award timeout──▶ REFUNDED
```

## Access control

The previous sketches had none. `settle(jobId, slashed)` was callable by anyone,
so any account could declare any job fraudulent and redirect the escrow;
`recordVerdict` was world-writable. Now:

- `NodeRegistry.slash` — only the VerificationContract. Everyone else reverts
  with `NotSlasher`.
- `Marketplace.beginVerification` / `refundOnFraud` — only the
  VerificationContract.
- `Marketplace.release` — permissionless *by design*, but only after the
  challenge deadline. The honest outcome must not depend on a privileged party
  staying online.
- `Marketplace.cancel` — only the requester, only after the award timeout.
- `VerificationContract.recordCommitment` — only the provider the marketplace
  actually awarded.
- `VerificationContract.submitVerdict` — only an allow-listed validator that
  also holds active stake.
- `VerificationContract.proveDataMismatch` — permissionless, because it proves
  rather than asserts.

## Two ways fraud is confirmed, and their different trust assumptions

`proveDataMismatch` is **trustless**. The challenger reveals the DA blob and its
Merkle path; the EVM recomputes the block root and the blob hash. If the data
under the provider's committed root does not hash to the output hash the
provider put on chain, fraud is proven by arithmetic. Sibling direction is
derived from the committed leaf index rather than taken from the caller, so a
challenger cannot substitute a different job's blob from the same DA block, and
a truthful reveal reverts with `NoMismatch` — an honest provider cannot be
slashed by a well-formed challenge.

`edgegrid.ledger.Ledger.prove_data_mismatch` enforces the same two checks in the
same order, and **both are mandatory there too**. It previously skipped the
inclusion check whenever the caller passed no proof or the commitment carried no
root, which meant anyone could slash a provider in simulation by handing in
arbitrary bytes — something the EVM path never permitted, since a rootless
commitment stores `bytes32(0)` and no sibling path folds to zero. A missing root
or a missing proof now raises `MissingInclusionProof` on both backends.
`Ledger.record_commitment` also requires real 32-byte hex digests, so a scenario
that runs in simulation is one the ABI would accept.

`submitVerdict` is an **oracle**. It carries the off-chain LLM judge's ruling, so
it is restricted to allow-listed staked validators. `PASS` and `ERROR` are
recorded and leave the escrow to settle normally; only `FAIL` slashes. A judge
outage is an `ERROR`, never fraud.

The Merkle scheme is byte-compatible with `edgegrid/da.py` — leaves are
`sha256(0x00 ‖ data)`, nodes `sha256(0x01 ‖ left ‖ right)`, odd tail duplicated.
`tests/test_chain.py::test_da_proof_from_python_is_verified_by_the_evm` submits a
proof generated by the Python DA layer and has the EVM check it, so the two
implementations cannot drift apart unnoticed.

## The 80/20 split

On confirmed fraud the provider is slashed for the escrowed amount, capped at
its remaining collateral. `VALIDATOR_SLASH_BPS = 8000` of the slash goes to the
reporter; the treasury gets the **remainder**, computed by subtraction rather
than as a second percentage, so the two always sum to exactly the slashed amount
with no dust stranded. If the provider cannot cover the amount the slash is
capped and `fullyCovered` comes back `false` — under-collateralisation is
reported, never rounded away.

Collateral that is unbonding is still slashable, so a provider cannot escape a
pending challenge by calling `requestUnstake`.

## Running it

```bash
cd contracts && npm install          # already installed in this repo
npx hardhat compile
npx hardhat test                     # 39 tests

# real chain
npx hardhat node &                   # http://127.0.0.1:8545, chainId 31337
npx hardhat run scripts/deploy.js --network localhost   # writes deployment.json
cd .. && .venv/bin/python contracts/scripts/lifecycle.py
.venv/bin/python -m pytest tests/test_ledger.py tests/test_chain.py -q
```

`deployment.json` is machine-local and gitignored. `edgegrid/chain.py` refuses
to start without it and tells you which command to run.

Anything that time-travels the devnet takes `edgegrid.chain.devnet_lock()` —
`scripts/lifecycle.py` and the on-chain tests both do. `evm_increaseTime` moves
the chain's clock for every process on the node, and node-unlocked accounts are
shared, so two sessions against one devnet otherwise corrupt each other's
challenge deadlines and nonces. Those failures read as settlement bugs, which is
why the lock exists rather than a note telling you not to do it.

## Measured gas (Hardhat node, chainId 31337, solc 0.8.24, optimizer runs=200)

Deployment:

| contract | gas |
|---|---|
| NodeRegistry | 1,121,822 |
| Marketplace | 1,198,543 |
| VerificationContract | 1,641,201 |
| ModelRegistry | 870,232 |
| **total (excluding wiring)** | **4,831,798** |

Per operation, from `scripts/lifecycle.py` run twice against the same chain.
Job ids embed the block number, so calldata length — and therefore gas — moves
by a few units between runs; the numbers below are one measured pair, not a
constant.
"first" is the cold-storage cost on a fresh chain, "repeat" is the same call once
the storage slots it touches are already non-zero — the gap is EVM storage
pricing, not two different implementations:

| operation | first | repeat |
|---|---|---|
| `stake` | 69,602 | 35,402 |
| `setValidator` | 47,827 | 27,927 |
| `openEscrow` | 144,033 | 126,933 |
| `recordCommitment` | 201,128 | 184,028 |
| `release` | 78,219 | 61,119 |
| `withdraw` (marketplace) | 32,317 | 32,317 |
| `proveDataMismatch` (3-leaf DA block) | 221,353 | 118,753 |
| `submitVerdict` (FAIL, slashes) | 158,721 | 141,621 |

`proveDataMismatch` scales with the size of the revealed blob and the depth of
the Merkle path, both of which are calldata; the numbers above are for a 31-byte
blob in a three-blob block.

## Choosing a backend

```python
from edgegrid.chain import get_backend

ledger = get_backend("sim")     # edgegrid.ledger.Ledger
chain  = get_backend("chain")   # raises with instructions if the node is down
```

There is deliberately no `"auto"`. A run that quietly downgraded from a real
chain to a simulation would produce numbers nobody could interpret. A keyword
the chosen backend cannot honour raises rather than being dropped:
`get_backend("chain", min_stake=...)` used to return a backend running the
*deployed* minimum while the calling code read as though it had set one.

`ChainBackend` additionally refuses to start when the live contracts were
deployed with different economics than `edgegrid/config.py` is configured for —
`ParamMismatch`, with the redeploy command. The values are read from the
contracts, not from `deployment.json`, so an edited or stale file cannot hide a
divergence; they are also echoed in `check_invariants()["params"]`.

Both backends emit the **same columns** from `rows()`:

| column | meaning |
|---|---|
| `backend` | `sim` or `chain`; never inferred later from an empty `tx_hash` |
| `resolution` | `challenge_window_elapsed`, `award_timeout_cancel`, `data_mismatch_proof`, or `validator_verdict` — whether a slash rested on a proof or on an oracle |
| `reporter` | who was paid the reporter share, `""` when nothing was slashed |
| `verdict`, `quality_score` | the judge ruling, read back from the chain's `VerdictRecorded` log rather than from the argument that was sent |
| `*_wei` | the exact integers; the float columns are the human view of the same numbers |
| `tx_hash`, `gas_used` | real on chain, `""`/`0` in simulation, never fabricated |

## Value conservation

`Ledger.check_invariants()` asserts, in integer wei, that:

1. everything deposited is still somewhere — staked, unbonding, held in an open
   escrow, credited but unpulled, or withdrawn;
2. every closed escrow paid exactly one of the provider or the requester;
3. every slashed wei landed in exactly one of the validator reward or treasury.

`tests/test_ledger.py` drives 300 randomised operations per seed across 12 seeds
and re-checks all three after **every** operation, accepted or rejected. Every
commitment in that run is bound to a real DA Merkle block, so the `fraud`
operation submits a genuine inclusion proof.

`ChainBackend.check_invariants()` does the same against the chain's own
balances, measured as a delta from the state it connected to. Solidity mappings
have no enumerable holder set, so the account list is built two ways: the
accounts this instance transacted with, **plus** every account named in a
value-moving event either contract emitted since the baseline block. Before that
second half existed the check was scoped to whatever accounts the process
happened to touch, and any other writer on the same deployment — a second test
session against the same devnet — surfaced as a conservation failure rather than
as an unbaselined holder. Slash totals are likewise checked against the
`Slashed` logs emitted by *this instance's own transactions*, not against the
registry's global counter.

Both backends reconcile the exact wei, never `to_wei(from_wei(x))`. That
round-trip is not the identity above about 15 significant digits, and the chain
backend used to rebuild its wei that way: an escrow of 0.9053067274661275 GRID
slashes 905306727466127488 wei and splits it into halves that each move by tens
of wei through a float64, so the old check raised `InvariantViolation` on a
chain that was behaving perfectly.
`tests/test_chain.py::test_invariants_hold_for_an_amount_floats_cannot_represent`
is the regression test.

The accounting is in wei because `SettlementRecord` carries floats and a float64
cannot hold 18 significant digits. The integers are the authority; the floats
are a view, and `check_invariants` reconciles them to float64 precision rather
than pretending they are exact.

## Reference

[Morpheus-Lumerin-Node](https://github.com/MorpheusAIs/Morpheus-Lumerin-Node) —
proxy-router plus escrow contract structure; the pattern borrowed is
bid → escrow → settle → slash.

## Known limits

- The chain is a local Hardhat devnet. Gas numbers are real EVM gas; the ETH is
  not real money and `scripts/lifecycle.py` uses `evm_increaseTime` to step over
  the one-hour challenge window, which a public chain would not allow. The run
  manifest records that it did so.
- `ChainBackend.check_invariants()` builds its holder list from this instance's
  transactions plus the value-moving events either contract emitted since the
  baseline block. That covers every account that moved, but it is still a
  delta-based check: it says nothing about value that moved before the instance
  connected. `accounts_checked` and `holders_discovered_from_logs` in the
  returned dict report how the list was built.
- Slashing is 1× the escrowed job value, matching the Python ledger. A
  production parameterisation would slash a multiple of job value or a fraction
  of stake; that is a parameter change, not a redesign.
- `submitVerdict` trusts its validator set. A quorum with disagreement handling
  is Phase-2 work; today `VALIDATOR_QUORUM` is 1.
