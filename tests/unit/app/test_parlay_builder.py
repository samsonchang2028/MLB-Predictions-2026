from __future__ import annotations

from app.parlay_builder import (
    RankMode,
    SourcePolicy,
    build_parlay_leg,
    build_parlay_report,
    compute_parlay_leg_score,
    compute_parlay_metrics,
    filter_eligible_legs,
    rank_legs,
    suggest_parlay_combinations,
)


def _shadow_row(
    game_pk: int,
    *,
    model_p: float = 0.58,
    market_p: float = 0.55,
    home_american: int = -130,
    away_american: int = 110,
    play: bool = True,
    risk_flags: list[str] | None = None,
    pick: str | None = None,
) -> dict:
    edge = model_p - market_p
    side = "HOME" if model_p >= 0.5 else "AWAY"
    return {
        "game_pk": game_pk,
        "matchup": f"Away {game_pk} @ Home {game_pk}",
        "pick": pick or ("Home" if side == "HOME" else "Away"),
        "game_start_pacific": "2026-09-13 19:10 PDT",
        "model_probability": model_p,
        "market_probability": market_p,
        "edge": edge,
        "play": play,
        "home_american": home_american,
        "away_american": away_american,
        "raw_model_favorite": side,
        "model_market_agree": side == ("HOME" if market_p >= 0.5 else "AWAY"),
        "raw_model_side_probability": model_p if side == "HOME" else 1.0 - model_p,
        "risk_flags": risk_flags or [],
    }


def test_parlay_leg_score_penalizes_crossover_and_boosts_agreement() -> None:
    agree = _shadow_row(1, model_p=0.58, market_p=0.55)
    disagree = _shadow_row(2, model_p=0.58, market_p=0.42)
    crossover = _shadow_row(3, model_p=0.52, market_p=0.60, play=True)

    agree_score = compute_parlay_leg_score(agree, side="HOME", model_p=0.58)
    disagree_score = compute_parlay_leg_score(disagree, side="HOME", model_p=0.58)
    crossover_score = compute_parlay_leg_score(crossover, side="HOME", model_p=0.52)

    assert agree_score > disagree_score
    assert disagree_score > crossover_score


def test_filter_play_only_and_agreement_only_policies() -> None:
    rows = [
        _shadow_row(1, play=True),
        _shadow_row(2, play=False, model_p=0.58, market_p=0.42),
        _shadow_row(3, model_p=0.58, market_p=0.42, play=True),
    ]

    play_legs = filter_eligible_legs(rows, source_policy=SourcePolicy.PLAY)
    assert {leg["game_pk"] for leg in play_legs} == {1, 3}

    agreement_legs = filter_eligible_legs(rows, source_policy=SourcePolicy.AGREEMENT_ONLY)
    assert {leg["game_pk"] for leg in agreement_legs} == {1}


def test_raw_model_policy_uses_model_favorite_side() -> None:
    row = _shadow_row(4, model_p=0.44, market_p=0.50, home_american=120, away_american=-140)
    legs = filter_eligible_legs([row], source_policy=SourcePolicy.RAW_MODEL)
    assert len(legs) == 1
    assert legs[0]["side"] == "AWAY"
    assert legs[0]["american"] == -140


def test_top_edge_policy_limits_to_highest_abs_edge() -> None:
    rows = [
        _shadow_row(1, model_p=0.56, market_p=0.50),
        _shadow_row(2, model_p=0.70, market_p=0.50),
        _shadow_row(3, model_p=0.62, market_p=0.50),
    ]
    legs = filter_eligible_legs(rows, source_policy=SourcePolicy.TOP_EDGE, top_edge_limit=2)
    assert len(legs) == 2
    assert legs[0]["game_pk"] == 2
    assert legs[1]["game_pk"] == 3


def test_rank_legs_balanced_is_deterministic_on_ties() -> None:
    legs = [
        build_parlay_leg(_shadow_row(2), side="HOME"),
        build_parlay_leg(_shadow_row(1), side="HOME"),
    ]
    assert legs[0] is not None and legs[1] is not None
    ranked = rank_legs([legs[0], legs[1]], rank_mode=RankMode.BALANCED)
    assert [leg["game_pk"] for leg in ranked] == [1, 2]


def test_compute_parlay_metrics_for_known_american_lines() -> None:
    leg_a = build_parlay_leg(_shadow_row(1, home_american=-110, away_american=-110), side="HOME")
    leg_b = build_parlay_leg(_shadow_row(2, home_american=150, away_american=-170), side="HOME")
    assert leg_a is not None and leg_b is not None

    metrics = compute_parlay_metrics([leg_a, leg_b], stake=100.0)
    assert metrics["leg_count"] == 2
    assert metrics["combined_decimal"] == leg_a["decimal_odds"] * leg_b["decimal_odds"]
    assert metrics["payout"] == metrics["combined_decimal"] * 100.0
    assert metrics["naive_independence_estimate"] == leg_a["model_p"] * leg_b["model_p"]


def test_suggest_parlay_combinations_returns_top_three_without_duplicate_games() -> None:
    legs = []
    for game_pk in range(1, 6):
        built = build_parlay_leg(_shadow_row(game_pk), side="HOME")
        assert built is not None
        legs.append(built)

    ranked = rank_legs(legs, rank_mode=RankMode.BALANCED)
    suggestions = suggest_parlay_combinations(ranked, leg_count=2, pool_size=5, limit=3)

    assert len(suggestions) == 3
    for suggestion in suggestions:
        game_pks = [leg["game_pk"] for leg in suggestion["legs"]]
        assert len(game_pks) == len(set(game_pks))
        assert suggestion["payout"] == suggestion["combined_decimal"] * 100.0


def test_invalid_odds_rows_are_excluded() -> None:
    row = _shadow_row(9, home_american=0, away_american=110)
    legs = filter_eligible_legs([row], source_policy=SourcePolicy.RAW_MODEL)
    assert legs == []


def test_build_parlay_report_insufficient_legs_status() -> None:
    report = build_parlay_report(
        [_shadow_row(1)],
        source_policy=SourcePolicy.RAW_MODEL,
        leg_count=3,
    )
    assert report["status"] == "insufficient_legs"
    assert report["suggestions"] == []


def test_build_parlay_report_no_predictions_empty_state() -> None:
    report = build_parlay_report([])
    assert report["status"] == "no_predictions"
    assert report["ranked_legs"] == []
