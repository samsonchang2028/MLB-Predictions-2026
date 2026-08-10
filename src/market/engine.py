"""Market probability and edge engine (MARKET-001).

Pure, deterministic functions that turn American moneylines into market
probabilities, strip the bookmaker vig, and compare a model probability against
the market. No DB access lives here: callers pass odds values / records and the
timestamps they were observed at.

Formulas
--------
American -> decimal odds:
    favorite (american < 0):   decimal = 1 + 100 / |american|
    underdog (american > 0):   decimal = 1 + american / 100
    (``-100`` and ``+100`` both give decimal ``2.0`` -> implied ``0.5``.)

Decimal -> implied probability:
    implied = 1 / decimal

Two-way no-vig (simple proportional normalization):
    raw_home = implied(home_american)
    raw_away = implied(away_american)
    booksum  = raw_home + raw_away              (> 1 when the book charges vig)
    overround = booksum - 1                      (classic market overround)
    vig       = overround / booksum              (the market "hold"; the fraction
                                                  of the booksum that is margin;
                                                  ~0.0455 for a -110/-110 pair)
    no_vig_home = raw_home / booksum             (proportional normalization,
    no_vig_away = raw_away / booksum              so the two sum to exactly 1.0)

Edge (per side):
    edge = model_probability - no_vig_market_probability

Expected value per 1 unit staked at the offered price, using the MODEL
probability:
    EV = p_model * (decimal_odds - 1) - (1 - p_model)
    (At a fair price where ``p_model`` equals the price's own implied
    probability, ``EV == 0``.)

Provenance / point-in-time (ADR-002, ADR-004)
---------------------------------------------
A market observation always carries its ``source`` (bookmaker/sportsbook
identity) and, for live/future predictions, the odds ``snapshot_timestamp`` it
was taken at. A live pregame market is only valid when the snapshot exists
strictly before the prediction cutoff and the prediction precedes first pitch:
``snapshot_timestamp < prediction_timestamp < game_start_timestamp``.

Odds are labeled ``OPENING`` / ``SNAPSHOT`` / ``CLOSING``:

- ``SNAPSHOT`` — a live timestamped snapshot; the only valid live pregame input,
  and only when it is timestamp-valid.
- ``CLOSING`` — refused as a pregame input unless it is genuinely timestamp-valid
  (i.e. the "prediction timestamp is actually at close"); otherwise usable only
  as a post-hoc benchmark.
- ``OPENING`` — historical archive opening lines (DATA-009). Never a live pregame
  input; they support "model edge versus opening market" / "simulated ROI at
  opening prices" only, and only for ``MATCHED`` archive mappings.
  ``UNMATCHED`` / ``AMBIGUOUS`` archive records never enter canonical evaluation.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime

# Archive mapping statuses (mirror validation.odds_mapping / DATA-009). Kept as
# local constants so this module stays standalone and needs no DB/validation
# import; only MATCHED archive events may enter canonical opening-market eval.
MATCHED = "MATCHED"
UNMATCHED = "UNMATCHED"
AMBIGUOUS = "AMBIGUOUS"

# Float tolerance for "sums to exactly 1.0" style assertions.
PROBABILITY_SUM_TOLERANCE = 1e-9


class MarketLabel(enum.Enum):
    """Which market snapshot a price represents (opening/current/closing)."""

    OPENING = "OPENING"
    SNAPSHOT = "SNAPSHOT"
    CLOSING = "CLOSING"


# --------------------------------------------------------------------------- #
# American-odds conversions
# --------------------------------------------------------------------------- #
def american_to_decimal(american: object) -> float:
    """Convert a valid American moneyline to decimal odds.

    Negatives (favorites) and positives (underdogs) are handled separately.
    ``0``, ``None``, non-numeric, non-integer-ish (e.g. ``110.5``), and
    magnitudes below 100 are rejected with a clear ``ValueError``.
    """
    value = _validate_american(american)
    if value < 0:
        return 1.0 + 100.0 / abs(value)
    return 1.0 + value / 100.0


def american_to_implied_probability(american: object) -> float:
    """Implied win probability of a single American price (``1 / decimal``).

    ``-100`` and ``+100`` both return exactly ``0.5``.
    """
    return 1.0 / american_to_decimal(american)


def decimal_to_implied_probability(decimal_odds: float) -> float:
    """Implied win probability of decimal odds (``1 / decimal``)."""
    if not isinstance(decimal_odds, (int, float)) or isinstance(decimal_odds, bool):
        raise ValueError(f"decimal odds must be numeric, got {decimal_odds!r}")
    if decimal_odds <= 1.0:
        raise ValueError(f"decimal odds must be > 1.0, got {decimal_odds!r}")
    return 1.0 / float(decimal_odds)


# --------------------------------------------------------------------------- #
# Two-way no-vig normalization
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class NoVigMarket:
    """Two-way market with raw implied probabilities and vig removed.

    ``no_vig_home_probability + no_vig_away_probability == 1.0`` within
    ``PROBABILITY_SUM_TOLERANCE`` (simple proportional normalization).
    """

    home_american: int
    away_american: int
    raw_home_probability: float
    raw_away_probability: float
    booksum: float
    overround: float
    vig: float
    no_vig_home_probability: float
    no_vig_away_probability: float


def no_vig_two_way(home_american: object, away_american: object) -> NoVigMarket:
    """Compute raw implied probs, overround/vig, and no-vig probabilities.

    Uses simple proportional normalization: each side's raw implied probability
    is divided by the booksum, so the two no-vig probabilities sum to exactly
    1.0 (within float tolerance).
    """
    home = _validate_american(home_american)
    away = _validate_american(away_american)
    raw_home = american_to_implied_probability(home)
    raw_away = american_to_implied_probability(away)
    booksum = raw_home + raw_away
    if booksum <= 0.0:
        raise ValueError("degenerate market: non-positive booksum")
    overround = booksum - 1.0
    vig = overround / booksum
    return NoVigMarket(
        home_american=home,
        away_american=away,
        raw_home_probability=raw_home,
        raw_away_probability=raw_away,
        booksum=booksum,
        overround=overround,
        vig=vig,
        no_vig_home_probability=raw_home / booksum,
        no_vig_away_probability=raw_away / booksum,
    )


# --------------------------------------------------------------------------- #
# Edge + expected value
# --------------------------------------------------------------------------- #
def edge(model_probability: float, no_vig_market_probability: float) -> float:
    """Model edge for one side: ``model_probability - no_vig_market_probability``."""
    p_model = _validate_probability(model_probability, "model_probability")
    p_market = _validate_probability(
        no_vig_market_probability, "no_vig_market_probability"
    )
    return p_model - p_market


def expected_value(model_probability: float, american: object) -> float:
    """EV per 1 unit staked at ``american`` using the MODEL probability.

    ``EV = p_model * (decimal_odds - 1) - (1 - p_model)``.
    """
    p_model = _validate_probability(model_probability, "model_probability")
    decimal_odds = american_to_decimal(american)
    return p_model * (decimal_odds - 1.0) - (1.0 - p_model)


# --------------------------------------------------------------------------- #
# Timestamp validity (ADR-002)
# --------------------------------------------------------------------------- #
def snapshot_is_pregame_valid(
    snapshot_timestamp: datetime,
    prediction_timestamp: datetime,
    game_start_timestamp: datetime,
) -> bool:
    """True iff the odds snapshot is valid for a pregame prediction.

    Requires ``snapshot_timestamp < prediction_timestamp < game_start_timestamp``
    (ADR-002): the odds must exist strictly before the prediction cutoff, and the
    prediction must precede first pitch. A snapshot at/after the cutoff or at/after
    first pitch is not valid.
    """
    for name, value in (
        ("snapshot_timestamp", snapshot_timestamp),
        ("prediction_timestamp", prediction_timestamp),
        ("game_start_timestamp", game_start_timestamp),
    ):
        if not isinstance(value, datetime):
            raise ValueError(f"{name} must be a datetime, got {value!r}")
    return (
        snapshot_timestamp < prediction_timestamp
        and prediction_timestamp < game_start_timestamp
    )


# --------------------------------------------------------------------------- #
# Evaluation result shapes
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class SideEvaluation:
    """Per-side comparison of the model against the no-vig market."""

    side: str
    american: int
    decimal_odds: float
    no_vig_market_probability: float
    model_probability: float
    edge: float
    expected_value: float


@dataclass(frozen=True)
class MarketEvaluation:
    """Full model-vs-market view for one game, with provenance preserved.

    ``source`` is the bookmaker/sportsbook identity and ``snapshot_timestamp`` is
    the odds instant used (``None`` for archive benchmarks, which make no exact
    point-in-time price claim). ``is_benchmark`` marks post-hoc benchmarks (e.g.
    opening-market or closing-market comparisons) that are never pregame inputs.
    """

    label: MarketLabel
    source: str
    snapshot_timestamp: datetime | None
    is_benchmark: bool
    benchmark_label: str | None
    roi_label: str | None
    game_pk: int | None
    market: NoVigMarket
    home: SideEvaluation
    away: SideEvaluation


def _build_sides(
    market: NoVigMarket, model_probability_home: float
) -> tuple[SideEvaluation, SideEvaluation]:
    p_home = _validate_probability(model_probability_home, "model_probability_home")
    p_away = 1.0 - p_home
    home = SideEvaluation(
        side="home",
        american=market.home_american,
        decimal_odds=american_to_decimal(market.home_american),
        no_vig_market_probability=market.no_vig_home_probability,
        model_probability=p_home,
        edge=edge(p_home, market.no_vig_home_probability),
        expected_value=expected_value(p_home, market.home_american),
    )
    away = SideEvaluation(
        side="away",
        american=market.away_american,
        decimal_odds=american_to_decimal(market.away_american),
        no_vig_market_probability=market.no_vig_away_probability,
        model_probability=p_away,
        edge=edge(p_away, market.no_vig_away_probability),
        expected_value=expected_value(p_away, market.away_american),
    )
    return home, away


# --------------------------------------------------------------------------- #
# Pregame (live/future) evaluation — timestamp-guarded
# --------------------------------------------------------------------------- #
def evaluate_pregame(
    *,
    home_american: object,
    away_american: object,
    model_probability_home: float,
    source: str,
    snapshot_timestamp: datetime,
    prediction_timestamp: datetime,
    game_start_timestamp: datetime,
    label: MarketLabel = MarketLabel.SNAPSHOT,
) -> MarketEvaluation:
    """Evaluate the model against a LIVE pregame market snapshot.

    Guards (ADR-002 / ADR-004):

    - ``OPENING`` archive odds are never a live pregame input -> rejected. Use
      :func:`opening_market_benchmark_from_archive`.
    - ``CLOSING`` odds are refused as a pregame input *unless* they are genuinely
      timestamp-valid (prediction actually at close). The timestamp guard below
      enforces exactly that.
    - The snapshot must be timestamp-valid:
      ``snapshot_timestamp < prediction_timestamp < game_start_timestamp``.

    The returned view preserves ``source`` and the ``snapshot_timestamp`` used.
    """
    if not isinstance(label, MarketLabel):
        raise ValueError(f"label must be a MarketLabel, got {label!r}")
    if label is MarketLabel.OPENING:
        raise ValueError(
            "OPENING archive odds are a benchmark, not a live pregame input; "
            "use opening_market_benchmark_from_archive"
        )
    if snapshot_timestamp is None:
        raise ValueError("a pregame market requires a snapshot_timestamp")
    if not snapshot_is_pregame_valid(
        snapshot_timestamp, prediction_timestamp, game_start_timestamp
    ):
        raise ValueError(
            f"odds snapshot {snapshot_timestamp!r} is not timestamp-valid for a "
            f"pregame prediction (require snapshot < prediction "
            f"{prediction_timestamp!r} < first pitch {game_start_timestamp!r})"
        )
    market = no_vig_two_way(home_american, away_american)
    home, away = _build_sides(market, model_probability_home)
    return MarketEvaluation(
        label=label,
        source=_validate_source(source),
        snapshot_timestamp=snapshot_timestamp,
        is_benchmark=False,
        benchmark_label=None,
        roi_label=None,
        game_pk=None,
        market=market,
        home=home,
        away=away,
    )


# --------------------------------------------------------------------------- #
# Post-hoc benchmarks (never pregame inputs)
# --------------------------------------------------------------------------- #
_BENCHMARK_LABELS: dict[MarketLabel, tuple[str, str]] = {
    MarketLabel.OPENING: (
        "model edge versus opening market",
        "simulated ROI at opening prices",
    ),
    MarketLabel.CLOSING: (
        "model edge versus closing market (post-hoc)",
        "simulated ROI at closing prices (post-hoc)",
    ),
    MarketLabel.SNAPSHOT: (
        "model edge versus market snapshot (post-hoc)",
        "simulated ROI at snapshot prices (post-hoc)",
    ),
}


def evaluate_benchmark(
    *,
    home_american: object,
    away_american: object,
    model_probability_home: float,
    source: str,
    label: MarketLabel,
    snapshot_timestamp: datetime | None = None,
    game_pk: int | None = None,
) -> MarketEvaluation:
    """Evaluate the model against a market as a POST-HOC benchmark.

    This path makes no pregame timestamp claim and must never be used as a live
    pregame input. It labels results explicitly (e.g. closing-market benchmark /
    simulated ROI at closing prices) so downstream consumers cannot mistake a
    benchmark for a timestamp-valid pregame market.
    """
    if not isinstance(label, MarketLabel):
        raise ValueError(f"label must be a MarketLabel, got {label!r}")
    benchmark_label, roi_label = _BENCHMARK_LABELS[label]
    market = no_vig_two_way(home_american, away_american)
    home, away = _build_sides(market, model_probability_home)
    return MarketEvaluation(
        label=label,
        source=_validate_source(source),
        snapshot_timestamp=snapshot_timestamp,
        is_benchmark=True,
        benchmark_label=benchmark_label,
        roi_label=roi_label,
        game_pk=game_pk,
        market=market,
        home=home,
        away=away,
    )


def opening_market_benchmark_from_archive(
    *,
    mapping_status: str,
    game_pk: int | None,
    sportsbook: str,
    opening_home_american: object,
    opening_away_american: object,
    model_probability_home: float,
) -> MarketEvaluation:
    """Opening-market benchmark from a DATA-009 archive mapping.

    Only ``MATCHED`` archive mappings may enter canonical opening-market
    evaluation. ``UNMATCHED`` / ``AMBIGUOUS`` records are rejected explicitly and
    never contribute a market probability. The result is labeled "model edge
    versus opening market" / "simulated ROI at opening prices" and carries no
    ``snapshot_timestamp`` because the archive opening line is not the exact price
    at an arbitrary prediction timestamp (ADR-004).
    """
    if mapping_status == MATCHED:
        if game_pk is None:
            raise ValueError("MATCHED archive mapping must carry a game_pk")
    elif mapping_status in (UNMATCHED, AMBIGUOUS):
        raise ValueError(
            f"{mapping_status} archive records must not enter canonical "
            "opening-market evaluation"
        )
    else:
        raise ValueError(f"unknown archive mapping status: {mapping_status!r}")
    return evaluate_benchmark(
        home_american=opening_home_american,
        away_american=opening_away_american,
        model_probability_home=model_probability_home,
        source=sportsbook,
        label=MarketLabel.OPENING,
        snapshot_timestamp=None,
        game_pk=game_pk,
    )


# --------------------------------------------------------------------------- #
# Validation helpers
# --------------------------------------------------------------------------- #
def _validate_american(value: object) -> int:
    """Return a valid integer American price or raise a clear ``ValueError``.

    Rejects ``None``, booleans, non-numeric values, non-integer-ish floats,
    ``0``, and magnitudes below 100.
    """
    if value is None:
        raise ValueError("American odds must not be None")
    if isinstance(value, bool):
        raise ValueError(f"American odds must be a number, not a bool: {value!r}")
    if not isinstance(value, (int, float)):
        raise ValueError(f"American odds must be numeric, got {value!r}")
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"American odds must be integer-valued, got {value!r}")
    integer_value = int(value)
    if integer_value == 0:
        raise ValueError("American odds must be non-zero")
    if abs(integer_value) < 100:
        raise ValueError(
            f"American odds magnitude must be >= 100, got {integer_value!r}"
        )
    return integer_value


def _validate_probability(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric, got {value!r}")
    probability = float(value)
    if not 0.0 <= probability <= 1.0:
        raise ValueError(f"{name} must be in [0, 1], got {probability!r}")
    return probability


def _validate_source(source: object) -> str:
    if not isinstance(source, str) or not source.strip():
        raise ValueError(f"source/bookmaker identity must be a non-empty string, got {source!r}")
    return source
