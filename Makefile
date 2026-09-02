# The Edge Grid — one entry point for every task.
# Everything runs inside .venv; `make setup` creates it.

PY := .venv/bin/python
PIP := .venv/bin/pip

.DEFAULT_GOAL := help
.PHONY: help setup check test test-live lint schemas net gateway chain contracts \
        experiments figures paper clean nuke

help:  ## show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
	  | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup:  ## install the full toolchain (venv, python deps, hardhat, ollama model)
	./setup.sh

check:  ## verify the environment without changing it
	./setup.sh --check

test:  ## run the test suite (skips tests needing live services)
	$(PY) -m pytest tests/ -q -m "not live"

test-live:  ## run everything, including tests that need ollama and a local chain
	$(PY) -m pytest tests/ -q

schemas:  ## regenerate shared/schemas.md from edgegrid/schemas.py
	$(PY) -m edgegrid.schemas --emit-markdown > shared/schemas.md
	@echo "wrote shared/schemas.md"

net:  ## run a 3-node P2P network end to end
	$(PY) discovery/run_network.py --nodes 3

gateway:  ## serve the OpenAI-compatible gateway + operator dashboard on :8000
	$(PY) -m uvicorn gateway.app:app --port 8000 --reload

chain:  ## start a local EVM node (leave running in its own terminal)
	cd contracts && npx hardhat node

contracts:  ## compile, test and deploy the contracts to the local chain
	cd contracts && npx hardhat compile && npx hardhat test
	cd contracts && npx hardhat run scripts/deploy.js --network localhost

experiments:  ## run all four experiments (needs ollama; ~20-30 min on CPU)
	$(PY) experiments/run_all.py

figures:  ## regenerate every figure from the latest result CSVs
	$(PY) -m experiments.make_figures

paper:  ## regenerate the paper's results tables from the CSVs
	$(PY) experiments/make_tables.py

clean:  ## remove caches and build artefacts (keeps results and keys)
	rm -rf .pytest_cache **/__pycache__ contracts/cache contracts/artifacts
	find . -name '__pycache__' -not -path './.venv/*' -prune -exec rm -rf {} +

nuke: clean  ## also remove the venv, node_modules and the local DA store
	rm -rf .venv contracts/node_modules .da

# -- content-addressed model weights (Objective 3 / Module 3) ---------------
# The weight store talks to a real kubo node; these bring one up and take it
# down. Ports are published on loopback only - see deploy/ipfs/README.md.
# Override a busy port:  IPFS_GATEWAY_PORT=8088 make ipfs-up

.PHONY: ipfs-up ipfs-down ipfs-logs weights weights-demo

IPFS_COMPOSE := docker compose -f deploy/ipfs/docker-compose.yml

ipfs-up:  ## start the local IPFS (kubo) node and wait for its API
	$(IPFS_COMPOSE) up -d
	@printf "waiting for the kubo API on 127.0.0.1:$${IPFS_API_PORT:-5001} "
	@for i in $$(seq 1 40); do \
	  if curl -sf -X POST http://127.0.0.1:$${IPFS_API_PORT:-5001}/api/v0/version >/dev/null; then \
	    echo; curl -s -X POST http://127.0.0.1:$${IPFS_API_PORT:-5001}/api/v0/version; echo; \
	    exit 0; \
	  fi; printf '.'; sleep 1; \
	done; echo; echo "kubo did not answer; try: $(IPFS_COMPOSE) logs" >&2; exit 1

ipfs-down:  ## stop the IPFS node (the named volume, and so the blockstore, survives)
	$(IPFS_COMPOSE) down

ipfs-logs:  ## tail the IPFS node's logs
	$(IPFS_COMPOSE) logs -f

weights:  ## run the weight-distribution experiment (needs `make ipfs-up`)
	$(PY) -m inference.weights_cli experiment

weights-demo:  ## publish a synthetic weight file, fetch it cold then warm
	$(PY) -m inference.weights_cli demo
