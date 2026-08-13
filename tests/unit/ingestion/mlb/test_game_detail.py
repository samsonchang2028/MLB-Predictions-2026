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
    # DATA-016: the true MLB ``fields=`` invariant, corrected from the version
    # that locked the P0 bug in place.
    #
    # MLB applies ``fields=`` as an allowlist at EVERY nesting level. Two real
    # API semantics govern the boxscore ``players`` subtree, which is keyed by
    # dynamic ids (e.g. "ID660271"):
    #   1. Listing ``players`` itself EMPTIES the subtree, so ``players`` (and
    #      its dynamic id keys) must stay UNLISTED. The ID-keyed entries still
    #      survive because their listed descendants match.
    #   2. A stat LEAF is returned only when it is explicitly listed. The old
    #      projection listed the identity leaves (``id``/``fullName``) but NOT
    #      the pitching leaves. Proof from the hollow certified build: ``person``
    #      came back POPULATED (its id/fullName were listed) while ``stats`` came
    #      back EMPTY ``{}`` (it was omitted) — 132,848 rows, 100% NULL stats.
    fields = set(GAME_DETAIL_FIELDS.split(","))

    # ``players`` (and its dynamic ID keys) must NOT be listed.
    assert "players" not in fields

    # The stat container + the exact pitching leaves FEAT-002/FEAT-003 consume
    # MUST be listed, or the API returns ``stats: {}`` for every player.
    required_stat_leaves = {
        "stats", "pitching", "inningsPitched", "outs", "battersFaced",
        "numberOfPitches", "hits", "runs", "earnedRuns", "baseOnBalls",
        "strikeOuts", "homeRuns",
    }
    assert required_stat_leaves <= fields


def test_payload_parser_retains_exact_json_and_validates_game_pk() -> None:
    payload = b'{"gamePk":123,"liveData":{}}\n'
    postponed = {
        "abstract_game_state": "Final",
        "detailed_state": "Postponed",
        "coded_game_state": "D",
    }

    assert _validate_payload(payload, 123, lifecycle=postponed) == payload.decode()

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


# --- Hollow-payload guard (DATA-016) ---------------------------------------

def _full_pitching() -> dict[str, object]:
    return {
        "inningsPitched": "6.0", "outs": 18, "battersFaced": 25,
        "numberOfPitches": 97, "hits": 6, "runs": 3, "earnedRuns": 3,
        "baseOnBalls": 3, "strikeOuts": 3, "homeRuns": 1,
    }


def _completed_game(pitching: object, *, pitchers: list[int] | None = None) -> bytes:
    pitchers = [10] if pitchers is None else pitchers
    home_players = {
        "ID10": {"person": {"id": 10, "fullName": "Starter"}, "stats": {"pitching": pitching}},
    }
    away_players = {
        "ID20": {"person": {"id": 20, "fullName": "Starter"}, "stats": {"pitching": pitching}},
    }
    return json.dumps(
        {
            "gamePk": 123,
            "liveData": {"boxscore": {"teams": {
                "home": {"team": {"id": 1}, "pitchers": pitchers, "players": home_players},
                "away": {
                    "team": {"id": 2},
                    "pitchers": [] if not pitchers else [20],
                    "players": away_players,
                },
            }}},
        }
    ).encode()


def test_completed_game_with_real_pitching_line_is_accepted() -> None:
    payload = _completed_game(_full_pitching())

    assert _validate_payload(payload, 123) == payload.decode()


def test_empty_stats_object_is_rejected_as_hollow_payload() -> None:
    # This is the exact P0 shape: ``person`` present, ``stats``/``pitching`` empty.
    payload = _completed_game({})

    with pytest.raises(ValueError, match="hollow boxscore payload"):
        _validate_payload(payload, 123)


def test_missing_required_pitching_stat_is_rejected() -> None:
    pitching = _full_pitching()
    del pitching["earnedRuns"]
    payload = _completed_game(pitching)

    with pytest.raises(ValueError, match="missing required pitching stats.*earnedRuns"):
        _validate_payload(payload, 123)


def test_missing_number_of_pitches_is_rejected() -> None:
    pitching = _full_pitching()
    del pitching["numberOfPitches"]

    with pytest.raises(
        ValueError, match="missing required pitching stats.*numberOfPitches"
    ):
        _validate_payload(_completed_game(pitching), 123)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("inningsPitched", 6.0, r"inningsPitched must use N\.\[012\] text"),
        ("inningsPitched", "6.3", r"inningsPitched must use N\.\[012\] text"),
        ("outs", True, "non-negative integers.*outs"),
        ("battersFaced", "25", "non-negative integers.*battersFaced"),
        ("numberOfPitches", -1, "non-negative integers.*numberOfPitches"),
        ("earnedRuns", -1, "non-negative integers.*earnedRuns"),
        ("hits", 1.5, "non-negative integers.*hits"),
        ("baseOnBalls", False, "non-negative integers.*baseOnBalls"),
        ("strikeOuts", -2, "non-negative integers.*strikeOuts"),
    ],
)
def test_malformed_required_pitching_stat_is_rejected(
    field: str, value: object, message: str
) -> None:
    pitching = _full_pitching()
    pitching[field] = value

    with pytest.raises(ValueError, match=message):
        _validate_payload(_completed_game(pitching), 123)


def test_completed_side_with_only_all_zero_pitching_lines_is_rejected() -> None:
    pitching = {key: (0 if key != "inningsPitched" else "0.0") for key in _full_pitching()}
    payload = _completed_game(pitching)

    with pytest.raises(ValueError, match="no pitcher with recorded activity"):
        _validate_payload(payload, 123)


def test_inactive_listed_pitcher_with_real_sibling_is_accepted() -> None:
    # DATA-019: real MLB boxscores can list an inactive pitcher in the pitchers
    # array with a complete 0 IP / 0 BF / 0 pitch line. That is not the DATA-016
    # hollow projection shape when another pitcher on the same side has activity.
    zero = {key: (0 if key != "inningsPitched" else "0.0") for key in _full_pitching()}
    real = _full_pitching()
    payload = json.dumps(
        {
            "gamePk": 123,
            "liveData": {"boxscore": {"teams": {
                "home": {
                    "team": {"id": 1},
                    "pitchers": [10, 11],
                    "players": {
                        "ID10": {"person": {"id": 10, "fullName": "Inactive"}, "stats": {"pitching": zero}},
                        "ID11": {"person": {"id": 11, "fullName": "Starter"}, "stats": {"pitching": real}},
                    },
                },
                "away": {
                    "team": {"id": 2},
                    "pitchers": [20],
                    "players": {
                        "ID20": {"person": {"id": 20, "fullName": "Starter"}, "stats": {"pitching": real}},
                    },
                },
            }}},
        }
    ).encode()

    assert _validate_payload(payload, 123) == payload.decode()


def test_game_without_boxscore_pitchers_is_accepted() -> None:
    # Postponed / cancelled / not-yet-played games carry empty ``pitchers``
    # arrays and legitimately have no line; they must not be flagged as hollow.
    payload = _completed_game(_full_pitching(), pitchers=[])

    lifecycle = {
        "abstract_game_state": "Final",
        "detailed_state": "Cancelled",
        "coded_game_state": "C",
    }
    assert _validate_payload(payload, 123, lifecycle=lifecycle) == payload.decode()


@pytest.mark.parametrize(
    "response",
    [
        {"gamePk": 123},
        {"gamePk": 123, "liveData": {}},
        {"gamePk": 123, "liveData": {"boxscore": {}}},
        {"gamePk": 123, "liveData": {"boxscore": {"teams": {}}}},
        {
            "gamePk": 123,
            "liveData": {"boxscore": {"teams": {
                "home": {"team": {"id": 1}, "pitchers": [], "players": {}},
                "away": {"team": {"id": 2}, "pitchers": [], "players": {}},
            }}},
        },
    ],
)
def test_completed_game_missing_boxscore_structure_is_rejected(response: dict) -> None:
    with pytest.raises(ValueError, match="completed game.*hollow boxscore payload"):
        _validate_payload(json.dumps(response).encode(), 123)


@pytest.mark.parametrize(
    ("coded", "detail"),
    [("C", "Cancelled"), ("D", "Postponed"), ("U", "Suspended"), ("S", "Scheduled")],
)
def test_nonplayed_lifecycle_may_legitimately_omit_boxscore(
    coded: str, detail: str
) -> None:
    payload = b'{"gamePk":123}'
    lifecycle = {
        "abstract_game_state": "Final" if coded != "S" else "Preview",
        "detailed_state": detail,
        "coded_game_state": coded,
    }

    assert _validate_payload(payload, 123, lifecycle=lifecycle) == payload.decode()


# --- Repair-loop-2 tester exploratory cases (DATA-016) ----------------------

def test_partial_hollow_missing_player_entry_for_one_listed_pitcher_is_rejected() -> None:
    # A completed game where one side's ``pitchers`` list has a real entry AND
    # an id with NO corresponding ``players`` entry at all (not merely an empty
    # stats object) must still be rejected -- the good sibling pitcher does not
    # mask the missing one.
    pitching = _full_pitching()
    payload = json.dumps(
        {
            "gamePk": 123,
            "liveData": {"boxscore": {"teams": {
                "home": {
                    "team": {"id": 1},
                    "pitchers": [10, 11],
                    "players": {
                        "ID10": {"person": {"id": 10, "fullName": "A"}, "stats": {"pitching": pitching}},
                        # ID11 is entirely absent from players -- not merely empty.
                    },
                },
                "away": {
                    "team": {"id": 2},
                    "pitchers": [20],
                    "players": {
                        "ID20": {"person": {"id": 20, "fullName": "B"}, "stats": {"pitching": pitching}},
                    },
                },
            }}},
        }
    ).encode()

    with pytest.raises(ValueError, match="player identity for pitcher 11"):
        _validate_payload(payload, 123)


@pytest.mark.parametrize(
    ("innings", "accepted"),
    [
        ("6.0", True),
        ("0.0", True),
        ("15.2", True),
        ("6", False),  # bare integer -- MLB never emits this format
        ("6.10", False),  # two-digit fractional part
        ("-1.0", False),  # negative
        ("6.00", False),  # extra digit after the valid single fractional char
    ],
)
def test_innings_pitched_format_matrix(innings: str, accepted: bool) -> None:
    pitching = _full_pitching()
    pitching["inningsPitched"] = innings
    payload = _completed_game(pitching)

    if accepted:
        assert _validate_payload(payload, 123) == payload.decode()
    else:
        with pytest.raises(ValueError, match=r"inningsPitched must use N\.\[012\] text"):
            _validate_payload(payload, 123)


def test_duplicate_pitcher_id_across_home_and_away_is_not_flagged() -> None:
    # KNOWN GAP (informational, not a merge blocker): pitcher-id uniqueness is
    # only enforced WITHIN a side (``seen`` resets per side). A real MLB person
    # id cannot legitimately pitch for both teams in one game, so a same-id
    # collision across home/away can only come from a corrupted/malformed
    # payload -- a different failure mode than the DATA-016 hollow-stats defect
    # this task targets. Locking in current (accepting) behavior so a future
    # change to widen the check is a deliberate, visible diff.
    pitching = _full_pitching()
    payload = json.dumps(
        {
            "gamePk": 123,
            "liveData": {"boxscore": {"teams": {
                "home": {
                    "team": {"id": 1}, "pitchers": [5],
                    "players": {"ID5": {"person": {"id": 5, "fullName": "A"}, "stats": {"pitching": pitching}}},
                },
                "away": {
                    "team": {"id": 2}, "pitchers": [5],
                    "players": {"ID5": {"person": {"id": 5, "fullName": "A"}, "stats": {"pitching": pitching}}},
                },
            }}},
        }
    ).encode()

    assert _validate_payload(payload, 123) == payload.decode()


def test_noncompleted_lifecycle_with_malformed_stats_present_is_still_validated() -> None:
    # A Postponed-by-schedule game whose detail feed nonetheless carries a
    # non-empty ``pitchers`` list (e.g. suspended-then-resumed partial data)
    # must still have its stats validated -- the lifecycle guard only excuses
    # a genuinely EMPTY/missing pitchers list, not malformed values once real
    # pitcher entries are present.
    pitching = _full_pitching()
    pitching["earnedRuns"] = -5
    payload = json.dumps(
        {
            "gamePk": 123,
            "liveData": {"boxscore": {"teams": {
                "home": {
                    "team": {"id": 1}, "pitchers": [10],
                    "players": {"ID10": {"person": {"id": 10, "fullName": "A"}, "stats": {"pitching": pitching}}},
                },
                "away": {"team": {"id": 2}, "pitchers": [], "players": {}},
            }}},
        }
    ).encode()
    postponed = {
        "abstract_game_state": "Final",
        "detailed_state": "Postponed",
        "coded_game_state": "D",
    }

    with pytest.raises(ValueError, match="non-negative integers.*earnedRuns"):
        _validate_payload(payload, 123, lifecycle=postponed)
