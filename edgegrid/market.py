"""The hybrid market protocol: bid scoring and a sealed-bid second-price auction.

This is a *procurement* (reverse) auction. Providers bid the price they want to
be paid; the requester picks the cheapest acceptable bid. So the winner is the
LOWEST bid, and under a Vickrey rule the winner is paid the SECOND-lowest price,
which is therefore at or above its own bid. Bidding your true reserve is the
dominant strategy: your bid decides *whether* you win, never *what* you are paid.

Two details the naive `min(bids, key=price)` rule in the original stub got wrong:

  * Latency, tier and the price ceiling are hard constraints. A bid that misses
    `max_latency_ms`, `min_tier` or `max_price` is not a cheap bid, it is not a
    bid at all. Here it scores `inf` and is reported with a reason.

  * A warm node (model already resident) is worth more than its sticker price,
    because it skips a cold-start load. `C.WARM_START_BONUS` is applied as a
    handicap on the *score* only. It must not be applied to the money, or a warm
    winner could be paid less than its reserve. The clearing price is therefore
    the winner's *threshold* price - the highest it could have bid and still won:

        clearing = runner_up_effective / winner_discount_factor

    With no warm winner that reduces exactly to "the runner-up's price", which
    is the plain Vickrey rule. When the winner *is* warm the clearing price sits
    above the runner-up's sticker price by exactly the handicap - the requester
    pays more in GRID for a node it valued 15% higher, and in effective terms it
    still pays the runner-up's score. That is the standard scoring-auction
    result, and it is what keeps truthful bidding dominant for warm nodes too.
    The invariant either way is `winning_bid_price <= clearing_price <= max_price`:
    a procurement auction that paid its winner less than the winner's own bid
    would not be individually rational, and nobody would bid truthfully again.

With a single eligible bid there is no runner-up, so the clearing price is the
requester's own reserve, `job.max_price`. That is the standard reserve-price
convention: the requester already declared that ceiling as acceptable, so paying
it is truthful, and a monopolist provider still cannot extract more than the
ceiling by bidding low. The alternative - paying the winner's own bid - is a
first-price rule and destroys truthful bidding for exactly the single-bidder
case where the incentive to shade is largest.

Everything here is pure: no sockets, no clock, no config mutation. The auction is
deterministic given the same *set* of bids, in any order, so a run can be
replayed from a CSV and reach the same price. That is stronger than it sounds:
it rules out any rule that reads arrival order, which is why a peer's revised
bid is chosen by its signed `created_ms` rather than by whichever copy gossip
delivered last (see `_revision_key`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional

from edgegrid import config as C
from edgegrid.identity import verify_message
from edgegrid.schemas import Bid, HardwareTier, JobAward, JobRequest, sha256_hex

# Exclusion reasons. Strings, not an enum, because they are written straight into
# result CSVs and read by humans.
REASON_JOB_MISMATCH = "job_mismatch"
REASON_BAD_SIGNATURE = "bad_signature"
REASON_TIER_BELOW_MIN = "tier_below_min"
REASON_LATENCY_OVER_BUDGET = "latency_over_budget"
REASON_PRICE_OVER_MAX = "price_over_max"
REASON_NONPOSITIVE_PRICE = "nonpositive_price"
REASON_SUPERSEDED = "superseded"

# Floats coming off the wire carry representation noise (0.1 * 0.85 is not
# exactly 0.085). Quantising the sort key keeps ties genuinely tied, which is
# what makes the tie-break rule below reproducible.
_QUANT = 12


def _q(x: float) -> float:
    return round(x, _QUANT)


def discount_factor(bid: Bid) -> float:
    """The multiplier applied to a bid's price to get its score.

    Warm nodes get `C.WARM_START_BONUS` off, capped to a sane range so a
    misconfigured bonus cannot make prices negative or free."""
    if not bid.warm:
        return 1.0
    bonus = min(max(C.WARM_START_BONUS, 0.0), 0.99)
    return 1.0 - bonus


def effective_price(bid: Bid) -> float:
    """Price after the warm-start handicap. Ranking only - never the payout."""
    return bid.price * discount_factor(bid)


def exclusion_reason(bid: Bid, job: JobRequest, *,
                     require_signature: bool = True) -> Optional[str]:
    """Why this bid cannot win, or None if it is eligible.

    Hard constraints are checked before soft ones so the reported reason is the
    most fundamental problem, not whichever check happened to run first."""
    if bid.job_id != job.job_id:
        return REASON_JOB_MISMATCH
    if require_signature and not verify_message(bid, bid.bidder_wallet):
        return REASON_BAD_SIGNATURE
    if bid.price <= 0 or not math.isfinite(bid.price):
        return REASON_NONPOSITIVE_PRICE
    if int(bid.tier) < int(job.min_tier):
        return REASON_TIER_BELOW_MIN
    if not math.isfinite(bid.estimated_ttft_ms) or bid.estimated_ttft_ms > job.max_latency_ms:
        return REASON_LATENCY_OVER_BUDGET
    if bid.price > job.max_price:
        return REASON_PRICE_OVER_MAX
    return None


def score_bid(bid: Bid, job: JobRequest, *, require_signature: bool = True) -> float:
    """Effective price of a bid, or `inf` if it violates the job's constraints.

    Lower is better. `inf` means "cannot win", not "expensive"."""
    if exclusion_reason(bid, job, require_signature=require_signature) is not None:
        return math.inf
    return effective_price(bid)


@dataclass(frozen=True)
class ScoredBid:
    bid: Bid
    effective: float
    reason: Optional[str] = None

    @property
    def eligible(self) -> bool:
        return self.reason is None

    def sort_key(self) -> tuple[float, float, str]:
        """Deterministic total order: cheapest effective price, then fastest
        estimated TTFT, then peer id as a final arbitrary-but-stable arbiter."""
        return (_q(self.effective), _q(self.bid.estimated_ttft_ms), self.bid.bidder_peer_id)


@dataclass
class AuctionOutcome:
    """Everything the auction decided, including what it threw away and why."""

    award: Optional[JobAward]
    ranked: list[ScoredBid] = field(default_factory=list)     # eligible, best first
    rejected: list[ScoredBid] = field(default_factory=list)   # with a reason each
    n_received: int = 0

    @property
    def n_eligible(self) -> int:
        return len(self.ranked)

    @property
    def n_accounted(self) -> int:
        """Bids that ended up somewhere. Must equal `n_received`."""
        return len(self.ranked) + len(self.rejected)

    def reason_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for sb in self.rejected:
            counts[sb.reason or "unknown"] = counts.get(sb.reason or "unknown", 0) + 1
        return counts


def _revision_key(bid: Bid) -> tuple[int, str]:
    """Which of two bids from the same peer is the newer revision.

    `created_ms` is the bid's own claim about when it was made, and it is inside
    the signature, so a peer cannot have it rewritten in flight. Exact ties fall
    back to the hash of the canonical bytes: arbitrary, but a property of the
    bids themselves rather than of the order they happened to arrive in. Using
    arrival order here - which is what `latest[peer] = bid` in a loop does -
    makes the auction's outcome depend on gossip scheduling, so the same bid set
    replayed from a CSV in a different row order can pay a different price."""
    return (bid.created_ms, sha256_hex(bid.canonical()))


def rank_bids(bids: Iterable[Bid], job: JobRequest, *,
              require_signature: bool = True) -> tuple[list[ScoredBid], list[ScoredBid]]:
    """Split bids into (eligible, sorted best-first) and (rejected, with reason).

    Duplicate bids from the same peer are collapsed to that peer's newest
    revision, so a provider can revise a bid inside the window without being
    able to occupy two slots and manufacture its own runner-up. The superseded
    copies are reported in `rejected` with REASON_SUPERSEDED rather than
    dropped: every bid handed in must show up in exactly one of the two lists,
    or `n_received` silently stops matching what the auction actually saw."""
    latest: dict[str, Bid] = {}
    rejected: list[ScoredBid] = []
    superseded: list[Bid] = []
    for bid in bids:
        reason = exclusion_reason(bid, job, require_signature=require_signature)
        if reason is not None:
            rejected.append(ScoredBid(bid, math.inf, reason))
            continue
        prev = latest.get(bid.bidder_peer_id)
        if prev is None:
            latest[bid.bidder_peer_id] = bid
        elif _revision_key(bid) > _revision_key(prev):
            latest[bid.bidder_peer_id] = bid
            superseded.append(prev)
        else:
            superseded.append(bid)
    rejected += [ScoredBid(b, math.inf, REASON_SUPERSEDED) for b in superseded]
    ranked = [ScoredBid(b, effective_price(b)) for b in latest.values()]
    ranked.sort(key=ScoredBid.sort_key)
    return ranked, rejected


def evaluate(bids: Iterable[Bid], job: JobRequest, *,
             auction_ms: float = 0.0,
             require_signature: bool = True) -> AuctionOutcome:
    """Run the auction and return the full outcome, rejections included."""
    bids = list(bids)
    ranked, rejected = rank_bids(bids, job, require_signature=require_signature)
    if len(ranked) + len(rejected) != len(bids):
        # Only reachable from a bug in rank_bids, and the one thing that must
        # never happen quietly: a bid that is neither ranked nor rejected has
        # been dropped, and every count derived from this outcome is then wrong.
        raise AssertionError(
            f"auction lost bids: {len(bids)} in, {len(ranked)} ranked, "
            f"{len(rejected)} rejected")
    if not ranked:
        return AuctionOutcome(None, ranked, rejected, len(bids))

    winner = ranked[0]
    if len(ranked) >= 2:
        # The winner's threshold price: the highest it could have bid and still
        # have beaten the runner-up. Equals the runner-up's price when neither
        # side is warm-discounted.
        threshold = ranked[1].effective / discount_factor(winner.bid)
    else:
        threshold = job.max_price

    clearing = min(threshold, job.max_price)

    award = JobAward(
        job_id=job.job_id,
        winner_peer_id=winner.bid.bidder_peer_id,
        winner_wallet=winner.bid.bidder_wallet,
        clearing_price=clearing,
        winning_bid_price=winner.bid.price,
        n_bids=len(ranked),
        auction_ms=auction_ms,
    )
    return AuctionOutcome(award, ranked, rejected, len(bids))


def second_price_auction(bids: Iterable[Bid], job: JobRequest, *,
                         auction_ms: float = 0.0,
                         require_signature: bool = True) -> Optional[JobAward]:
    """The auction as the node uses it: bids in, one unsigned award or None out.

    Returns None when no bid is eligible - an auction with no acceptable bid has
    no winner, and must never fall back to "cheapest ineligible bid"."""
    return evaluate(bids, job, auction_ms=auction_ms,
                    require_signature=require_signature).award


def bid_for(job: JobRequest, *, peer_id: str, wallet: str, price: float,
            estimated_ttft_ms: float, warm: bool = False,
            tier: HardwareTier = HardwareTier.CPU, stake: float = 0.0) -> Bid:
    """Construct (but do not sign) a bid answering `job`. Convenience for nodes
    and tests so the job_id wiring lives in one place."""
    return Bid(
        job_id=job.job_id,
        bidder_peer_id=peer_id,
        bidder_wallet=wallet,
        price=price,
        estimated_ttft_ms=estimated_ttft_ms,
        warm=warm,
        tier=tier,
        stake=stake,
    )
