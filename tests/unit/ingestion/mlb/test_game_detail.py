import json

import pytest

from ingestion.mlb.game_detail import GAME_DETAIL_FIELDS, _validate_payload


def test_filtered_game_detail_request_excludes_play_and_pitch_level_fields() -> None:
    fields = set(GAME_DETAIL_FIELDS.split(","))

    assert {"gamePk", "probablePitchers", "boxscore", "pitchers"} <= fields
    assert "plays" not in fields
    assert "playEvents" not in fields
    assert "pitchData" not in fields


def test_players_subtree_is_not_field_filtered() -> None:
    # The boxscore ``players`` object is keyed by dynamic ids (e.g. "ID660271"),
    # so it cannot be whitelisted; listing it (or its stat sub-keys) makes the
    # live feed return ``players`` empty and breaks silver stat extraction. It
    # must be left out of the filter so the full players subtree survives.
    fields = set(GAME_DETAIL_FIELDS.split(","))

    for players_subtree_key in (
        "players", "person", "stats", "pitching", "inningsPitched",
        "earnedRuns", "hits", "numberOfPitches",
    ):
        assert players_subtree_key not in fields


def test_payload_parser_retains_exact_json_and_validates_game_pk() -> None:
    payload = b'{"gamePk":123,"liveData":{}}\n'

    assert _validate_payload(payload, 123) == payload.decode()

    with pytest.raises(ValueError, match="does not match requested 124"):
        _validate_payload(payload, 124)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("not-bytes", "exact bytes or None"),
        (b"not-json", "not valid JSON"),
        (json.dumps([]).encode(), "must be an object"),
        (b'{"message":"not found"}', "does not match requested"),
    ],
)
def test_invalid_game_detail_envelopes_are_rejected(
    payload: object, message: str
) -> None:
    with pytest.raises((TypeError, ValueError), match=message):
        _validate_payload(payload, 123)
