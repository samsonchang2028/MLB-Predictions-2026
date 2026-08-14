"""Deterministic unit tests for the market engine (MARKET-001).

Covers known-value conversions, no-vig normalization + vig/overround, edge,
expected value (break-even / +EV / -EV), invalid-input rejection, archive
mapping-status gating, opening-benchmark labeling, timestamp validity, the
CLOSING pregame guard, and provenance preservation.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from market import (
    AMBIGUOUS,
    MATCHED,
    UNMATCHED,
    MarketLabel,
    american_to_decimal,
    american_to_implied_probability,
    decimal_to_implied_probability,
    edge,
    evaluate_benchmark,
    evaluate_pregame,
    expected_value,
    no_vig_two_way,
    opening_market_benchmark_from_archive,
    probability_to_american,
    snapshot_is_pregame_valid,
)


# --------------------------------------------------------------------------- #
# American-odds conversions (known values)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "american, expected_decimal",
    [
        (-110, 1.0 + 100.0 / 110.0),
        (110, 2.10),
        (-200, 1.50),
        (150, 2.50),
        (-100, 2.0),
        (100, 2.0),
    ],
)
def test_american_to_decimal_known_values(american, expected_decimal):
    assert american_to_decimal(american) == pytest.approx(expected_decimal)


@pytest.mark.parametrize(
    "american, expected_probability",
    [
        (-110, 110.0 / 210.0),
        (110, 100.0 / 210.0),
        (-200, 200.0 / 300.0),
        (150, 100.0 / 250.0),
        (-100, 0.5),
        (100, 0.5),
    ],
)
def test_american_to_implied_probability_known_values(american, expected_probability):
    assert american_to_implied_probability(american) == pytest.approx(
        expected_probability
    )


def test_plus_and_minus_100_both_imply_one_half():
    assert american_to_implied_probability(-100) == pytest.approx(0.5)
    assert american_to_implied_probability(100) == pytest.approx(0.5)


def test_integer_valued_float_accepted():
    assert american_to_decimal(-110.0) == pytest.approx(1.0 + 100.0 / 110.0)


def test_decimal_to_implied_probability_roundtrip():
    assert decimal_to_implied_probability(2.0) == pytest.approx(0.5)
    assert decimal_to_implied_probability(2.5) == pytest.approx(0.4)


# --------------------------------------------------------------------------- #
# Invalid input rejection
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", [0, None, "150", 99, -99, 110.5, True, False])
def test_invalid_american_rejected(bad):
    with pytest.raises(ValueError):
        american_to_decimal(bad)
    with pytest.raises(ValueError):
        american_to_implied_probability(bad)


@pytest.mark.parametrize("bad", [1.0, 0.0, -1.5])
def test_decimal_to_implied_probability_rejects_non_positive_payout(bad):
    with pytest.raises(ValueError):
        decimal_to_implied_probability(bad)


# --------------------------------------------------------------------------- #
# Probability -> American odds (MARKET-003, inverse of american_to_implied_probability)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "probability, expected_american",
    [
        (110.0 / 210.0, -110),  # hand-verified: implied of -110
        (100.0 / 210.0, 110),  # hand-verified: implied of +110
        (200.0 / 300.0, -200),  # hand-verified: implied of -200
        (100.0 / 250.0, 150),  # hand-verified: implied of +150
        (0.5, -100),  # boundary: favorite-side convention (see docstring)
    ],
)
def test_probability_to_american_known_values(probability, expected_american):
    assert probability_to_american(probability) == expected_american


def test_probability_to_american_is_integer():
    assert isinstance(probability_to_american(0.3), int)


@pytest.mark.parametrize(
    "american",
    [-110, 110, -200, 150, -100],  # -100 covered separately (boundary asymmetry)
)
def test_probability_to_american_roundtrips_implied_probability(american):
    probability = american_to_implied_probability(american)
    assert probability_to_american(probability) == american


def test_probability_to_american_roundtrip_at_plus_100_boundary():
    # +100 and -100 both imply exactly 0.5; probability_to_american(0.5) picks
    # the favorite-side convention (-100), so +100 round-trips to the nearest
    # valid equal-magnitude price rather than itself.
    probability = american_to_implied_probability(100)
    assert probability_to_american(probability) == -100


@pytest.mark.parametrize("probability", [0.01, 0.1, 0.3, 0.5, 0.7, 0.9, 0.99])
def test_probability_to_american_roundtrip_property_across_range(probability):
    american = probability_to_american(probability)
    recovered = american_to_implied_probability(american)
    # Integer rounding of the American price means recovery is approximate,
    # not exact, except where the theoretical price is already an integer.
    assert recovered == pytest.approx(probability, abs=5e-3)


@pytest.mark.parametrize("bad", [0, 0.0, 1, 1.0, -0.1, 1.1, None, "0.5", True, False])
def test_probability_to_american_rejects_invalid_probability(bad):
    with pytest.raises(ValueError):
        probability_to_american(bad)


def test_probability_to_american_result_never_below_american_floor():
    # Minimum magnitude occurs at the 0.5 boundary and equals exactly 100,
    # which is valid (_validate_american rejects only magnitude < 100).
    for probability in (0.001, 0.2, 0.5, 0.8, 0.999):
        assert abs(probability_to_american(probability)) >= 100


# --------------------------------------------------------------------------- #
# Two-way no-vig normalization + vig/overround
# --------------------------------------------------------------------------- #
def test_no_vig_dead_even_pair_sums_to_one_and_hold_is_4_55_percent():
    market = no_vig_two_way(-110, -110)
    # Raw implied probabilities each 110/210.
    assert market.raw_home_probability == pytest.approx(110.0 / 210.0)
    assert market.raw_away_probability == pytest.approx(110.0 / 210.0)
    # No-vig probabilities are exactly 0.5 / 0.5 and sum to exactly 1.0.
    assert market.no_vig_home_probability == pytest.approx(0.5)
    assert market.no_vig_away_probability == pytest.approx(0.5)
    assert market.no_vig_home_probability + market.no_vig_away_probability == pytest.approx(
        1.0, abs=1e-12
    )
    # Booksum ~1.0476; classic overround ~4.76%; market hold (vig) ~4.55%.
    assert market.booksum == pytest.approx(1.0476190476, abs=1e-9)
    assert market.overround == pytest.approx(0.0476190476, abs=1e-9)
    assert market.vig == pytest.approx(0.0454545454, abs=1e-9)


def test_no_vig_favorite_underdog_sums_to_one():
    market = no_vig_two_way(-200, 150)
    total = market.no_vig_home_probability + market.no_vig_away_probability
    assert total == pytest.approx(1.0, abs=1e-12)
    # Favorite retains higher probability than the underdog after de-vig.
    assert market.no_vig_home_probability > market.no_vig_away_probability


def test_no_vig_proportional_relationship():
    market = no_vig_two_way(-200, 150)
    ratio_raw = market.raw_home_probability / market.raw_away_probability
    ratio_novig = market.no_vig_home_probability / market.no_vig_away_probability
    assert ratio_raw == pytest.approx(ratio_novig)


# --------------------------------------------------------------------------- #
# Edge
# --------------------------------------------------------------------------- #
def test_edge_sign_and_magnitude():
    # Model likes home more than the market -> positive edge of the exact size.
    assert edge(0.60, 0.50) == pytest.approx(0.10)
    # Model likes home less than the market -> negative edge.
    assert edge(0.40, 0.55) == pytest.approx(-0.15)
    assert edge(0.50, 0.50) == pytest.approx(0.0)


def test_edge_rejects_out_of_range_probability():
    with pytest.raises(ValueError):
        edge(1.2, 0.5)
    with pytest.raises(ValueError):
        edge(0.5, -0.1)


# --------------------------------------------------------------------------- #
# Expected value
# --------------------------------------------------------------------------- #
def test_ev_break_even_at_fair_price():
    # Fair coin at +100 (decimal 2.0): EV is exactly zero.
    assert expected_value(0.5, 100) == pytest.approx(0.0, abs=1e-12)


def test_ev_zero_when_model_equals_price_implied_probability():
    # Staking at a price at exactly its own implied probability breaks even.
    p = american_to_implied_probability(-110)
    assert expected_value(p, -110) == pytest.approx(0.0, abs=1e-12)


def test_ev_positive_and_negative_known_values():
    # +100 (decimal 2.0): EV = p*(1) - (1-p) = 2p - 1.
    assert expected_value(0.60, 100) == pytest.approx(0.20)
    assert expected_value(0.40, 100) == pytest.approx(-0.20)


def test_ev_formula_matches_manual_computation():
    p = 0.55
    american = -120
    decimal_odds = american_to_decimal(american)
    manual = p * (decimal_odds - 1.0) - (1.0 - p)
    assert expected_value(p, american) == pytest.approx(manual)


# --------------------------------------------------------------------------- #
# Timestamp validity
# --------------------------------------------------------------------------- #
def _ts(hour: int) -> datetime:
    return datetime(2026, 4, 1, hour, 0, tzinfo=timezone.utc)


def test_snapshot_valid_before_cutoff_before_first_pitch():
    assert snapshot_is_pregame_valid(_ts(10), _ts(12), _ts(19)) is True


def test_snapshot_at_or_after_cutoff_is_invalid():
    # Snapshot exactly at the cutoff is not "before" it.
    assert snapshot_is_pregame_valid(_ts(12), _ts(12), _ts(19)) is False
    # Snapshot after the cutoff.
    assert snapshot_is_pregame_valid(_ts(13), _ts(12), _ts(19)) is False


def test_snapshot_at_or_after_first_pitch_is_invalid():
    assert snapshot_is_pregame_valid(_ts(19), _ts(20), _ts(19)) is False


# --------------------------------------------------------------------------- #
# Pregame evaluation (live snapshot) + provenance preservation
# --------------------------------------------------------------------------- #
def test_evaluate_pregame_preserves_source_and_timestamp():
    snapshot = _ts(10)
    result = evaluate_pregame(
        home_american=-110,
        away_american=-110,
        model_probability_home=0.60,
        source="DraftKings",
        snapshot_timestamp=snapshot,
        prediction_timestamp=_ts(12),
        game_start_timestamp=_ts(19),
    )
    # Book identity + exact snapshot instant survive the computation.
    assert result.source == "DraftKings"
    assert result.snapshot_timestamp == snapshot
    assert result.label is MarketLabel.SNAPSHOT
    assert result.is_benchmark is False
    # Model 0.60 home vs no-vig 0.50 -> +0.10 edge on home, mirror on away.
    assert result.home.edge == pytest.approx(0.10)
    assert result.away.edge == pytest.approx(-0.10)
    assert result.home.model_probability == pytest.approx(0.60)
    assert result.away.model_probability == pytest.approx(0.40)
    # No-vig market still sums to 1.0.
    assert (
        result.market.no_vig_home_probability
        + result.market.no_vig_away_probability
    ) == pytest.approx(1.0, abs=1e-12)


def test_evaluate_pregame_rejects_snapshot_after_first_pitch():
    with pytest.raises(ValueError):
        evaluate_pregame(
            home_american=-110,
            away_american=-110,
            model_probability_home=0.5,
            source="DraftKings",
            snapshot_timestamp=_ts(20),  # after first pitch
            prediction_timestamp=_ts(21),
            game_start_timestamp=_ts(19),
        )


def test_evaluate_pregame_rejects_snapshot_after_cutoff():
    with pytest.raises(ValueError):
        evaluate_pregame(
            home_american=-110,
            away_american=-110,
            model_probability_home=0.5,
            source="DraftKings",
            snapshot_timestamp=_ts(13),  # after prediction cutoff
            prediction_timestamp=_ts(12),
            game_start_timestamp=_ts(19),
        )


def test_evaluate_pregame_accepts_earlier_snapshot():
    result = evaluate_pregame(
        home_american=-150,
        away_american=130,
        model_probability_home=0.55,
        source="FanDuel",
        snapshot_timestamp=_ts(9),
        prediction_timestamp=_ts(12),
        game_start_timestamp=_ts(19),
    )
    assert result.snapshot_timestamp == _ts(9)


def test_evaluate_pregame_refuses_opening_label():
    with pytest.raises(ValueError):
        evaluate_pregame(
            home_american=-110,
            away_american=-110,
            model_probability_home=0.5,
            source="archive",
            snapshot_timestamp=_ts(9),
            prediction_timestamp=_ts(12),
            game_start_timestamp=_ts(19),
            label=MarketLabel.OPENING,
        )


# --------------------------------------------------------------------------- #
# CLOSING: refused as pregame input unless timestamp-valid; ok as benchmark
# --------------------------------------------------------------------------- #
def test_closing_refused_as_pregame_input_when_not_timestamp_valid():
    with pytest.raises(ValueError):
        evaluate_pregame(
            home_american=-110,
            away_american=-110,
            model_probability_home=0.5,
            source="Pinnacle",
            snapshot_timestamp=_ts(19),  # at first pitch -> not valid
            prediction_timestamp=_ts(20),
            game_start_timestamp=_ts(19),
            label=MarketLabel.CLOSING,
        )


def test_closing_allowed_as_posthoc_benchmark():
    result = evaluate_benchmark(
        home_american=-110,
        away_american=-110,
        model_probability_home=0.60,
        source="Pinnacle",
        label=MarketLabel.CLOSING,
    )
    assert result.is_benchmark is True
    assert result.label is MarketLabel.CLOSING
    assert "closing" in result.benchmark_label.lower()
    assert "post-hoc" in result.benchmark_label.lower()
    assert "closing" in result.roi_label.lower()
    assert result.source == "Pinnacle"
    assert result.home.edge == pytest.approx(0.10)


def test_closing_timestamp_valid_is_accepted_as_pregame_input():
    # If a CLOSING price is genuinely timestamp-valid (prediction at close),
    # it is accepted as a pregame input.
    result = evaluate_pregame(
        home_american=-110,
        away_american=-110,
        model_probability_home=0.5,
        source="Pinnacle",
        snapshot_timestamp=_ts(18),
        prediction_timestamp=_ts(18) + timedelta(minutes=30),
        game_start_timestamp=_ts(19),
        label=MarketLabel.CLOSING,
    )
    assert result.label is MarketLabel.CLOSING
    assert result.is_benchmark is False


# --------------------------------------------------------------------------- #
# Archive opening-market benchmark: MATCHED only; labels; exclusions
# --------------------------------------------------------------------------- #
def test_opening_benchmark_matched_is_labeled_and_carries_no_timestamp():
    result = opening_market_benchmark_from_archive(
        mapping_status=MATCHED,
        game_pk=746123,
        sportsbook="BetMGM",
        opening_home_american=-130,
        opening_away_american=110,
        model_probability_home=0.58,
    )
    assert result.label is MarketLabel.OPENING
    assert result.is_benchmark is True
    assert result.benchmark_label == "model edge versus opening market"
    assert result.roi_label == "simulated ROI at opening prices"
    assert result.game_pk == 746123
    assert result.source == "BetMGM"
    # No exact point-in-time price claim from the archive opening line.
    assert result.snapshot_timestamp is None
    # No-vig market still normalized.
    assert (
        result.market.no_vig_home_probability
        + result.market.no_vig_away_probability
    ) == pytest.approx(1.0, abs=1e-12)


@pytest.mark.parametrize("status", [UNMATCHED, AMBIGUOUS])
def test_opening_benchmark_rejects_unmatched_and_ambiguous(status):
    with pytest.raises(ValueError):
        opening_market_benchmark_from_archive(
            mapping_status=status,
            game_pk=None,
            sportsbook="BetMGM",
            opening_home_american=-130,
            opening_away_american=110,
            model_probability_home=0.58,
        )


def test_opening_benchmark_matched_requires_game_pk():
    with pytest.raises(ValueError):
        opening_market_benchmark_from_archive(
            mapping_status=MATCHED,
            game_pk=None,
            sportsbook="BetMGM",
            opening_home_american=-130,
            opening_away_american=110,
            model_probability_home=0.58,
        )


def test_opening_benchmark_rejects_unknown_status():
    with pytest.raises(ValueError):
        opening_market_benchmark_from_archive(
            mapping_status="SOMETHING_ELSE",
            game_pk=1,
            sportsbook="BetMGM",
            opening_home_american=-130,
            opening_away_american=110,
            model_probability_home=0.58,
        )


def test_edge_versus_opening_market_sign():
    # Model 0.58 home vs the de-vigged opening market probability.
    result = opening_market_benchmark_from_archive(
        mapping_status=MATCHED,
        game_pk=1,
        sportsbook="BetMGM",
        opening_home_american=-130,
        opening_away_american=110,
        model_probability_home=0.58,
    )
    market = no_vig_two_way(-130, 110)
    expected_home_edge = 0.58 - market.no_vig_home_probability
    expected_away_edge = 0.42 - market.no_vig_away_probability
    assert result.home.edge == pytest.approx(expected_home_edge)
    assert result.away.edge == pytest.approx(expected_away_edge)
    # Home and away edges are mirror images (both markets sum to 1.0).
    assert result.home.edge + result.away.edge == pytest.approx(0.0, abs=1e-12)


# --------------------------------------------------------------------------- #
# Source identity validation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad_source", ["", "   ", None, 123])
def test_blank_source_rejected(bad_source):
    with pytest.raises(ValueError):
        evaluate_benchmark(
            home_american=-110,
            away_american=-110,
            model_probability_home=0.5,
            source=bad_source,
            label=MarketLabel.SNAPSHOT,
        )
