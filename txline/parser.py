"""Parse raw TxLINE SSE events into typed dataclasses."""
from __future__ import annotations
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional


class GamePhase(IntEnum):
    NOT_STARTED = 1
    FIRST_HALF = 2
    HALFTIME = 3
    SECOND_HALF = 4
    ENDED = 5
    ENDED_EXTRA_TIME = 10
    ENDED_PENALTIES = 13


# Stat base keys
GOALS = 1
YELLOW_CARDS = 2
RED_CARDS = 3
CORNERS = 4

# Period multipliers: (period * 1000) + base_key
PERIOD_H1 = 1000
PERIOD_H2 = 2000


@dataclass
class ScoreEvent:
    fixture_id: str
    competition_id: str
    home_team: str
    away_team: str
    home_score: int
    away_score: int
    game_phase: int
    minute: Optional[int]
    event_type: str  # "goal", "yellow_card", "red_card", "phase_change"
    event_id: str
    raw: dict

    @property
    def is_ended(self) -> bool:
        return self.game_phase in (5, 10, 13)

    @property
    def result(self) -> str:
        if self.home_score > self.away_score:
            return "home"
        if self.away_score > self.home_score:
            return "away"
        return "draw"

    @property
    def score_display(self) -> str:
        return f"{self.home_score}–{self.away_score}"


@dataclass
class OddsEvent:
    fixture_id: str
    home_odds: float
    away_odds: float
    draw_odds: float
    market: str  # "match_winner"
    event_id: str
    raw: dict
    # Opportunistic — only set if TxLINE's odds payload happens to carry them
    # (unconfirmed against a live feed). None means "unknown", not "no match".
    competition_id: str = ""
    home_team: Optional[str] = None
    away_team: Optional[str] = None

    @property
    def shift_pct(self, prev_home: float) -> float:
        if prev_home == 0:
            return 0.0
        return ((self.home_odds - prev_home) / prev_home) * 100


def parse_score_event(data: dict) -> Optional[ScoreEvent]:
    if not data.get("fixtureId"):
        return None
    try:
        stats = data.get("stats", {})
        home_goals = stats.get(str(GOALS), {}).get("home", 0)
        away_goals = stats.get(str(GOALS), {}).get("away", 0)
        phase = data.get("gamePhase", 1)

        # Determine event type from phase transition or stat change
        event_type = "update"
        if phase in (GamePhase.ENDED, GamePhase.ENDED_EXTRA_TIME, GamePhase.ENDED_PENALTIES):
            event_type = "full_time"
        elif phase == GamePhase.HALFTIME:
            event_type = "half_time"
        elif "scoreChange" in data:
            event_type = "goal"

        return ScoreEvent(
            fixture_id=str(data.get("fixtureId", "")),
            competition_id=str(data.get("competitionId", "")),
            home_team=data.get("homeTeam", "Home"),
            away_team=data.get("awayTeam", "Away"),
            home_score=int(home_goals),
            away_score=int(away_goals),
            game_phase=phase,
            minute=data.get("minute"),
            event_type=event_type,
            event_id=str(data.get("messageId", data.get("updateId", ""))),
            raw=data,
        )
    except (KeyError, TypeError, ValueError):
        return None


def parse_odds_event(data: dict) -> Optional[OddsEvent]:
    try:
        markets = data.get("markets", [])
        mw = next((m for m in markets if m.get("type") == "match_winner"), None)
        if not mw:
            return None
        outcomes = {o["type"]: o["price"] for o in mw.get("outcomes", [])}
        return OddsEvent(
            fixture_id=str(data.get("fixtureId", "")),
            home_odds=float(outcomes.get("home", 0)),
            away_odds=float(outcomes.get("away", 0)),
            draw_odds=float(outcomes.get("draw", 0)),
            market="match_winner",
            event_id=str(data.get("messageId", "")),
            raw=data,
            competition_id=str(data.get("competitionId", "")),
            home_team=data.get("homeTeam"),
            away_team=data.get("awayTeam"),
        )
    except (KeyError, TypeError, ValueError):
        return None
