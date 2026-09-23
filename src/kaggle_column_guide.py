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

# Contribution prefix -> label of the PIR component, in the order of CONTRIBUTION_STATS
# (src/build_game_player_stats.py, which holds the signs; a test keeps both in step). A leading "-"
# marks a component that reduces PIR: its share is taken with a minus sign.
CONTRIBUTION_LABELS = {
    "pts": "Points",
    "reb": "Total rebounds",
    "ast": "Assists",
    "stl": "Steals",
    "blk": "Blocks made",
    "fdr": "Fouls drawn",
    "mfg": "-Missed field goals",
    "mft": "-Missed free throws",
    "tov": "-Turnovers",
    "blkag": "-Own shots blocked",
    "pf": "-Fouls committed",
}

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
    "fg_missed": (GAME_DERIVED, "Missed field goals (FGA - FGM)"),
    "two_points_made": (KAGGLE_BOX_SCORE, "Two-pointers made"),
    "two_points_attempted": (KAGGLE_BOX_SCORE, "Two-pointers attempted"),
    "three_points_made": (KAGGLE_BOX_SCORE, "Three-pointers made"),
    "three_points_attempted": (KAGGLE_BOX_SCORE, "Three-pointers attempted"),
    "free_throws_made": (KAGGLE_BOX_SCORE, "Free throws made"),
    "free_throws_attempted": (KAGGLE_BOX_SCORE, "Free throws attempted"),
    "ft_missed": (GAME_DERIVED, "Missed free throws (FTA - FTM)"),
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
    **{
        f"{prefix}_share_of_pir": (GAME_DERIVED, f"{label} / pir this row; blank unless this row's pir > 0")
        for prefix, label in CONTRIBUTION_LABELS.items()
    },
}

GAME_STATS_SHEET = "Game Stats"
KPI_DERIVED = "Player KPIs (derived)"


# Guide order of the spread stats within a KPI group: percentiles low-to-high, range, then sd, cv.
_DISTRIBUTION_TEXTS = {
    "p10": "10th percentile of {what}, season",
    "p50": "Median {what}, season",
    "p90": "90th percentile of {what}, season",
    "range": "{base}_p90 - {base}_p10",
    "sd": "Std dev of {what}; blank under 2 games",
    "cv": "{base}_sd / mean of {what}; blank under 2 games or if mean <= 0",
}


def _distribution_guide(
    base: str, what: str, stats: tuple[str, ...] = tuple(_DISTRIBUTION_TEXTS)
) -> dict[str, tuple[str, str]]:
    """Guide entries `{base}_{stat}` of the standard spread stats of one per-game series.

    Args:
        base: KPI name prefix, e.g. "minutes".
        what: The per-game series in words, e.g. "game minutes".
        stats: The spread stats to document, in order; a subset when the rest have older hand-written texts.
    """
    return {f"{base}_{stat}": (KPI_DERIVED, _DISTRIBUTION_TEXTS[stat].format(base=base, what=what)) for stat in stats}


def _contribution_guide() -> dict[str, tuple[str, str]]:
    """Guide entries of the per-component contribution KPIs: `pct`, its percentiles and range, `std`, `cv`."""
    guide: dict[str, tuple[str, str]] = {}
    for prefix in CONTRIBUTION_LABELS:
        share, base = f"{prefix}_share_of_pir", f"{prefix}_contribution"
        guide[f"{base}_pct"] = (KPI_DERIVED, f"Mean {share} x 100; the 11 contribution_pct values sum to 100")
        guide[f"{base}_p10"] = (KPI_DERIVED, f"10th percentile of {share} x 100, season")
        guide[f"{base}_p50"] = (KPI_DERIVED, f"Median {share} x 100, season")
        guide[f"{base}_p90"] = (KPI_DERIVED, f"90th percentile of {share} x 100, season")
        guide[f"{base}_range"] = (KPI_DERIVED, f"{base}_p90 - {base}_p10")
        guide[f"{base}_std"] = (KPI_DERIVED, f"Std dev of {share} (unscaled); blank under 2 games")
        guide[f"{base}_cv"] = (KPI_DERIVED, f"{base}_std / |mean {share}|; blank under 2 games or if mean is 0")
    return guide


def player_kpis_guide(recent_games: int) -> dict[str, tuple[str, str]]:
    """Column -> (source dataset, explanation) for the `Player KPIs` sheet, in sheet order.

    Columns are grouped by base metric (PIR, PIR/min, minutes, one group per contribution
    component, fouls drawn rate, shooting, usage), identity columns first. Within a group,
    aggregates follow one sequence: recent-then-season average, trend/median, percentiles
    (p10, p50, p90, range), spread (sd, cv). Every per-game series carries the full set
    (average, sd, cv, p10, p50, p90, range); see `DISTRIBUTION_FAMILIES` in
    src/build_player_kpis.py. See the `player-kpis` skill for the convention a future column
    addition should follow.

    Args:
        recent_games: Size of the recent window (games played), quoted in the texts.

    Returns:
        The ordered guide; the KPI builder takes its column order from these keys.
    """
    n = recent_games
    return {
        # Identity / context.
        "player_id": (GAME_STATS_SHEET, "Kaggle player id"),
        "player_name_raw": (GAME_STATS_SHEET, "Raw name, LAST, FIRST in capitals"),
        "player_name": (KPI_DERIVED, "Cleaned First Last name (suffix kept, e.g. Wade Baldwin IV)"),
        "team_id": (KPI_DERIVED, "Most recent team code (latest game, DNP included)"),
        "games_played": (KPI_DERIVED, "Games played (minutes > 0); all KPIs use only these"),
        "games_dnp": (KPI_DERIVED, "Games listed with DNP (did not play)"),
        "dnp_rate": (KPI_DERIVED, "DNP games / (games played + DNP games)"),
        "recent_games": (KPI_DERIVED, f"Games used by the recent window: min({n}, games played)"),
        # PIR.
        "pir_avg_recent": (KPI_DERIVED, f"Mean PIR over the last {n} games played (expected PIR)"),
        "pir_avg": (KPI_DERIVED, "Mean PIR per game played, season"),
        "pir_median_recent": (KPI_DERIVED, f"Median PIR over the last {n} games played"),
        "pir_p10": (KPI_DERIVED, "10th percentile of game PIR, season (floor)"),
        "pir_p50": (KPI_DERIVED, "Median game PIR, season (typical outcome)"),
        "pir_p90": (KPI_DERIVED, "90th percentile of game PIR, season (ceiling)"),
        "pir_range": (KPI_DERIVED, "pir_p90 - pir_p10 (outcome spread)"),
        **_distribution_guide("pir", "game PIR", ("sd", "cv")),
        # PIR / minute.
        "pir_per_min": (KPI_DERIVED, "Season total PIR / season total minutes"),
        **_distribution_guide("pir_per_min", "per-game PIR/min", ("p10", "p50", "p90", "range")),
        "pir_per_min_sd": (KPI_DERIVED, "Std dev of per-game PIR/min; blank under 2 games"),
        "pir_per_min_cv": (KPI_DERIVED, "PIR/min std dev / mean of per-game PIR/min; blank if mean <= 0"),
        # Minutes.
        "minutes_avg_recent": (KPI_DERIVED, f"Mean minutes over the last {n} games played"),
        "minutes_avg": (KPI_DERIVED, "Mean minutes per game played, season"),
        "minutes_trend": (KPI_DERIVED, "Recent mean minutes minus season mean minutes (role change)"),
        **_distribution_guide("minutes", "game minutes", ("p10", "p50", "p90", "range")),
        "minutes_sd": (KPI_DERIVED, "Std dev of game minutes; blank under 2 games"),
        "minutes_cv": (KPI_DERIVED, "Minutes std dev / mean minutes; blank under 2 games"),
        "starts_rate": (KPI_DERIVED, "Starts (is_starter = 1) / games played, season"),
        # Contribution to PIR, one group per component (mean and spread of {prefix}_share_of_pir over
        # the games with pir > 0); the 11 means sum to 100.
        **_contribution_guide(),
        # Fouls drawn rate.
        "fdr_rate": (KPI_DERIVED, "Season fouls drawn / season minutes"),
        **_distribution_guide("fdr_rate", "per-game fouls drawn / minute"),
        # Shooting.
        "fg_pct": (KPI_DERIVED, "Season FGM / FGA"),
        "fg3_pct": (KPI_DERIVED, "Season three-pointers made / attempted"),
        "ft_pct": (KPI_DERIVED, "Season FTM / FTA"),
        "ts_pct": (KPI_DERIVED, "Season points / (2 x (FGA + 0.44 x FTA))"),
        # Usage.
        "usage_proxy_avg": (KPI_DERIVED, "Mean per game played of FGA + 0.44 x FTA + TO + 0.5 x AST"),
        **_distribution_guide("usage_proxy", "game usage proxy"),
        "usage_per_min": (KPI_DERIVED, "Season usage proxy total / season minutes"),
        **_distribution_guide("usage_per_min", "per-game usage proxy / minute"),
    }
