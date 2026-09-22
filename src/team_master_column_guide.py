"""Column explanations for the `Column Guide` sheet of the team master workbook.

One short explanation per column of each source dataset, based on the fetch scripts
and SKILL.md files of `basketnews-team-stats` and `dunkest-defense-positions`, the
player guide (`player_master_column_guide.py`) and standard basketball-analytics
definitions. Entries marked "(unverified)" could not be confirmed from those sources.
Keys are the column names exactly as they appear in each source CSV.
"""

from __future__ import annotations

BN_TEAM = "BN Team Stats"
DUNKEST_DVP = "Dunkest Defense vs Position"

# Master column prefix per source dataset - the single definition used by the builder and the guide.
SOURCE_PREFIXES = {BN_TEAM: "bnteam_", DUNKEST_DVP: "dunkdvp_"}

UNDOCUMENTED = "(undocumented: new column, add to team_master_column_guide.py)"

# Basketnews blocks are named `<offense|defense>_<all|home|away>_<kpi>`; the split is spelled out once here.
BN_SPLITS = {"all": "all games", "home": "home games", "away": "away games"}

# Team's own numbers (the page's OFFENSE table), per game unless stated.
_BN_OFFENSE_KPIS = {
    "offensive_rating": "Points scored per 100 possessions",
    "points": "Points scored per game",
    "3p_percentage": "Three-point %",
    "2p_percentage": "Two-point %",
    "ft_percentage": "Free throw %",
    "ts_percentage": "True shooting %",
    "3p_attempted": "Three-pointers attempted per game",
    "2p_attempted": "Two-pointers attempted per game",
    "ft_attempted": "Free throws attempted per game",
    "3p_attempted_rate": "Share of field goal attempts from three",
    "assists": "Assists per game",
    "assist_percentage": "Assist % (formula unverified)",
    "turnovers": "Turnovers per game",
    "turnover_percentage": "Turnovers per 100 plays",
    "steals_opponent": "Steals by opponents per game (listed under offense)",
    "offensive_rebounds": "Offensive rebounds per game",
    "offensive_rebound_percentage": "Share of available offensive rebounds grabbed",
    "fouls_received": "Fouls drawn per game",
    "blocks_received": "Own shots blocked by opponents per game",
    "possessions": "Possessions per game",
}

# The page's DEFENSE table: what the team concedes (opponent side) plus its own defensive actions.
_BN_DEFENSE_KPIS = {
    "defensive_rating": "Points conceded per 100 possessions",
    "points_opponent": "Points conceded per game",
    "3p_percentage_opponent": "Opponents' three-point %",
    "2p_percentage_opponent": "Opponents' two-point %",
    "ft_percentage_opponent": "Opponents' free throw %",
    "ts_percentage_opponent": "Opponents' true shooting %",
    "3p_attempted_opponent": "Opponents' three-pointers attempted per game",
    "2p_attempted_opponent": "Opponents' two-pointers attempted per game",
    "ft_attempted_opponent": "Opponents' free throws attempted per game",
    "3p_attempted_rate_opponent": "Opponents' share of field goal attempts from three",
    "assists_opponent": "Opponents' assists per game",
    "assist_percentage_opponent": "Opponents' assist % (formula unverified)",
    "turnovers_opponent": "Opponents' turnovers per game (forced)",
    "turnover_percentage_opponent": "Opponents' turnovers per 100 plays",
    "steals": "Steals per game",
    "defensive_rebounds": "Defensive rebounds per game",
    "defensive_rebound_percentage": "Share of available defensive rebounds grabbed",
    "fouls": "Fouls committed per game",
    "blocks": "Blocks per game",
    "possessions_opponent": "Opponents' possessions per game",
}

_BN_IDENTITY_GUIDE = {
    "team_id": f"Basketnews team id (Master: {SOURCE_PREFIXES[BN_TEAM]}team_id)",
    "team_name": "Team name (Master: team_name)",
    "team_short_name": "Team short name (Master: team_short_name)",
    "season": "Season start year, 2025 = 2025-26 (Master: season)",
    "league_id": "Basketnews league id (25 = EuroLeague)",
    "games_played_all": "Games played, all games",
    "games_played_home": "Games played, home games",
    "games_played_away": "Games played, away games",
}


def _bn_team_guide() -> dict[str, str]:
    """Explain every Basketnews team column; the block/split prefix is spelled out in each text."""
    guide = dict(_BN_IDENTITY_GUIDE)
    for side, kpis in (("offense", _BN_OFFENSE_KPIS), ("defense", _BN_DEFENSE_KPIS)):
        for split, split_text in BN_SPLITS.items():
            for kpi, text in kpis.items():
                guide[f"{side}_{split}_{kpi}"] = f"{text}, {split_text}"
    return guide


# Dunkest columns are named `<position>_<stat>`; values are per-game amounts the team concedes to that position.
DVP_POSITIONS = ("guards", "forwards", "centers")
_DVP_STATS = {
    "points": "Points",
    "rebounds": "Rebounds",
    "assists": "Assists",
    "steals": "Steals",
    "blocks": "Blocks",
    "turnovers": "Turnovers",
    "3point_field_goals_made": "Three-pointers made",
    "fantasy_points": "Fantasy points",
}

_DVP_IDENTITY_GUIDE = {
    "team_id": f"Dunkest team id (Master: {SOURCE_PREFIXES[DUNKEST_DVP]}team_id)",
    "team_name": "Dunkest spelling; Master uses the Basketnews name",
    "season": "Season label, e.g. 2025-26 (Master: Basketnews season)",
}


def _dunkest_dvp_guide() -> dict[str, str]:
    """Explain every Dunkest column: the stat conceded per game to that position."""
    guide = dict(_DVP_IDENTITY_GUIDE)
    for position in DVP_POSITIONS:
        for stat, text in _DVP_STATS.items():
            guide[f"{position}_{stat}"] = f"{text} conceded per game to {position}"
    return guide


TEAM_KPIS = "Team KPIs"


def team_kpis_guide() -> dict[str, tuple[str, str]]:
    """Column -> (source dataset, explanation) for the `Team KPIs` sheet, in sheet order.

    `foul_rate_per40_*` normalization: a EuroLeague game is 40 regulation minutes (4 x
    10-minute quarters). The raw Basketnews fouls columns are already per-game averages, and
    per-team overtime minutes are not available anywhere in the raw data, so under the
    assumption that every game is exactly 40 minutes (overtime not modeled), a per-game rate
    already equals a per-40-minutes rate. These columns restate that per-game rate under this
    explicit, documented assumption rather than leaving the unit implicit.
    """
    return {
        "team_name": (TEAM_KPIS, "Team name (Basketnews spelling); joins into the team master table"),
        "pace_factor": (
            TEAM_KPIS,
            "offense_all_possessions (Basketnews team stats, per-game pace) / the 20-team league average; "
            "1.0 = league-average pace, >1 = faster than average",
        ),
        "foul_rate_per40_drawn": (
            TEAM_KPIS,
            "offense_all_fouls_received (Basketnews team stats) per 40 minutes of team play; already a "
            "per-game average and a game is 40 regulation minutes, so under a fixed 40-minutes-per-game "
            "assumption (overtime not modeled, no per-game minutes data exists) this equals the raw "
            "per-game rate",
        ),
        "foul_rate_per40_committed": (
            TEAM_KPIS,
            "defense_all_fouls (Basketnews team stats, fouls the team itself commits) per 40 minutes of "
            "team play; same 40-minutes-per-game assumption as foul_rate_per40_drawn",
        ),
        "funnel_ratio_guards": (
            TEAM_KPIS,
            "guards_fantasy_points (Dunkest defense vs position, conceded per game) / the 20-team league "
            "average for guards; 1.0 = league-average funnel to guards, >1 = concedes more than average",
        ),
        "funnel_ratio_forwards": (
            TEAM_KPIS,
            "forwards_fantasy_points (Dunkest defense vs position, conceded per game) / the 20-team league "
            "average for forwards",
        ),
        "funnel_ratio_centers": (
            TEAM_KPIS,
            "centers_fantasy_points (Dunkest defense vs position, conceded per game) / the 20-team league "
            "average for centers",
        ),
    }


TEAM_KPI_COLUMNS = [column for column in team_kpis_guide() if column != "team_name"]

GUIDE: dict[str, dict[str, str]] = {
    BN_TEAM: _bn_team_guide(),
    DUNKEST_DVP: _dunkest_dvp_guide(),
    TEAM_KPIS: {column: text for column, (_, text) in team_kpis_guide().items()},
}
