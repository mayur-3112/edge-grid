<!-- The original 5-day prototype README, kept for the record. The current
     README.md describes the system as built. -->

# The Edge Grid — Prototype

A small-scale prototype of a decentralized P2P AI inference network, built to generate experimental numbers for a research paper. **Timeline: draft due 2026-08-28 (5 days), 4 people, zero budget.**

## Scope (prototype-scale, stated explicitly as limitations in the paper)

| Full proposal | Prototype |
|---|---|
| Arbitrum Stylus (Rust/WASM) | Solidity on Arbitrum Sepolia testnet |
| Celestia DA | On-chain SHA-256 hash commitment only |
| vLLM (CUDA) | Ollama, one small quantized model (Qwen2.5-1.5B or Llama-3.2-3B) |
| Full economic staking/slashing | **Simulated entirely in Python** (local ledger, no deployment) — real slashing logic, fake economic stakes |
| Fine-tuned LLM-as-judge | Off-the-shelf small model as judge, scored against a TruthfulQA subset |
| On-chain settlement | Solidity contracts written as design artifacts / future work, not deployed or wired into the demo (cut due to 5-day timeline and no blockchain specialist on the team) |

## Design references (read for patterns, not integrated as running code)

- [conduit](https://github.com/skorotkiewicz/conduit) — DHT + model-routing pattern (rust-libp2p) → port pattern to `discovery/`
- [Morpheus-Lumerin-Node](https://github.com/MorpheusAIs/Morpheus-Lumerin-Node) — proxy-router + escrow contract structure → reference for `contracts/`
- [hyperspace-node](https://github.com/hyperspaceai/hyperspace-node) — three-tier routing / reward logic → reference for `discovery/` + `verification/`

## Team split

| Track | Owner | Folder |
|---|---|---|
| Discovery + Market Protocol | A | `discovery/` |
| Edge Inference Engine | B | `inference/` |
| Agentic Verification + Evaluation harness | C | `verification/` |
| Blockchain Settlement | D | `contracts/` |

## Day-by-day (5 days to draft, 2026-08-28)

| Day | Date | Focus |
|---|---|---|
| 1 | 08-23 | Scaffolding (this), each person reads their track's reference repo, confirm who owns what |
| 2 | 08-24 | Parallel build: discovery (DHT+auction), inference (Ollama wrapper), verification (judge+harness). Settlement track writes the local simulation module (no deployment) |
| 3 | 08-25 | Continue build + start integration. Daily sync on message format (see `shared/schemas.md`) — this is the usual break point |
| 4 | 08-26 | Finish integration: client → GossipSub job broadcast → bid → P2P stream → inference → hash commit (local) → validator sampling → simulated settlement. Run experiments (see `docs/EXPERIMENTS.md`) |
| 5 | 08-27 | Write the paper from results; 08-28 buffer/submit |

## Suggested track ownership (mixed specialization, 2 people strong in LLM/ML)

- **Inference Engine** (`inference/`) and **Verification/Evaluation** (`verification/`) → the 2 LLM/ML-strong people. These are the highest-value tracks for the paper's actual results.
- **Discovery + Market Protocol** (`discovery/`) and **Settlement simulation** (`contracts/`) → the other 2, picking whichever they're more comfortable with (networking vs. logic/data structures). Nobody needs blockchain deployment experience since settlement is simulated in Python.

## Setup

```bash
# discovery
cd discovery && pip install -r requirements.txt

# inference
cd inference && pip install -r requirements.txt

# verification
cd verification && pip install -r requirements.txt

# contracts
cd contracts && npm install
```
