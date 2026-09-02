"""Central configuration. Every tunable in one place, all overridable by env.

Import this rather than reading os.environ anywhere else, so that a run's
configuration can be snapshotted in full alongside its results.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")

def _s(key: str, default: str) -> str: return os.getenv(key, default)
def _i(key: str, default: int) -> int: return int(os.getenv(key, str(default)))
def _f(key: str, default: float) -> float: return float(os.getenv(key, str(default)))
def _b(key: str, default: bool) -> bool:
    return os.getenv(key, str(default)).strip().lower() in ("1", "true", "yes", "on")

# -- paths -----------------------------------------------------------------
RESULTS_DIR = Path(_s("EDGEGRID_RESULTS_DIR", str(REPO_ROOT / "docs" / "results")))
DATA_DIR = Path(_s("EDGEGRID_DATA_DIR", str(REPO_ROOT / "verification" / "data")))
DA_DIR = Path(_s("EDGEGRID_DA_DIR", str(REPO_ROOT / ".da")))
FIGURES_DIR = Path(_s("EDGEGRID_FIGURES_DIR", str(REPO_ROOT / "docs" / "figures")))

# -- inference -------------------------------------------------------------
OLLAMA_HOST = _s("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = _s("OLLAMA_MODEL", "qwen3-vl:2b-instruct")
INFERENCE_TIMEOUT_S = _f("INFERENCE_TIMEOUT_S", 180.0)

# -- judge -----------------------------------------------------------------
GROQ_API_KEY = _s("GROQ_API_KEY", "")
GROQ_JUDGE_MODEL = _s("GROQ_JUDGE_MODEL", "openai/gpt-oss-120b")
OPENROUTER_API_KEY = _s("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = _s("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
OPENROUTER_JUDGE_MODEL = _s("OPENROUTER_JUDGE_MODEL", "google/gemma-4-31b-it:free")
JUDGE_BACKEND = _s("JUDGE_BACKEND", "ollama")        # groq | ollama | mock
JUDGE_MODEL = _s("JUDGE_MODEL", OLLAMA_MODEL)
PASS_THRESHOLD = _i("PASS_THRESHOLD", 3)             # score >= threshold -> pass

# -- market protocol -------------------------------------------------------
BID_WINDOW_S = _f("BID_WINDOW_S", 2.0)
WARM_START_BONUS = _f("WARM_START_BONUS", 0.15)      # 15% effective discount when warm
HEARTBEAT_INTERVAL_S = _f("HEARTBEAT_INTERVAL_S", 5.0)
HEARTBEAT_PORT = _i("HEARTBEAT_PORT", 45820)
GOSSIP_HEARTBEAT_S = _f("GOSSIP_HEARTBEAT_S", 1.0)
MESH_WAIT_S = _f("MESH_WAIT_S", 3.0)

# -- verification ----------------------------------------------------------
SAMPLE_RATE = _f("SAMPLE_RATE", 0.05)                # 5% audit, per the Phase-1 design
VALIDATOR_QUORUM = _i("VALIDATOR_QUORUM", 1)
CHALLENGE_WINDOW_S = _f("CHALLENGE_WINDOW_S", 3600.0)

# -- settlement ------------------------------------------------------------
MIN_STAKE = _f("MIN_STAKE", 10.0)
VALIDATOR_SLASH_SHARE = _f("VALIDATOR_SLASH_SHARE", 0.80)
TREASURY_SLASH_SHARE = _f("TREASURY_SLASH_SHARE", 0.20)
RPC_URL = _s("RPC_URL", "http://127.0.0.1:8545")
DEPLOYMENT_FILE = Path(_s("DEPLOYMENT_FILE", str(REPO_ROOT / "contracts" / "deployment.json")))

# -- baselines -------------------------------------------------------------
CENTRALIZED_USD_PER_1K_TOKENS = _f("CENTRALIZED_USD_PER_1K_TOKENS", 0.002)
GRID_USD = _f("GRID_USD", 0.001)                     # notional GRID -> USD for cost comparison

for _d in (RESULTS_DIR, DATA_DIR, DA_DIR, FIGURES_DIR):
    _d.mkdir(parents=True, exist_ok=True)


def snapshot() -> dict:
    """Every tunable, for the config.json written next to each run's results."""
    return {
        k: (str(v) if isinstance(v, Path) else v)
        for k, v in globals().items()
        if k.isupper() and not k.startswith("_")
        and isinstance(v, (str, int, float, bool, Path))
        and k not in ("GROQ_API_KEY", "OPENROUTER_API_KEY")
    } | {"GROQ_API_KEY_SET": bool(GROQ_API_KEY),
     "OPENROUTER_API_KEY_SET": bool(OPENROUTER_API_KEY)}
