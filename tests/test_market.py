"""Tests for the hybrid market protocol.

The auction is the one part of the system with money attached, so these tests
pin down the rules rather than the implementation: what the winner is paid, what
gets excluded and why, and that two runs over the same bids agree exactly.
"""

from __future__ import annotations

import math

import pytest

from edgegrid import config as C
from edgegrid import market
from edgegrid.identity import Identity
from edgegrid.schemas import Bid, HardwareTier, JobRequest

REQUESTER = Identity.generate()


def make_job(**kw) -> JobRequest:
    job = JobRequest(
        prompt="What is the capital of France?",
        model="qwen3-vl:2b-instruct",
        requester_peer_id="peer-requester",
        requester_wallet=REQUESTER.address,
        max_price=kw.pop("max_price", 1.0),
        max_latency_ms=kw.pop("max_latency_ms", 30_000),
        min_tier=kw.pop("min_tier", HardwareTier.CPU),
        **kw,
    )
    REQUESTER.sign_message(job)
    return job


def make_bid(job: JobRequest, price: float, *, peer: str = "peer-a",
             ttft: float = 1000.0, warm: bool = False,
             tier: HardwareTier = HardwareTier.CPU,
             identity: Identity | None = None, sign: bool = True,
             created_ms: int | None = None) -> Bid:
    ident = identity or Identity.generate()
    bid = market.bid_for(job, peer_id=peer, wallet=ident.address, price=price,
                         estimated_ttft_ms=ttft, warm=warm, tier=tier)
    if created_ms is not None:
        bid.created_ms = created_ms   # set before signing; it is inside the signature
    if sign:
        ident.sign_message(bid)
    return bid


# --------------------------------------------------------------------------
# second-price correctness
# --------------------------------------------------------------------------

def test_winner_is_cheapest_and_pays_runner_up_price():
    job = make_job()
    bids = [make_bid(job, 0.09, peer="p-mid"),
            make_bid(job, 0.05, peer="p-low"),
            make_bid(job, 0.13, peer="p-high")]
    award = market.second_price_auction(bids, job)
    assert award is not None
    assert award.winner_peer_id == "p-low"
    assert award.winning_bid_price == pytest.approx(0.05)
    # Paid the runner-up's price, not its own: this is what makes bidding the
    # true reserve a dominant strategy.
    assert award.clearing_price == pytest.approx(0.09)
    assert award.n_bids == 3


def test_clearing_price_never_below_the_winning_bid():
    """A procurement auction that paid less than the winner's own bid would not
    be individually rational. Check the invariant across a spread of shapes."""
    job = make_job(max_price=0.5)
    for prices in ([0.1, 0.2], [0.1, 0.1], [0.05, 0.4, 0.41], [0.3]):
        bids = [make_bid(job, p, peer=f"p{i}") for i, p in enumerate(prices)]
        award = market.second_price_auction(bids, job)
        assert award is not None
        assert award.winning_bid_price <= award.clearing_price <= job.max_price


def test_no_eligible_bids_returns_none_not_a_fallback_winner():
    job = make_job(max_price=0.05)
    bids = [make_bid(job, 0.20, peer="p1"), make_bid(job, 0.30, peer="p2")]
    outcome = market.evaluate(bids, job)
    assert outcome.award is None
    assert market.second_price_auction(bids, job) is None
    assert outcome.n_received == 2
    assert outcome.reason_counts() == {market.REASON_PRICE_OVER_MAX: 2}


def test_empty_bid_list_returns_none():
    assert market.second_price_auction([], make_job()) is None


# --------------------------------------------------------------------------
# single-bid reserve case
# --------------------------------------------------------------------------

def test_single_bid_clears_at_the_requester_reserve():
    """With no runner-up the reserve is the requester's own declared ceiling.
    Paying the winner's own bid instead would be a first-price rule and would
    reward shading in exactly the case where the incentive is largest."""
    job = make_job(max_price=0.30)
    award = market.second_price_auction([make_bid(job, 0.07, peer="only")], job)
    assert award is not None
    assert award.winner_peer_id == "only"
    assert award.winning_bid_price == pytest.approx(0.07)
    assert award.clearing_price == pytest.approx(0.30)
    assert award.n_bids == 1


def test_single_eligible_bid_after_exclusions_still_uses_the_reserve():
    job = make_job(max_price=0.30, max_latency_ms=2000)
    bids = [make_bid(job, 0.07, peer="fast", ttft=900.0),
            make_bid(job, 0.02, peer="slow", ttft=9000.0)]
    outcome = market.evaluate(bids, job)
    assert outcome.award.winner_peer_id == "fast"
    assert outcome.award.clearing_price == pytest.approx(0.30)
    assert outcome.reason_counts() == {market.REASON_LATENCY_OVER_BUDGET: 1}


# --------------------------------------------------------------------------
# warm-start bonus
# --------------------------------------------------------------------------

def test_warm_bonus_changes_the_winner():
    """A warm node beats a cheaper cold node when the handicap covers the gap."""
    job = make_job()
    cold = make_bid(job, 0.100, peer="cold")
    warm = make_bid(job, 0.110, peer="warm", warm=True)   # effective 0.0935
    assert market.score_bid(warm, job) < market.score_bid(cold, job)
    award = market.second_price_auction([cold, warm], job)
    assert award.winner_peer_id == "warm"
    assert award.winning_bid_price == pytest.approx(0.110)
    # Threshold price: the most the warm node could have bid and still won.
    expected = 0.100 / (1.0 - C.WARM_START_BONUS)
    assert award.clearing_price == pytest.approx(expected)
    assert award.clearing_price >= award.winning_bid_price


def test_warm_bonus_is_not_enough_to_overcome_a_big_gap():
    job = make_job()
    cold = make_bid(job, 0.050, peer="cold")
    warm = make_bid(job, 0.110, peer="warm", warm=True)   # effective 0.0935
    award = market.second_price_auction([cold, warm], job)
    assert award.winner_peer_id == "cold"
    # The cold winner is paid the score it had to beat, which is the warm
    # runner-up's *discounted* price - paying its 0.110 sticker would overpay for
    # a cold-start the requester never got.
    assert award.clearing_price == pytest.approx(0.110 * (1 - C.WARM_START_BONUS))


def test_warm_discount_is_applied_to_the_score_only():
    job = make_job()
    warm = make_bid(job, 0.20, peer="w", warm=True)
    assert market.effective_price(warm) == pytest.approx(0.20 * (1 - C.WARM_START_BONUS))
    award = market.second_price_auction([warm], job)
    # Payout comes from the reserve, never from the discounted score.
    assert award.clearing_price == pytest.approx(job.max_price)


# --------------------------------------------------------------------------
# hard constraints
# --------------------------------------------------------------------------

def test_latency_budget_excludes_a_cheaper_bid():
    job = make_job(max_latency_ms=1500)
    cheap_slow = make_bid(job, 0.01, peer="slow", ttft=5000.0)
    dear_fast = make_bid(job, 0.20, peer="fast", ttft=900.0)
    assert market.score_bid(cheap_slow, job) == math.inf
    assert (market.exclusion_reason(cheap_slow, job)
            == market.REASON_LATENCY_OVER_BUDGET)
    award = market.second_price_auction([cheap_slow, dear_fast], job)
    assert award.winner_peer_id == "fast"
    # The excluded bid must not become the runner-up either: with one eligible
    # bid left, the reserve applies.
    assert award.clearing_price == pytest.approx(job.max_price)
    assert award.n_bids == 1


def test_latency_exactly_at_the_budget_is_eligible():
    job = make_job(max_latency_ms=1500)
    assert market.exclusion_reason(make_bid(job, 0.1, ttft=1500.0), job) is None


def test_tier_below_minimum_is_excluded():
    job = make_job(min_tier=HardwareTier.DISCRETE_GPU)
    weak = make_bid(job, 0.01, peer="cpu", tier=HardwareTier.CPU)
    strong = make_bid(job, 0.30, peer="gpu", tier=HardwareTier.DISCRETE_GPU)
    assert market.exclusion_reason(weak, job) == market.REASON_TIER_BELOW_MIN
    award = market.second_price_auction([weak, strong], job)
    assert award.winner_peer_id == "gpu"


def test_price_over_ceiling_is_excluded_and_the_ceiling_itself_is_not():
    job = make_job(max_price=0.10)
    assert market.exclusion_reason(make_bid(job, 0.10), job) is None
    assert market.exclusion_reason(make_bid(job, 0.1001), job) == market.REASON_PRICE_OVER_MAX


def test_bid_for_a_different_job_is_excluded():
    job_a, job_b = make_job(), make_job()
    stray = make_bid(job_b, 0.01, peer="stray")
    assert market.exclusion_reason(stray, job_a) == market.REASON_JOB_MISMATCH
    assert market.second_price_auction([stray], job_a) is None


def test_nonpositive_price_is_excluded():
    job = make_job()
    assert market.exclusion_reason(make_bid(job, 0.0), job) == market.REASON_NONPOSITIVE_PRICE
    assert market.exclusion_reason(make_bid(job, -1.0), job) == market.REASON_NONPOSITIVE_PRICE


# --------------------------------------------------------------------------
# signatures
# --------------------------------------------------------------------------

def test_unsigned_bid_is_rejected():
    job = make_job()
    unsigned = make_bid(job, 0.01, peer="unsigned", sign=False)
    assert unsigned.signature is None
    assert market.exclusion_reason(unsigned, job) == market.REASON_BAD_SIGNATURE
    assert market.score_bid(unsigned, job) == math.inf


def test_bid_signed_by_the_wrong_key_is_rejected():
    """The cheapest bid in the room is worthless if it was not signed by the
    wallet it claims - that wallet is the one settlement will pay and slash."""
    job = make_job()
    impostor, victim = Identity.generate(), Identity.generate()
    bid = market.bid_for(job, peer_id="p-forged", wallet=victim.address,
                         price=0.001, estimated_ttft_ms=100.0)
    impostor.sign_message(bid)
    assert market.exclusion_reason(bid, job) == market.REASON_BAD_SIGNATURE

    honest = make_bid(job, 0.20, peer="p-honest")
    award = market.second_price_auction([bid, honest], job)
    assert award.winner_peer_id == "p-honest"


def test_tampered_bid_is_rejected():
    job = make_job()
    ident = Identity.generate()
    bid = make_bid(job, 0.20, peer="p", identity=ident)
    assert market.exclusion_reason(bid, job) is None
    bid.price = 0.01  # signature covers canonical bytes, so this must not verify
    assert market.exclusion_reason(bid, job) == market.REASON_BAD_SIGNATURE


def test_signature_checking_can_be_disabled_for_offline_replay():
    """Replaying a CSV of historical bids has no keys to check against; the
    caller must ask for that explicitly rather than getting it by accident."""
    job = make_job()
    unsigned = make_bid(job, 0.01, peer="p", sign=False)
    assert market.second_price_auction([unsigned], job) is None
    award = market.second_price_auction([unsigned], job, require_signature=False)
    assert award is not None and award.winner_peer_id == "p"


# --------------------------------------------------------------------------
# determinism
# --------------------------------------------------------------------------

def test_tie_break_is_by_ttft_then_peer_id():
    job = make_job()
    same_price_slow = make_bid(job, 0.10, peer="zzz", ttft=900.0)
    same_price_fast = make_bid(job, 0.10, peer="aaa", ttft=800.0)
    award = market.second_price_auction([same_price_slow, same_price_fast], job)
    assert award.winner_peer_id == "aaa"          # faster TTFT wins the tie

    a = make_bid(job, 0.10, peer="bbb", ttft=800.0)
    b = make_bid(job, 0.10, peer="aaa", ttft=800.0)
    award = market.second_price_auction([a, b], job)
    assert award.winner_peer_id == "aaa"          # then peer id, deterministically


def test_result_is_independent_of_bid_arrival_order():
    job = make_job()
    bids = [make_bid(job, 0.10, peer="a", ttft=800.0),
            make_bid(job, 0.10, peer="b", ttft=800.0),
            make_bid(job, 0.07, peer="c", ttft=1200.0),
            make_bid(job, 0.07, peer="d", ttft=1200.0, warm=True)]
    reference = market.second_price_auction(bids, job)
    for perm in ([3, 2, 1, 0], [1, 3, 0, 2], [2, 0, 3, 1]):
        shuffled = [bids[i] for i in perm]
        award = market.second_price_auction(shuffled, job)
        assert award.winner_peer_id == reference.winner_peer_id
        assert award.clearing_price == pytest.approx(reference.clearing_price)
        assert award.winning_bid_price == pytest.approx(reference.winning_bid_price)


def test_float_noise_does_not_break_a_tie():
    """0.011 * 0.85 is 0.009349999999999999, not 0.00935. Without quantisation
    the tie-break would turn on representation noise instead of the stated rule,
    and the winner could flip depending on which price was typed how."""
    job = make_job()
    warm_price = 0.011
    equivalent = round(warm_price * (1 - C.WARM_START_BONUS), 12)
    warm = make_bid(job, warm_price, peer="zzz", warm=True, ttft=1000.0)
    cold = make_bid(job, equivalent, peer="aaa", ttft=1000.0)
    assert market.effective_price(warm) != market.effective_price(cold)  # raw floats differ
    award = market.second_price_auction([warm, cold], job)
    assert award.winner_peer_id == "aaa"          # resolved by peer id, not noise


def test_duplicate_bids_from_one_peer_collapse_to_the_latest():
    """A peer must not be able to be its own runner-up and set its own price."""
    job = make_job(max_price=0.50)
    first = make_bid(job, 0.30, peer="p-dup", created_ms=1_000)
    revised = make_bid(job, 0.10, peer="p-dup", created_ms=2_000)
    other = make_bid(job, 0.40, peer="p-other", created_ms=1_000)
    outcome = market.evaluate([first, revised, other], job)
    assert outcome.n_eligible == 2
    assert outcome.award.winner_peer_id == "p-dup"
    assert outcome.award.winning_bid_price == pytest.approx(0.10)
    assert outcome.award.clearing_price == pytest.approx(0.40)


def test_a_superseded_duplicate_is_reported_not_silently_dropped():
    """The collapse in `rank_bids` used to make the older copy disappear, so
    `n_eligible + len(rejected)` no longer added up to `n_received` and a bid
    the auction had genuinely seen left no trace anywhere in the outcome."""
    job = make_job(max_price=0.50)
    outcome = market.evaluate(
        [make_bid(job, 0.30, peer="p-dup", created_ms=1_000),
         make_bid(job, 0.10, peer="p-dup", created_ms=2_000),
         make_bid(job, 0.40, peer="p-other", created_ms=1_000)], job)
    assert outcome.n_received == 3
    assert outcome.n_accounted == 3
    assert outcome.reason_counts() == {market.REASON_SUPERSEDED: 1}
    superseded = [sb for sb in outcome.rejected if sb.reason == market.REASON_SUPERSEDED]
    assert superseded[0].bid.price == pytest.approx(0.30)


def test_which_duplicate_wins_does_not_depend_on_arrival_order():
    """The revision rule reads the bid's own signed `created_ms`. Reading
    arrival order instead - `latest[peer] = bid` inside the loop - let gossip
    scheduling decide the price: the same three bids delivered in a different
    order cleared at 0.30 instead of 0.10."""
    job = make_job(max_price=0.50)
    old = make_bid(job, 0.30, peer="p-dup", created_ms=1_000)
    new = make_bid(job, 0.10, peer="p-dup", created_ms=2_000)
    other = make_bid(job, 0.40, peer="p-other", created_ms=1_000)
    for order in ([old, new, other], [new, old, other], [other, new, old]):
        award = market.second_price_auction(order, job)
        assert award.winning_bid_price == pytest.approx(0.10)
        assert award.clearing_price == pytest.approx(0.40)


def test_duplicates_with_an_identical_timestamp_still_resolve_deterministically():
    """Two revisions stamped in the same millisecond are broken by the hash of
    the bids themselves, so every node clearing the same set agrees - a tie
    resolved by list position would let two requesters disagree on the price."""
    job = make_job(max_price=0.50)
    a = make_bid(job, 0.30, peer="p-dup", created_ms=1_000)
    b = make_bid(job, 0.10, peer="p-dup", created_ms=1_000)
    other = make_bid(job, 0.40, peer="p-other", created_ms=1_000)
    forward = market.second_price_auction([a, b, other], job)
    backward = market.second_price_auction([other, b, a], job)
    assert forward.winning_bid_price == backward.winning_bid_price
    assert forward.clearing_price == pytest.approx(backward.clearing_price)


def test_outcome_accounts_for_every_bid_it_was_given():
    job = make_job(max_price=0.10, max_latency_ms=2000)
    bids = [make_bid(job, 0.05, peer="ok", ttft=900.0),
            make_bid(job, 0.90, peer="dear", ttft=900.0),
            make_bid(job, 0.05, peer="slow", ttft=9000.0),
            make_bid(job, 0.05, peer="forged", sign=False)]
    outcome = market.evaluate(bids, job)
    assert outcome.n_received == 4
    assert outcome.n_accounted == 4
    assert outcome.reason_counts() == {
        market.REASON_PRICE_OVER_MAX: 1,
        market.REASON_LATENCY_OVER_BUDGET: 1,
        market.REASON_BAD_SIGNATURE: 1,
    }


def test_auction_ms_is_carried_into_the_award():
    job = make_job()
    award = market.second_price_auction([make_bid(job, 0.05)], job, auction_ms=1234.5)
    assert award.auction_ms == pytest.approx(1234.5)


# --------------------------------------------------------------------------
# invariants over randomised bid sets
#
# The hand-written cases above pin the rules the auction is supposed to have.
# These sweep shapes nobody thought to write down - all-warm fields, every bid
# excluded, exact ties, duplicates from the same peer - and assert only the two
# properties that must hold for any input at all.
# --------------------------------------------------------------------------

import random


def _random_case(rng: random.Random):
    job = make_job(max_price=rng.choice([0.05, 0.2, 1.0]),
                   max_latency_ms=rng.choice([500, 2000, 30_000]),
                   min_tier=rng.choice(list(HardwareTier)))
    bids = []
    for i in range(rng.randint(0, 6)):
        bids.append(make_bid(
            job, round(rng.choice([0.0, 0.01, 0.05, 0.2, 0.5, 2.0]), 4),
            peer=f"p{rng.randint(0, 3)}",              # collisions are the point
            ttft=rng.choice([100.0, 1500.0, 5000.0]),
            warm=rng.random() < 0.4,
            tier=rng.choice(list(HardwareTier)),
            sign=rng.random() < 0.9,
            created_ms=rng.choice([1_000, 2_000])))
    return job, bids


def test_every_bid_is_accounted_for_on_random_inputs():
    """`n_eligible + len(rejected)` must equal `n_received` for any input. It
    did not once a peer bid twice: the superseded copy left no trace at all."""
    rng = random.Random(20260902)
    for _ in range(400):
        job, bids = _random_case(rng)
        outcome = market.evaluate(bids, job)
        assert outcome.n_received == len(bids)
        assert outcome.n_accounted == len(bids)
        assert sum(outcome.reason_counts().values()) == len(outcome.rejected)


def test_individual_rationality_holds_on_random_inputs():
    rng = random.Random(1234)
    for _ in range(400):
        job, bids = _random_case(rng)
        award = market.second_price_auction(bids, job)
        if award is None:
            continue
        assert award.winning_bid_price <= award.clearing_price <= job.max_price
        assert award.clearing_price > 0
        assert award.n_bids >= 1


def test_the_outcome_is_the_same_whatever_order_the_bids_arrive_in():
    """Order independence is what makes a CSV replay reproduce a run's price."""
    rng = random.Random(99)
    for _ in range(400):
        job, bids = _random_case(rng)
        reference = market.evaluate(bids, job)
        shuffled = list(bids)
        rng.shuffle(shuffled)
        other = market.evaluate(shuffled, job)
        assert (reference.award is None) == (other.award is None)
        assert reference.reason_counts() == other.reason_counts()
        if reference.award is not None:
            assert reference.award.winner_peer_id == other.award.winner_peer_id
            assert reference.award.winning_bid_price == other.award.winning_bid_price
            assert reference.award.clearing_price == pytest.approx(
                other.award.clearing_price)
