import json

import pytest

from ingestion.mlb.game_detail import GAME_DETAIL_FIELDS, _validate_payload


def test_filtered_game_detail_request_excludes_play_and_pitch_level_fields() -> None:
    fields = set(GAME_DETAIL_FIELDS.split(","))

    assert {"gamePk", "probablePitchers", "boxscore", "pitchers", "pitching"} <= fields
    assert "plays" not in fields
    assert "playEvents" not in fields
    assert "pitchData" not in fields


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
