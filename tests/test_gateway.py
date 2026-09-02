"""Gateway and SDK tests.

Two layers:

  * contract tests run against a `LocalGrid` whose inference and judge calls are
    replaced with deterministic stubs. They assert the *shape* of the OpenAI
    surface, the pipeline wiring, and every honesty property the design turns on:
    a judge outage becomes ERROR rather than a pass, a local run is never labelled
    p2p, and a settlement conserves value.

  * one live test (`-m live`, deselected by default) runs the real pipeline
    against the real Ollama, because a passing contract test on stubs proves
    nothing about whether the thing actually generates tokens.

The stubs replace ONLY the two network calls that leave the process. The auction,
signing, DA layer, sampling and settlement are the real implementations in every
test here.
"""

from __future__ import annotations

import json
from typing import AsyncIterator, Optional

import pytest
from fastapi.testclient import TestClient

from edgegrid import config as C
from edgegrid.da import DALayer, verify_proof
from edgegrid.identity import verify_message
from edgegrid.schemas import (
    Bid,
    Commitment,
    InferenceResult,
    JobAward,
    JobRequest,
    SettlementRecord,
    VerdictKind,
)
from edgegrid import market
from gateway import app as app_module
from gateway import grid as grid_mod
from gateway.events import STAGES, EventBus
from gateway.grid import GridError, LocalGrid

STUB_OUTPUT = "Tokyo is the capital of Japan."
STUB_MODEL = "stub-model:test"


class _FakeOllama:
    """The narrowest possible stand-in for the httpx client: it replays a fixed
    NDJSON body through `.stream()` so the REAL `_stream_inference` runs.

    Stubbing `_stream_inference` itself would mean the measurement code under
    test never executes, which is how a fabricated TTFT went unnoticed.
    """

    def __init__(self, body: str):
        self.body = body

    def stream(self, method, url, **kwargs):
        body = self.body

        class _Resp:
            status_code = 200

            async def aiter_lines(self):
                for line in body.split("\n"):
                    yield line

            async def aread(self):
                return body.encode()

        class _Ctx:
            async def __aenter__(self):
                return _Resp()

            async def __aexit__(self, *exc):
                return False

        return _Ctx()


def _no_probe(g):
    """A `_probe_ollama` that reports a reachable runtime without a socket."""

    async def probe():
        g.ollama_ok, g.ollama_error = True, None
        return [STUB_MODEL], [STUB_MODEL]

    return probe


class StubGrid(LocalGrid):
    """LocalGrid with the two outbound network calls stubbed and nothing else.

    `judge_score=None` makes the judge raise, which is how the ERROR-verdict path
    is exercised without having to take a real judge offline.
    """

    def __init__(self, *args, judge_score: Optional[int] = 5, **kwargs):
        super().__init__(*args, **kwargs)
        self.judge_score = judge_score
        self.inference_calls = 0

    async def start(self) -> None:  # no HTTP client needed
        self.ollama_ok = True
        self.available_models = [STUB_MODEL]
        await self.refresh_nodes()

    async def close(self) -> None:
        pass

    async def _probe_ollama(self):
        self.ollama_ok, self.ollama_error = True, None
        return [STUB_MODEL], [STUB_MODEL]

    def _host_hardware(self):
        return 8, 16.0

    def node_views(self):
        # psutil is real and harmless, but pinning it keeps the test deterministic.
        views = super().node_views()
        for v in views:
            if v["executes"]:
                v["cpu_percent"], v["ram_available_gb"] = 10.0, 8.0
        return views

    async def _stream_inference(self, job, messages, node, temperature) -> AsyncIterator:
        self.inference_calls += 1
        for piece in STUB_OUTPUT.split(" "):
            yield "delta", piece + " "
        result = InferenceResult(
            job_id=job.job_id, provider_peer_id=node.peer_id,
            output=STUB_OUTPUT + " ", model=job.model, tokens_generated=7,
            ttft_ms=42.5, total_ms=180.0, tokens_per_sec=38.9,
            warm=job.model in node.record.warm_models,
            output_hash=InferenceResult.hash_output(STUB_OUTPUT + " "))
        node.identity.sign_message(result)
        yield "result", {"result": result, "prompt_tokens": 11,
                         "prompt_tokens_reported": True,
                         "token_counts_reported": True,
                         "throughput_measured": True,
                         "eval_duration_ns": 180_000_000,
                         "load_duration_ms": 0.0,
                         "done_reason": "stop"}

    async def _judge(self, question, output):
        if self.judge_score is None:
            raise GridError("stub judge is offline")
        return self.judge_score, "stub judge", "stub-judge:test"


@pytest.fixture
def grid(tmp_path):
    import asyncio

    g = StubGrid(EventBus(), da=DALayer(tmp_path / "da"), sample_rate=1.0)
    asyncio.get_event_loop_policy().new_event_loop().run_until_complete(g.start())
    return g


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient wired to a StubGrid, through the app's real lifespan."""

    async def fake_open_grid(bus):
        g = StubGrid(bus, da=DALayer(tmp_path / "da"), sample_rate=1.0)
        await g.start()
        return g, g.mode, "test harness: StubGrid"

    monkeypatch.setattr(app_module, "open_grid", fake_open_grid)
    with TestClient(app_module.app) as c:
        yield c


def _drain(grid, **kwargs):
    """Run one job to completion, returning (text, record)."""
    import asyncio

    async def go():
        parts, rec = [], None
        async for kind, data in grid.run_job(**kwargs):
            (parts.append(data) if kind == "delta" else None)
            if kind == "record":
                rec = data
        return "".join(parts), rec

    return asyncio.run(go())


JOB = dict(messages=[{"role": "user", "content": "What is the capital of Japan?"}],
           model=STUB_MODEL, max_tokens=64, temperature=0.0)


# --------------------------------------------------------------------------
# pipeline
# --------------------------------------------------------------------------

def test_pipeline_runs_all_five_stages(grid):
    text, rec = _drain(grid, **JOB)
    assert text.strip() == STUB_OUTPUT
    assert rec["status"] == "complete"
    assert list(rec["stages"]) == list(STAGES)
    assert [rec["stages"][s]["state"] for s in STAGES] == ["ok"] * 5


def test_every_message_is_signed_and_verifies(grid):
    _, rec = _drain(grid, **JOB)
    job = JobRequest.model_validate(rec["request"])
    assert verify_message(job, job.requester_wallet)
    award = JobAward.model_validate(rec["award"])
    assert verify_message(award, job.requester_wallet)
    for b in rec["bids"]:
        bid = Bid.model_validate({k: v for k, v in b.items()
                                  if k not in ("effective_price", "winner",
                                               "eligible", "reason")})
        assert verify_message(bid, bid.bidder_wallet), "a bid did not verify"
    result = InferenceResult.model_validate(rec["result"])
    commitment = Commitment.model_validate(rec["commitment"])
    winner = next(n for n in grid.nodes if n.peer_id == award.winner_peer_id)
    assert verify_message(result, winner.identity.address)
    assert verify_message(commitment, winner.identity.address)


def test_auction_is_second_price_and_uses_edgegrid_market(grid):
    """The winner is the cheapest effective bid and is paid the clearing price
    computed by `edgegrid.market`, not its own bid."""
    from edgegrid import market

    _, rec = _drain(grid, **JOB)
    award = JobAward.model_validate(rec["award"])
    eligible = [b for b in rec["bids"] if b["eligible"]]
    assert len(eligible) >= 2
    ranked = sorted(eligible, key=lambda b: b["effective_price"])
    assert ranked[0]["winner"] is True
    # Recompute the clearing price straight from the market module.
    bids = [Bid.model_validate({k: v for k, v in b.items()
                                if k not in ("effective_price", "winner", "eligible",
                                             "reason")})
            for b in rec["bids"]]
    job = JobRequest.model_validate(rec["request"])
    expected = market.evaluate(bids, job).award
    assert expected is not None
    assert award.clearing_price == pytest.approx(expected.clearing_price)
    assert award.winner_peer_id == expected.winner_peer_id


def test_warm_discount_affects_ranking_but_never_the_payout(grid):
    _, rec = _drain(grid, **JOB)
    for b in rec["bids"]:
        if not b["eligible"]:
            continue
        if b["warm"]:
            assert b["effective_price"] < b["price"]
        else:
            assert b["effective_price"] == pytest.approx(b["price"])
    award = rec["award"]
    winner = next(b for b in rec["bids"] if b["winner"])
    # A Vickrey winner is never paid less than it asked for.
    assert award["clearing_price"] >= winner["price"]


def test_da_commitment_carries_a_checkable_merkle_proof(grid):
    _, rec = _drain(grid, **JOB)
    commitment = Commitment.model_validate(rec["commitment"])
    assert grid.da.verify_blob(commitment.blob_ref, commitment.output_hash)
    # And a verifier with only the blob, the proof and the root can check it.
    blob = grid.da.get_blob(commitment.blob_ref)
    proof = [tuple(p) for p in rec["da"]["proof"]]
    assert rec["da"]["proof_len"] >= 1, "a single-leaf block proves nothing"
    assert verify_proof(blob, proof, rec["da"]["root"])
    # Tampering with the blob must break the proof.
    assert not verify_proof(blob + b"!", proof, rec["da"]["root"])


def test_verdict_pass_settles_and_conserves_value(grid):
    _, rec = _drain(grid, **JOB)
    assert rec["verdict"]["verdict"] == "pass"
    s = SettlementRecord.model_validate(rec["settlement"])
    assert s.state.value == "settled"
    assert s.provider_payout == pytest.approx(s.amount)
    assert s.provider_payout + s.requester_refund + s.validator_reward + \
        s.treasury_amount == pytest.approx(s.amount)


def test_verdict_fail_slashes_the_provider(tmp_path):
    g = StubGrid(EventBus(), da=DALayer(tmp_path / "da"), sample_rate=1.0, judge_score=1)
    import asyncio
    asyncio.run(g.start())
    before = dict(g.stakes)
    _, rec = _drain(g, **JOB)
    assert rec["verdict"]["verdict"] == "fail"
    s = SettlementRecord.model_validate(rec["settlement"])
    assert s.state.value == "slashed" and s.slashed
    assert s.slash_amount == pytest.approx(s.amount)
    assert s.validator_reward == pytest.approx(s.slash_amount * C.VALIDATOR_SLASH_SHARE)
    assert s.validator_reward + s.treasury_amount == pytest.approx(s.slash_amount)
    assert s.provider_payout == 0.0 and s.requester_refund == pytest.approx(s.amount)
    assert g.stakes[s.provider_peer_id] == pytest.approx(
        before[s.provider_peer_id] - s.slash_amount)


def test_judge_outage_is_an_error_verdict_not_a_pass_and_holds_escrow(tmp_path):
    """The single most important honesty property: an unreachable judge must not
    become a pass, a fail, or a mock."""
    g = StubGrid(EventBus(), da=DALayer(tmp_path / "da"), sample_rate=1.0, judge_score=None)
    import asyncio
    asyncio.run(g.start())
    _, rec = _drain(g, **JOB)
    v = rec["verdict"]
    assert v["verdict"] == VerdictKind.ERROR.value
    assert v["quality_score"] is None
    assert v["judge_backend"] == C.JUDGE_BACKEND, "the real backend must be recorded"
    assert "stub judge is offline" in v["reason"]
    assert v["blob_verified"] is True, "the DA check succeeded; only the judge failed"
    s = rec["settlement"]
    assert s["state"] == "awaiting_verification"
    assert s["provider_payout"] == 0.0 and s["slash_amount"] == 0.0
    assert g.stakes[s["provider_peer_id"]] == pytest.approx(
        next(n.profile.stake for n in g.nodes if n.peer_id == s["provider_peer_id"]))


def test_sampling_skips_verification_below_the_rate(tmp_path):
    g = StubGrid(EventBus(), da=DALayer(tmp_path / "da"), sample_rate=0.0)
    import asyncio
    asyncio.run(g.start())
    _, rec = _drain(g, **JOB)
    assert rec["sampled"] is False
    assert rec["verdict"] is None
    assert rec["stages"]["verify"]["state"] == "skipped"
    assert rec["settlement"]["state"] == "settled"


def test_reverify_reverses_the_previous_settlement(tmp_path):
    """An operator audit must not double-count a payout."""
    import asyncio

    g = StubGrid(EventBus(), da=DALayer(tmp_path / "da"), sample_rate=0.0, judge_score=1)
    asyncio.run(g.start())
    _, rec = _drain(g, **JOB)
    provider = rec["settlement"]["provider_peer_id"]
    stake_before = g.stakes[provider]
    earned_after_first = g.earnings.get(provider, 0.0)
    assert earned_after_first == pytest.approx(rec["settlement"]["amount"])

    audited = asyncio.run(g.reverify(rec["job_id"]))
    assert audited["verdict"]["verdict"] == "fail"
    # The first payout was reversed, then the slash applied once.
    assert g.earnings.get(provider, 0.0) == pytest.approx(0.0)
    assert g.stakes[provider] == pytest.approx(
        stake_before - audited["settlement"]["slash_amount"])


def test_unknown_model_produces_no_award_rather_than_a_fallback(grid):
    _, rec = _drain(grid, **{**JOB, "model": "not-a-model:v9"})
    assert rec["status"] == "error"
    assert "no eligible bids" in rec["error"]
    assert rec["stages"]["auction"]["state"] == "error"
    assert grid.inference_calls == 0, "inference must not run without an award"


def test_price_ceiling_rejects_bids_with_a_recorded_reason(grid):
    _, rec = _drain(grid, **{**JOB, "max_price": 0.0001})
    assert rec["status"] == "error"
    # Every bid was priced out, and the reason is on the record, not swallowed.
    assert "price_over_max" in rec["error"]


# --------------------------------------------------------------------------
# data availability: an outage and a mismatch mean opposite things
# --------------------------------------------------------------------------

def test_da_outage_is_an_error_verdict_and_never_slashes(tmp_path):
    """A DA store that cannot answer is an outage, not evidence of fraud.

    `DALayer.verify_blob` returns one boolean for "blob missing" and "blob does
    not match the commitment". Mapping that boolean to FAIL slashed an honest
    provider's stake for a storage failure - the same defect class as a judge
    outage becoming a fail.
    """
    import asyncio

    g = StubGrid(EventBus(), da=DALayer(tmp_path / "da"), sample_rate=0.0)
    asyncio.run(g.start())
    _, rec = _drain(g, **JOB)
    provider = rec["settlement"]["provider_peer_id"]
    stake_before = g.stakes[provider]

    # The store loses the blob. Nothing about the provider has changed.
    g.da._blob_path(rec["commitment"]["blob_ref"]).unlink()

    audited = asyncio.run(g.reverify(rec["job_id"]))
    v = audited["verdict"]
    assert v["verdict"] == VerdictKind.ERROR.value, "an outage is not a fail"
    assert v["blob_verified"] is False
    assert "DA unavailable" in v["reason"] and "did not return blob" in v["reason"]
    assert v["judge_model"] == "(no judge called)", \
        "no judge ran, so no model may be named on the row"
    s = audited["settlement"]
    assert s["state"] == "awaiting_verification"
    assert s["slash_amount"] == 0.0 and s["provider_payout"] == 0.0
    assert g.stakes[provider] == pytest.approx(stake_before), \
        "a storage outage must not cost the provider stake"


def test_da_mismatch_is_a_fail_and_does_slash(tmp_path):
    """The other half: bytes that do not match the commitment ARE evidence."""
    import asyncio
    import json as _json

    g = StubGrid(EventBus(), da=DALayer(tmp_path / "da"), sample_rate=0.0)
    asyncio.run(g.start())
    _, rec = _drain(g, **JOB)
    provider = rec["settlement"]["provider_peer_id"]
    stake_before = g.stakes[provider]

    # The store returns different bytes than the provider committed to.
    p = g.da._blob_path(rec["commitment"]["blob_ref"])
    doc = _json.loads(p.read_text())
    doc["data"] = "a completely different answer"
    p.write_text(_json.dumps(doc))

    audited = asyncio.run(g.reverify(rec["job_id"]))
    v = audited["verdict"]
    assert v["verdict"] == VerdictKind.FAIL.value
    assert "DA mismatch" in v["reason"] and "committed to" in v["reason"]
    s = audited["settlement"]
    assert s["state"] == "slashed" and s["slash_amount"] > 0
    assert g.stakes[provider] == pytest.approx(stake_before - s["slash_amount"])


def test_check_da_distinguishes_its_three_outcomes(grid):
    """The distinction is a method, not a comment, so it is asserted directly."""
    from gateway.grid import DA_MISMATCH, DA_OK, DA_UNAVAILABLE

    _, rec = _drain(grid, **JOB)
    commitment = Commitment.model_validate(rec["commitment"])
    assert grid.check_da(commitment) == (DA_OK, "")

    forged = commitment.model_copy(update={"output_hash": "00" * 32})
    status, detail = grid.check_da(forged)
    assert status == DA_MISMATCH and "committed to" in detail

    missing = commitment.model_copy(update={"blob_ref": "deadbeef" * 4})
    status, detail = grid.check_da(missing)
    assert status == DA_UNAVAILABLE and "did not return blob" in detail


# --------------------------------------------------------------------------
# bid identity: a signature over a self-asserted wallet proves nothing
# --------------------------------------------------------------------------

def test_a_bid_cannot_impersonate_another_peer(grid):
    """`market.exclusion_reason` checks the signature against `bid.bidder_wallet`,
    a field the bidder writes itself. On its own that admits a bid claiming
    another peer's id, tier and stake while naming the attacker's wallet for the
    payout - and `market.evaluate` accepts it. The requester holds the registry,
    so the requester binds peer id to wallet."""
    from edgegrid import market
    from edgegrid.identity import Identity
    from edgegrid.schemas import HardwareTier

    victim = next(n for n in grid.nodes if n.profile.label == "peer-c")
    attacker = Identity.generate()
    job = JobRequest(prompt="x", model=STUB_MODEL, requester_peer_id=grid.requester_peer_id,
                     requester_wallet=grid.requester.address)
    grid.requester.sign_message(job)
    forged = Bid(job_id=job.job_id,
                 bidder_peer_id=victim.peer_id,          # claims to BE peer-c
                 bidder_wallet=attacker.address,         # ... paid to the attacker
                 price=1e-6, estimated_ttft_ms=1.0, warm=True,
                 tier=HardwareTier.DISCRETE_GPU, stake=victim.record.stake)
    attacker.sign_message(forged)

    # The market module alone would seat it - the signature is valid over the
    # wallet the bid names.
    assert market.evaluate([forged], job).award is not None

    # The gateway's registry check is what refuses it, with a reason.
    reason = grid.admission_reason(forged)
    assert reason is not None and "not the wallet registered" in reason

    outcome, drops = grid.run_auction(job, extra_bids=[forged])
    assert outcome.award is not None
    assert outcome.award.winner_wallet != attacker.address
    assert not any(sb.bid.bidder_wallet == attacker.address for sb in outcome.ranked)
    assert any("not the wallet registered" in d for d in drops), \
        "the refusal must be reported, not silently dropped"


def test_an_unregistered_bidder_is_refused_with_a_reason(grid):
    from edgegrid.identity import Identity
    from edgegrid.schemas import HardwareTier

    stranger = Identity.generate()
    job = JobRequest(prompt="x", model=STUB_MODEL, requester_peer_id=grid.requester_peer_id)
    grid.requester.sign_message(job)
    bid = Bid(job_id=job.job_id, bidder_peer_id="16Uiu2HAmNOTAREALPEER",
              bidder_wallet=stranger.address, price=1e-6, estimated_ttft_ms=1.0,
              tier=HardwareTier.CPU, stake=999.0)
    stranger.sign_message(bid)
    assert "not in the node registry" in (grid.admission_reason(bid) or "")


def test_a_bid_cannot_overstate_its_stake(grid):
    node = next(n for n in grid.nodes if n.profile.label == "peer-d")
    job = JobRequest(prompt="x", model=STUB_MODEL, requester_peer_id=grid.requester_peer_id)
    grid.requester.sign_message(job)
    bid = market.bid_for(job, peer_id=node.peer_id, wallet=node.identity.address,
                         price=1e-6, estimated_ttft_ms=1.0, tier=node.record.tier,
                         stake=node.record.stake * 100)
    node.identity.sign_message(bid)
    assert "claims stake" in (grid.admission_reason(bid) or "")


def test_a_stake_below_the_floor_is_refused_with_a_reason(grid):
    node = next(n for n in grid.nodes if n.profile.label == "peer-d")
    job = JobRequest(prompt="x", model=STUB_MODEL, requester_peer_id=grid.requester_peer_id)
    grid.requester.sign_message(job)
    grid.stakes[node.peer_id] = C.MIN_STAKE / 2
    bid = market.bid_for(job, peer_id=node.peer_id, wallet=node.identity.address,
                         price=1e-6, estimated_ttft_ms=1.0, tier=node.record.tier,
                         stake=C.MIN_STAKE / 2)
    node.identity.sign_message(bid)
    assert "MIN_STAKE" in (grid.admission_reason(bid) or "")


# --------------------------------------------------------------------------
# measurements that must never be substituted
# --------------------------------------------------------------------------

def test_no_first_token_means_no_ttft_rather_than_a_substituted_number(tmp_path):
    """TTFT used to fall back to total wall-clock when the runtime streamed no
    content token. That puts a fabricated latency on the record, into
    `ttft_ms_mean`, and into anything quoted from it."""
    import asyncio

    calls = {"n": 0}

    class SilentRuntime(StubGrid):
        async def _stream_inference(self, job, messages, node, temperature):
            calls["n"] += 1
            # Cover the real code path: build the same `final` dict ollama would
            # send for a response that produced no content, and let the real
            # implementation decide what to do with it.
            async for item in LocalGrid._stream_inference(
                    self, job, messages, node, temperature):
                yield item

        async def _probe_ollama(self):
            self.ollama_ok, self.ollama_error = True, None
            return [STUB_MODEL], [STUB_MODEL]

    g = SilentRuntime(EventBus(), da=DALayer(tmp_path / "da"), sample_rate=0.0)
    asyncio.run(g.start())
    # Point the real streamer at a runtime that yields a `done` frame and nothing
    # else, which is what "generated no tokens" looks like on the wire.
    g._client = _FakeOllama(json.dumps(
        {"model": STUB_MODEL, "message": {"content": ""}, "done": True,
         "done_reason": "length", "eval_count": 0, "eval_duration": 0,
         "prompt_eval_count": 5}))

    _, rec = _drain(g, **JOB)
    assert rec["status"] == "error"
    assert "no content token" in rec["error"]
    assert "time-to-first-token" in rec["error"]
    assert rec["result"] is None, "no InferenceResult may carry a substituted ttft"


def test_throughput_is_not_divided_out_of_a_clock_artefact(tmp_path):
    """Ollama reports eval_duration=1000 ns for a one-token response. Dividing
    gives 1e6 tokens/sec, which used to be recorded as a measurement and averaged
    into `tokens_per_sec_mean`."""
    import asyncio

    g = StubGrid(EventBus(), da=DALayer(tmp_path / "da"), sample_rate=0.0)
    asyncio.run(g.start())
    g._client = _FakeOllama(
        json.dumps({"message": {"content": "Hello"}, "done": False}) + "\n" +
        json.dumps({"model": STUB_MODEL, "message": {"content": ""}, "done": True,
                    "done_reason": "length", "eval_count": 1, "eval_duration": 1000,
                    "prompt_eval_count": 9}))
    g._stream_inference = lambda *a, **k: LocalGrid._stream_inference(g, *a, **k)

    _, rec = _drain(g, **JOB)
    assert rec["status"] == "complete"
    assert rec["result"]["tokens_per_sec"] == 0.0, \
        "1 token / 1 microsecond is a clock artefact, not 1e6 tokens/sec"
    assert rec["execution"]["throughput_measured"] is False
    assert rec["execution"]["eval_duration_ns"] == 1000
    assert any("clock artefact" in n["message"] for n in rec["notes"]), \
        "the refusal to report a rate must itself be recorded"
    # And it must not contaminate the aggregate.
    s = g.stats()
    assert s["tokens_per_sec_mean"] is None
    assert s["tokens_per_sec_n"] == 0 and s["tokens_per_sec_unmeasured"] == 1
    # The TTFT beside it is measured here, not read back, so it survives.
    assert rec["result"]["ttft_ms"] > 0


def test_a_failed_hardware_measurement_is_recorded_not_replaced_by_the_model(tmp_path,
                                                                             monkeypatch):
    """`_host_hardware` used to fall back to NODE_PROFILES[0] - so a failed psutil
    read published the modelled peer's 16 cores / 30 GB in a signed NodeRecord as
    though it had been measured."""
    import asyncio
    import builtins

    real_import = builtins.__import__

    def no_psutil(name, *args, **kwargs):
        if name == "psutil":
            raise ImportError("psutil is not available in this test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_psutil)
    g = LocalGrid(EventBus(), da=DALayer(tmp_path / "da"))
    monkeypatch.setattr(g, "_probe_ollama", _no_probe(g))
    asyncio.run(g.start())

    host = next(v for v in g.node_views() if v["executes"])
    modelled = next(p for p in grid_mod.NODE_PROFILES if p.executes)
    assert host["cpu_count"] == 0 and host["ram_gb"] == 0.0, \
        "an unmeasured host reports the schema's unset value"
    assert host["cpu_count"] != modelled.cpu_count, "never the modelled profile"
    assert host["hardware_measured"] is False
    assert g.host_hardware_error and "psutil" in g.host_hardware_error
    assert host["cpu_percent"] is None and host["ram_available_gb"] is None, \
        "a failed telemetry read is null, never 0.0 - a dashboard cannot tell " \
        "'idle' from 'we could not look' if both render as zero"
    assert host["telemetry_error"] is not None
    assert g.stats()["host_hardware_error"] is not None


# --------------------------------------------------------------------------
# ledger honesty
# --------------------------------------------------------------------------

def test_an_audit_marks_the_superseded_ledger_row_reversed(tmp_path):
    """Reversing the value movement is not enough: the old row stays in the
    ledger, and anyone summing it - the dashboard included - double-counts a
    payout that no longer exists."""
    import asyncio

    g = StubGrid(EventBus(), da=DALayer(tmp_path / "da"), sample_rate=0.0, judge_score=1)
    asyncio.run(g.start())
    _, rec = _drain(g, **JOB)
    provider = rec["settlement"]["provider_peer_id"]
    asyncio.run(g.reverify(rec["job_id"]))

    assert len(g.settlements) == 2, "the audit trail keeps both rows"
    original, audit = g.settlements
    assert original["reversed"] is True and original["reversed_ms"] is not None
    assert audit["reversed"] is False and audit["audit"] is True

    totals = g.ledger_totals()
    assert totals["rows"] == 2 and totals["rows_reversed"] == 1
    # `ledger_totals` rounds for display, so compare to the 6dp it publishes.
    assert totals["paid"] == pytest.approx(sum(g.earnings.values()), abs=1e-6), \
        "ledger totals must equal the value that actually moved"
    assert totals["paid"] == pytest.approx(0.0, abs=1e-6)
    assert totals["slashed"] == pytest.approx(audit["slash_amount"], abs=1e-6)
    # The naive sum over every row is what used to be wrong.
    assert sum(s["provider_payout"] for s in g.settlements) > totals["paid"]


def test_settlements_endpoint_publishes_the_reversal_aware_totals(client):
    r = client.post("/v1/chat/completions", json={
        "model": STUB_MODEL, "messages": [{"role": "user", "content": "hi"}]})
    job_id = r.json()["edgegrid"]["job_id"]
    client.post(f"/api/jobs/{job_id}/verify")
    body = client.get("/api/settlements").json()
    assert body["totals"]["rows_reversed"] == 1
    assert body["totals"]["paid"] == pytest.approx(
        sum(s["earned"] for s in body["stakes"]))
    assert any(s["reversed"] for s in body["settlements"])


def test_stats_reports_the_judge_that_actually_ruled_not_the_configured_one(grid,
                                                                           monkeypatch):
    """`judge_backend` in stats is the CONFIGURED backend - what the next job
    would use. Verdicts already recorded may have come from another one.

    Both backends are pinned explicitly rather than inherited from the ambient
    configuration: the distinction under test is precisely that the two can
    differ, so a test that reads either of them from the environment passes or
    fails according to whatever is in .env, which is how this one broke when the
    project's default judge changed."""
    monkeypatch.setattr(C, "JUDGE_BACKEND", "ollama")
    _drain(grid, **JOB)
    monkeypatch.setattr(C, "JUDGE_BACKEND", "groq")
    s = grid.stats()
    assert s["judge_backend"] == "groq", "the configured backend"
    assert s["judge_backends_used"] == {"ollama": 1}, \
        "the backend that actually produced the recorded verdict"
    assert s["judge_models_used"] == {"stub-judge:test": 1}


# --------------------------------------------------------------------------
# events
# --------------------------------------------------------------------------

def test_event_bus_rejects_unknown_types():
    bus = EventBus()
    with pytest.raises(KeyError):
        bus.publish("job.exploded", job_id="x")


def test_pipeline_emits_ordered_events(grid):
    _drain(grid, **JOB)
    events = grid.bus.replay(500)
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs), "event sequence numbers must be monotonic"
    types = [e["type"] for e in events]
    for expected in ("job.created", "bid", "award", "inference.done", "commit",
                     "verdict", "settlement"):
        assert expected in types


# --------------------------------------------------------------------------
# OpenAI-compatible surface
# --------------------------------------------------------------------------

def test_health_reports_mode_and_never_claims_p2p(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.headers["x-edgegrid-mode"] == "local"
    body = r.json()
    assert body["mode"] == "local"
    assert body["mode_reason"], "the reason for the mode must always be given"
    assert body["stages"] == list(STAGES)
    assert body["judge"]["groq_key_set"] is bool(C.GROQ_API_KEY)


def test_open_grid_reports_every_transport_attempt(tmp_path, monkeypatch):
    """The mode_reason is the whole of what an operator has to go on. It used to
    report only the LAST module tried, so a p2p transport that existed and crashed
    on import was hidden behind an irrelevant AttributeError from a later one."""
    import asyncio
    import sys
    import types

    crashing = types.ModuleType("edgegrid.p2p_crash")

    def _explode(name, *args, **kwargs):
        if name == "edgegrid.p2p_crash":
            raise RuntimeError("libp2p transport failed to initialise: no listen addr")
        return _real_import(name, *args, **kwargs)

    import builtins
    _real_import = builtins.__import__
    monkeypatch.setattr(builtins, "__import__", _explode)
    monkeypatch.setattr(grid_mod, "TRANSPORT_MODULES",
                        ("edgegrid.p2p_crash", "edgegrid.definitely_absent"))
    monkeypatch.setattr(grid_mod.LocalGrid, "start", _noop_start)

    bus = EventBus()
    grid, mode, reason = asyncio.run(grid_mod.open_grid(bus))
    assert mode == "local", "a crashed transport is never labelled p2p"
    assert "FAILED TO IMPORT" in reason and "no listen addr" in reason, \
        "the real transport failure must be named, not masked"
    assert "definitely_absent" in reason, "every attempt is reported, not just the last"
    assert any(e["type"] == "log" and e.get("level") == "warn" for e in bus.replay()), \
        "choosing local is an operator-visible event"
    del crashing, sys


async def _noop_start(self):
    self.ollama_ok, self.ollama_error = False, "not started in this test"


def test_a_transport_that_fails_to_connect_is_recorded_not_swallowed(monkeypatch):
    """A module that exposes open_grid but raises when called must be reported
    verbatim, not reduced to 'exposes no open_grid'."""
    import asyncio
    import builtins
    import types

    mod = types.ModuleType("edgegrid.p2p_broken")

    async def open_grid(bus):
        raise ConnectionRefusedError("no peer answered on /ip4/0.0.0.0/tcp/9000")

    mod.open_grid = open_grid
    _real_import = builtins.__import__
    monkeypatch.setattr(builtins, "__import__",
                        lambda n, *a, **k: mod if n == "edgegrid.p2p_broken"
                        else _real_import(n, *a, **k))
    monkeypatch.setattr(grid_mod, "TRANSPORT_MODULES", ("edgegrid.p2p_broken",))
    monkeypatch.setattr(grid_mod.LocalGrid, "start", _noop_start)

    _, mode, reason = asyncio.run(grid_mod.open_grid(EventBus()))
    assert mode == "local"
    assert "FAILED TO CONNECT" in reason and "no peer answered" in reason


def test_every_response_carries_the_mode_including_errors(client):
    """`x-edgegrid-mode` used to be set only on the success paths, so a 404 or a
    422 - the responses an operator is most likely to be reading - went out with
    no statement of which backend produced them."""
    responses = [
        client.get("/health"),
        client.get("/v1/models"),
        client.get("/api/jobs/does-not-exist"),                       # 404 handler
        client.post("/v1/chat/completions", json={"model": "nope",
                                                  "messages": [{"role": "user",
                                                                "content": "hi"}]}),
        client.post("/v1/chat/completions", json={"model": STUB_MODEL}),  # 422
        client.get("/definitely-not-a-route"),                        # 404 router
    ]
    codes = [r.status_code for r in responses]
    assert 404 in codes and 422 in codes, f"expected the error paths too, got {codes}"
    for r in responses:
        assert r.headers.get("x-edgegrid-mode") == "local", \
            f"{r.request.url} answered {r.status_code} with no mode header"
        assert r.headers.get("x-edgegrid-version")


def test_models_endpoint_is_openai_shaped(client):
    body = client.get("/v1/models").json()
    assert body["object"] == "list"
    assert [m["id"] for m in body["data"]] == [STUB_MODEL]
    assert body["data"][0]["object"] == "model"


def test_chat_completion_non_streaming_matches_openai(client):
    r = client.post("/v1/chat/completions", json={
        "model": STUB_MODEL,
        "messages": [{"role": "user", "content": "What is the capital of Japan?"}],
        "max_tokens": 64})
    assert r.status_code == 200
    assert r.headers["x-edgegrid-mode"] == "local"
    b = r.json()
    assert b["object"] == "chat.completion"
    assert b["id"].startswith("chatcmpl-")
    assert isinstance(b["created"], int)
    assert b["model"] == STUB_MODEL
    choice = b["choices"][0]
    assert choice["index"] == 0
    assert choice["finish_reason"] == "stop"
    assert choice["message"]["role"] == "assistant"
    assert choice["message"]["content"].strip() == STUB_OUTPUT
    assert b["usage"] == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    eg = b["edgegrid"]
    assert eg["mode"] == "local"
    assert eg["n_bids"] >= 2
    assert eg["settlement_state"] in ("settled", "slashed", "awaiting_verification")
    assert eg["detail_url"] == f"/api/jobs/{eg['job_id']}"


def test_chat_completion_streaming_is_a_valid_sse_chunk_stream(client):
    with client.stream("POST", "/v1/chat/completions", json={
            "model": STUB_MODEL,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True}) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        assert r.headers["x-edgegrid-mode"] == "local"
        raw = "".join(r.iter_text())

    frames = [f for f in raw.split("\n\n") if f.strip()]
    assert frames[-1] == "data: [DONE]"
    objs = [json.loads(f[6:]) for f in frames[:-1]]
    assert all(o["object"] == "chat.completion.chunk" for o in objs)
    assert len({o["id"] for o in objs}) == 1
    assert objs[0]["choices"][0]["delta"]["role"] == "assistant"
    text = "".join(o["choices"][0]["delta"].get("content", "") for o in objs)
    assert text.strip() == STUB_OUTPUT
    last = objs[-1]
    assert last["choices"][0]["finish_reason"] == "stop"
    assert last["usage"]["completion_tokens"] == 7
    assert last["edgegrid"]["mode"] == "local"


def test_unknown_model_is_an_openai_error_body(client):
    r = client.post("/v1/chat/completions", json={
        "model": "gpt-5-turbo", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 404
    err = r.json()["error"]
    assert err["type"] == "invalid_request_error"
    assert "not served by any node" in err["message"]
    assert STUB_MODEL in err["message"], "the error must list what IS available"


def test_empty_messages_rejected(client):
    r = client.post("/v1/chat/completions", json={"model": STUB_MODEL, "messages": []})
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "invalid_request_error"


def test_model_alias_resolution_is_reported_not_silent(client, monkeypatch):
    monkeypatch.setattr(C, "OLLAMA_MODEL", STUB_MODEL)
    r = client.post("/v1/chat/completions", json={
        "model": "default", "messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    b = r.json()
    assert b["model"] == STUB_MODEL
    assert any("alias 'default' resolved" in n for n in b["edgegrid"]["notes"])


def test_openai_content_parts_are_accepted(client):
    r = client.post("/v1/chat/completions", json={
        "model": STUB_MODEL,
        "messages": [{"role": "user",
                      "content": [{"type": "text", "text": "What is the capital?"}]}]})
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"].strip() == STUB_OUTPUT


def test_extra_openai_fields_do_not_break_the_endpoint(client):
    r = client.post("/v1/chat/completions", json={
        "model": STUB_MODEL, "messages": [{"role": "user", "content": "hi"}],
        "top_p": 0.9, "presence_penalty": 0.1, "user": "someone", "n": 1,
        "frequency_penalty": 0.0, "stop": None, "seed": 7})
    assert r.status_code == 200


# --------------------------------------------------------------------------
# operator surface
# --------------------------------------------------------------------------

def test_api_nodes(client):
    body = client.get("/api/nodes").json()
    assert body["mode"] == "local"
    nodes = body["nodes"]
    assert len(nodes) == 5
    assert sum(1 for n in nodes if n["executes"]) == 1, "exactly one host runtime"
    assert all(n["signature_valid"] for n in nodes)
    assert all(n["peer_id"].startswith("16Uiu2") for n in nodes), "real libp2p peer ids"


def test_api_jobs_and_job_detail(client):
    r = client.post("/v1/chat/completions", json={
        "model": STUB_MODEL, "messages": [{"role": "user", "content": "hi"}]})
    job_id = r.json()["edgegrid"]["job_id"]
    jobs = client.get("/api/jobs").json()["jobs"]
    assert jobs[0]["job_id"] == job_id
    detail = client.get(f"/api/jobs/{job_id}").json()
    assert detail["job_id"] == job_id
    assert detail["result"]["output"].strip() == STUB_OUTPUT
    assert client.get("/api/jobs/nope").status_code == 404


def test_api_stats_and_settlements(client):
    client.post("/v1/chat/completions", json={
        "model": STUB_MODEL, "messages": [{"role": "user", "content": "hi"}]})
    s = client.get("/api/stats").json()
    assert s["mode"] == "local"
    assert s["jobs_complete"] == 1
    assert s["verdict_pass"] + s["verdict_fail"] + s["verdict_error"] == 1
    assert s["judge_backend"] == C.JUDGE_BACKEND
    assert s["ttft_ms_mean"] == pytest.approx(42.5)

    led = client.get("/api/settlements").json()
    assert len(led["settlements"]) == 1
    assert len(led["stakes"]) == 5
    assert led["settlements"][0]["state"] in ("settled", "slashed")


def test_api_reverify_endpoint(client):
    r = client.post("/v1/chat/completions", json={
        "model": STUB_MODEL, "messages": [{"role": "user", "content": "hi"}]})
    job_id = r.json()["edgegrid"]["job_id"]
    audited = client.post(f"/api/jobs/{job_id}/verify")
    assert audited.status_code == 200
    assert audited.json()["forced_verify"] is True
    assert client.post("/api/jobs/nope/verify").status_code == 400


def test_api_events_replays_the_pipeline(client):
    client.post("/v1/chat/completions", json={
        "model": STUB_MODEL, "messages": [{"role": "user", "content": "hi"}]})
    r = client.get("/api/events?replay=200&follow=false")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    frames = [f for f in r.text.split("\n\n") if f.strip()]

    assert frames[0].startswith("event: hello\ndata: ")
    hello = json.loads(frames[0].split("data: ", 1)[1])
    assert hello["mode"] == "local"
    assert hello["stages"] == list(STAGES)
    assert len(hello["nodes"]) == 5

    events = [json.loads(f[6:]) for f in frames[1:]]
    types = [e["type"] for e in events]
    for expected in ("job.created", "bid", "award", "inference.done", "commit",
                     "settlement"):
        assert expected in types
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)


def test_api_config_never_leaks_the_groq_key(client):
    body = client.get("/api/config").json()
    assert "GROQ_API_KEY" not in body
    assert "GROQ_API_KEY_SET" in body
    assert body["JUDGE_BACKEND"] == C.JUDGE_BACKEND


# --------------------------------------------------------------------------
# dashboard assets
# --------------------------------------------------------------------------

def test_dashboard_loads_and_is_self_contained(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    html = r.text
    assert "The Edge Grid" in html
    for asset in ("/static/styles.css", "/static/app.js"):
        a = client.get(asset)
        assert a.status_code == 200 and len(a.text) > 1000
    # It must work offline: no external origin anywhere in the page or its assets.
    for text in (html, client.get("/static/app.js").text,
                 client.get("/static/styles.css").text):
        assert "http://" not in text.replace("http://www.w3.org/2000/svg", "")
        assert "https://" not in text
    assert "fonts.googleapis" not in html


def test_dashboard_declares_the_five_stages_and_no_emoji(client):
    js = client.get("/static/app.js").text
    assert '["auction", "inference", "commit", "verify", "settle"]' in js
    for text in (client.get("/").text, js, client.get("/static/styles.css").text):
        assert not any(ord(ch) > 0x2700 for ch in text), "no emoji anywhere in the ui"


def test_dashboard_marks_reversed_ledger_rows(client):
    """The ledger panel is the reader of `/api/settlements`. If it renders a
    reversed row like a live one, marking the row on the server changes nothing."""
    js = client.get("/static/app.js").text
    css = client.get("/static/styles.css").text
    assert "r.reversed" in js, "the ledger row must branch on the reversed flag"
    assert "ledgerTotals" in js and "t.rows_live" in js, \
        "the panel header must report value that moved, not a row count"
    assert "tr.reversed" in css, "a reversed row must be visually distinct"


def test_dashboard_palette_is_rationed_and_semantic(client):
    """Colour is part of the spec: one structural accent plus reserved status
    hues. A stray fourth hue means something decorative crept in, and a red cell
    would stop reliably meaning "this is wrong"."""
    import re

    css = client.get("/static/styles.css").text
    hexes = {h.lower() for h in re.findall(r"#([0-9a-fA-F]{6})\b", css)}
    allowed = {
        "f6f5f1", "fffefb", "efeee8",          # paper, surface, sunk
        "16181c", "565b63", "8b9098",          # ink, secondary, dim
        "dcd9d1", "c6c2b8",                    # hairlines
        "1d4e89", "e5ecf5", "b6c8de",          # the one structural accent
        "2c6b4a", "e2efe7", "b3d2c0",          # status: good
        "8a5a10", "f6eeda", "ddc794", "6d4708",  # status: warning
        "9d3626", "f7e7e3", "d9b6ad", "7d2b1e",  # status: bad
    }
    assert hexes <= allowed, f"unexpected colours: {sorted(hexes - allowed)}"
    assert "fonts.googleapis" not in css, "the console must render with no network"


# --------------------------------------------------------------------------
# sdk
# --------------------------------------------------------------------------

def test_sdk_against_the_test_client(client, monkeypatch):
    """The SDK drives the same HTTP surface; point its httpx client at TestClient."""
    from sdk import EdgeGrid

    sdk = EdgeGrid("http://testserver")
    sdk._http = client  # TestClient is an httpx.Client

    assert sdk.health()["mode"] == "local"
    assert sdk.models() == [STUB_MODEL]
    assert len(sdk.nodes()) == 5

    c = sdk.complete("What is the capital of Japan?", model=STUB_MODEL, verify=True)
    assert c.text.strip() == STUB_OUTPUT
    assert c.mode == "local"
    assert c.n_bids >= 2
    assert c.ttft_ms == pytest.approx(42.5)
    assert c.verdict == "pass"
    assert c.settlement_state == "settled"
    assert c.clearing_price >= 0
    assert "job" in c.summary()

    pieces = list(sdk.stream("hi", model=STUB_MODEL))
    assert "".join(pieces).strip() == STUB_OUTPUT
    assert sdk.last_completion.mode == "local"
    assert sdk.last_completion.job_id

    assert len(sdk.jobs()) >= 2
    assert sdk.job(c.job_id)["job_id"] == c.job_id
    assert sdk.stats()["jobs_complete"] >= 2


def test_sdk_raises_a_useful_error_on_a_bad_model(client):
    from sdk import EdgeGrid, EdgeGridError

    sdk = EdgeGrid("http://testserver")
    sdk._http = client
    with pytest.raises(EdgeGridError) as exc:
        sdk.complete("hi", model="gpt-5-turbo")
    assert "404" in str(exc.value) and "not served" in str(exc.value)


# --------------------------------------------------------------------------
# live - real ollama, real judge. `pytest -m live`
# --------------------------------------------------------------------------

@pytest.mark.live
def test_live_pipeline_against_real_ollama():
    """Proves the stubs above are not hiding a broken runtime path.

    The reported TTFT is checked against the arrival of the first delta as this
    test times it independently. `ttft_ms > 0` - which is all this used to assert -
    passes for a hardcoded constant and for total wall-clock substituted in when
    no token arrived; both were live in the implementation and both went unnoticed.
    """
    import asyncio
    import time as _time

    async def go():
        g = LocalGrid(EventBus(), sample_rate=1.0)
        await g.start()
        if not g.ollama_ok:
            pytest.skip(f"ollama unreachable: {g.ollama_error}")
        parts, rec = [], None
        t0 = _time.monotonic()
        observed_ttft = observed_last_delta = None
        async for kind, data in g.run_job(
                messages=[{"role": "user",
                           "content": "In two sentences, explain why latency matters."}],
                model=C.OLLAMA_MODEL, max_tokens=80, temperature=0.0,
                force_verify=True):
            if kind == "delta":
                if observed_ttft is None:
                    observed_ttft = (_time.monotonic() - t0) * 1000.0
                # The inference stage ends at the LAST delta; the record arrives
                # after commit, verify and settle have also run.
                observed_last_delta = (_time.monotonic() - t0) * 1000.0
                parts.append(data)
            if kind == "record":
                rec = data
        await g.close()
        return "".join(parts), rec, observed_ttft, observed_last_delta

    text, rec, observed_ttft, observed_last_delta = asyncio.run(go())
    assert rec["status"] == "complete", rec["error"]
    assert text.strip()
    r = rec["result"]

    # TTFT is a measurement, checked against this test's own stopwatch. The gap is
    # one scheduler hop; the substitutions this guards against are seconds out.
    assert observed_ttft is not None, "the runtime streamed no token"
    assert abs(r["ttft_ms"] - observed_ttft) < 500.0, (
        f"reported ttft_ms={r['ttft_ms']} is not the time this test observed the "
        f"first token at ({observed_ttft:.1f} ms) - a fabricated or substituted value")
    assert r["ttft_ms"] < r["total_ms"], "TTFT must be strictly inside the run"
    assert abs(r["total_ms"] - observed_last_delta) < 500.0, (
        f"reported total_ms={r['total_ms']} is not when this test saw the stream end "
        f"({observed_last_delta:.1f} ms)")

    # Token counts come from the runtime, not from splitting the output on spaces.
    words = len(r["output"].split())
    assert r["tokens_generated"] > 0, "real token count from the runtime"
    assert words >= 10, f"the prompt must produce enough text to compare against: {words}"
    assert r["tokens_generated"] > words, (
        f"tokens_generated={r['tokens_generated']} equals or undercuts the word count "
        f"({words}); a real tokenizer emits more tokens than whitespace words for "
        f"English prose, so this is len(output.split()) rather than eval_count")
    assert rec["execution"]["token_counts_reported"] is True

    # Throughput must be consistent with the tokens and the time it took.
    assert rec["execution"]["throughput_measured"] is True
    generated_s = (r["total_ms"] - r["ttft_ms"]) / 1000.0
    assert r["tokens_per_sec"] * generated_s == pytest.approx(r["tokens_generated"],
                                                              rel=0.35), (
        f"tokens_per_sec={r['tokens_per_sec']} over {generated_s:.2f}s does not "
        f"account for {r['tokens_generated']} tokens")

    assert rec["execution"]["endpoint"] == g_host(), "the endpoint that served it"
    assert rec["verdict"]["judge_backend"] == C.JUDGE_BACKEND
    assert rec["verdict"]["verdict"] in ("pass", "fail", "error")
    assert rec["verdict"]["blob_verified"] is True
    assert rec["verdict"]["judge_model"], "the judge model must be recorded"


def g_host() -> str:
    return C.OLLAMA_HOST.rstrip("/")
