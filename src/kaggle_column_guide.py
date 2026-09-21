"""Column explanations for the two workbooks built from the Kaggle box score.

`GAME_STATS_GUIDE` documents `data/curated/player_game_stats.xlsx` (column -> (source
dataset, explanation)); `PLAYER_KPIS_GUIDE` documents `data/curated/player_kpis.xlsx`
(same shape). The player master workbook reuses the KPI texts under its "Kaggle KPIs"
source label. Entries marked "(unverified)" could not be confirmed from the data.
"""

from __future__ import annotations

KAGGLE_BOX_SCORE = "Kaggle box score"
KAGGLE_HEADER = "Kaggle header"
GAME_DERIVED = "Game Stats (derived)"
GAME_STATS_SOURCE_DATASETS = (KAGGLE_BOX_SCORE, KAGGLE_HEADER, GAME_DERIVED)

GAME_STATS_GUIDE: dict[str, tuple[str, str]] = {
    "game_id": (KAGGLE_BOX_SCORE, "Game id, e.g. E2025_028"),
    "game": (KAGGLE_BOX_SCORE, "Game label, first-listed team code first"),
    "season_code": (KAGGLE_BOX_SCORE, "Season code (E2025 = 2025-26)"),
    "phase": (KAGGLE_HEADER, "REGULAR SEASON, PLAY-IN, PLAYOFFS or FINAL FOUR"),
    "round": (KAGGLE_HEADER, "Round number"),
    "date": (KAGGLE_HEADER, "Game date, YYYY-MM-DD text"),
    "time": (KAGGLE_HEADER, "Tip-off time, HH:MM:SS text"),
    "team_id": (KAGGLE_BOX_SCORE, "Team code of the player in this game"),
    "team_name": (KAGGLE_HEADER, "Team name of the player in this game"),
    "opponent_id": (KAGGLE_HEADER, "Opponent team code"),
    "opponent_name": (KAGGLE_HEADER, "Opponent team name"),
    "home_away": (KAGGLE_HEADER, "home = first-listed team, away = second (venue unverified, e.g. Final Four)"),
    "player_id": (KAGGLE_BOX_SCORE, "Kaggle player id (P+digits or letter codes)"),
    "player": (KAGGLE_BOX_SCORE, "Raw name, LAST, FIRST in capitals"),
    "is_starter": (KAGGLE_BOX_SCORE, "1 if in the starting five, else 0"),
    "played": (GAME_DERIVED, "True if minutes > 0; False = DNP (did not play)"),
    "game_number": (GAME_DERIVED, "Player's chronological game counter, games played only"),
    "minutes": (KAGGLE_BOX_SCORE, "Minutes played as decimal number; 0 for DNP"),
    "points": (KAGGLE_BOX_SCORE, "Points scored"),
    "fgm": (GAME_DERIVED, "Field goals made (two + three pointers)"),
    "fga": (GAME_DERIVED, "Field goals attempted (two + three pointers)"),
    "two_points_made": (KAGGLE_BOX_SCORE, "Two-pointers made"),
    "two_points_attempted": (KAGGLE_BOX_SCORE, "Two-pointers attempted"),
    "three_points_made": (KAGGLE_BOX_SCORE, "Three-pointers made"),
    "three_points_attempted": (KAGGLE_BOX_SCORE, "Three-pointers attempted"),
    "free_throws_made": (KAGGLE_BOX_SCORE, "Free throws made"),
    "free_throws_attempted": (KAGGLE_BOX_SCORE, "Free throws attempted"),
    "offensive_rebounds": (KAGGLE_BOX_SCORE, "Offensive rebounds"),
    "defensive_rebounds": (KAGGLE_BOX_SCORE, "Defensive rebounds"),
    "total_rebounds": (KAGGLE_BOX_SCORE, "Total rebounds"),
    "assists": (KAGGLE_BOX_SCORE, "Assists"),
    "steals": (KAGGLE_BOX_SCORE, "Steals"),
    "turnovers": (KAGGLE_BOX_SCORE, "Turnovers"),
    "blocks_favour": (KAGGLE_BOX_SCORE, "Blocks made (shots rejected)"),
    "blocks_against": (KAGGLE_BOX_SCORE, "Own shots blocked by opponents"),
    "fouls_committed": (KAGGLE_BOX_SCORE, "Fouls committed"),
    "fouls_received": (KAGGLE_BOX_SCORE, "Fouls drawn (FDR)"),
    "valuation": (KAGGLE_BOX_SCORE, "Official PIR (Performance Index Rating)"),
    "plus_minus": (KAGGLE_BOX_SCORE, "Team point margin while on court"),
    "pir": (GAME_DERIVED, "PIR from the box score; equals valuation on played rows"),
    "pir_per_min": (GAME_DERIVED, "PIR / minutes"),
    "usage_proxy": (GAME_DERIVED, "FGA + 0.44 x FTA + TO + 0.5 x AST"),
    "usage_per_min": (GAME_DERIVED, "Usage proxy / minutes"),
    "fdr_per_min": (GAME_DERIVED, "Fouls drawn / minutes"),
    "fg_pct": (GAME_DERIVED, "FGM / FGA; blank if no attempts"),
    "ft_pct": (GAME_DERIVED, "FTM / FTA; blank if no attempts"),
    "ts_pct": (GAME_DERIVED, "True shooting: points / (2 x (FGA + 0.44 x FTA)); blank if no attempts"),
}
