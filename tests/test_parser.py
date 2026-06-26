"""Unit tests — TxLINE event parser."""
import pytest
from txline.parser import parse_score_event, parse_odds_event, GamePhase


def _score_payload(phase: int, home: int, away: int, event_type: str = "") -> dict:
    return {
        "fixtureId": "12345",
        "competitionId": "wc2026",
        "homeTeam": "France",
        "awayTeam": "Argentina",
        "gamePhase": phase,
        "stats": {"1": {"home": home, "away": away}},
        "minute": 72,
        "messageId": "msg_001",
        **({"scoreChange": True} if event_type == "goal" else {}),
    }


def test_parse_goal_event():
    data = _score_payload(GamePhase.SECOND_HALF, 2, 1, "goal")
    event = parse_score_event(data)
    assert event is not None
    assert event.event_type == "goal"
    assert event.home_score == 2
    assert event.away_score == 1
    assert event.result == "home"


def test_parse_halftime_event():
    data = _score_payload(GamePhase.HALFTIME, 1, 0)
    event = parse_score_event(data)
    assert event.event_type == "half_time"
    assert event.game_phase == GamePhase.HALFTIME


def test_parse_fulltime_event():
    data = _score_payload(GamePhase.ENDED, 1, 1)
    event = parse_score_event(data)
    assert event.event_type == "full_time"
    assert event.is_ended is True
    assert event.result == "draw"


def test_parse_odds_event():
    data = {
        "fixtureId": "12345",
        "messageId": "odds_001",
        "markets": [{"type": "match_winner", "outcomes": [
            {"type": "home", "price": 1.85},
            {"type": "draw", "price": 3.40},
            {"type": "away", "price": 2.10},
        ]}],
    }
    event = parse_odds_event(data)
    assert event is not None
    assert event.home_odds == pytest.approx(1.85)
    assert event.draw_odds == pytest.approx(3.40)
    assert event.away_odds == pytest.approx(2.10)


def test_parse_invalid_score_returns_none():
    assert parse_score_event({}) is None


def test_parse_invalid_odds_no_match_winner():
    data = {"fixtureId": "x", "messageId": "y", "markets": [{"type": "other", "outcomes": []}]}
    assert parse_odds_event(data) is None
