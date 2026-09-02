"""Shared pytest fixtures for every track.

Tracks own their own test modules; anything cross-cutting lives here so the
suite has one definition of "a node", "a job", and "a temp DA layer".
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

# Put the repo root on sys.path so `import edgegrid` works however pytest is
# invoked, not only via `python -m pytest` from the repo root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from edgegrid.da import DALayer  # noqa: E402  (needs the sys.path insert above)
from edgegrid.identity import Identity  # noqa: E402
from edgegrid.schemas import (  # noqa: E402
    Bid,
    HardwareTier,
    InferenceResult,
    JobRequest,
    sha256_hex,
)


def pytest_configure(config):
    config.addinivalue_line("markers", "live: needs a live external service (ollama, hardhat node)")
    config.addinivalue_line("markers", "slow: takes more than a few seconds")


def _reachable(host: str, port: int, timeout: float = 0.4) -> bool:
    try:
        with socket.create_connection((host, port), timeout):
            return True
    except OSError:
        return False


@pytest.fixture(scope="session")
def ollama_up() -> bool:
    return _reachable("localhost", 11434)


@pytest.fixture(scope="session")
def hardhat_up() -> bool:
    return _reachable("127.0.0.1", 8545)


@pytest.fixture
def requester() -> Identity:
    return Identity.from_hex("11" * 32)


@pytest.fixture
def provider() -> Identity:
    return Identity.from_hex("22" * 32)


@pytest.fixture
def validator() -> Identity:
    return Identity.from_hex("33" * 32)


@pytest.fixture
def da(tmp_path: Path) -> DALayer:
    return DALayer(tmp_path / "da")


@pytest.fixture
def job(requester: Identity) -> JobRequest:
    j = JobRequest(
        job_id="job-test-1",
        prompt="What causes ocean tides on Earth?",
        model="qwen3-vl:2b-instruct",
        max_tokens=64,
        requester_peer_id="peer-requester",
        requester_wallet=requester.address,
        max_price=1.0,
        max_latency_ms=30_000,
    )
    requester.sign_message(j)
    return j


@pytest.fixture
def make_bid(provider: Identity):
    """Factory so a test can vary one field without restating the rest."""

    def _make(price=0.5, ttft=800.0, warm=False, peer="peer-provider",
              tier=HardwareTier.CPU, stake=10.0, ident: Identity | None = None,
              job_id="job-test-1", sign=True) -> Bid:
        b = Bid(job_id=job_id, bidder_peer_id=peer,
                bidder_wallet=(ident or provider).address, price=price,
                estimated_ttft_ms=ttft, warm=warm, tier=tier, stake=stake)
        if sign:
            (ident or provider).sign_message(b)
        return b

    return _make


@pytest.fixture
def inference_result(provider: Identity) -> InferenceResult:
    out = "Ocean tides are caused by the gravitational pull of the Moon and the Sun."
    r = InferenceResult(
        job_id="job-test-1", provider_peer_id="peer-provider", output=out,
        model="qwen3-vl:2b-instruct", tokens_generated=17, ttft_ms=690.0,
        total_ms=1450.0, tokens_per_sec=11.7, warm=True, output_hash=sha256_hex(out),
    )
    provider.sign_message(r)
    return r
