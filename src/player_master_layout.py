"""Column layout of the player Master sheet: which columns are dropped, and the order of the rest.

`build_master_table` (src/build_player_master_table.py) applies the three levers below in this
order: `DUPLICATE_COLUMNS` are dropped, the remaining columns are put in `order_master_columns`
order, and `EXCLUDED_COLUMNS` are dropped last. The layout lists every column that can exist,
including the excluded ones at their natural place, so removing an entry from `EXCLUDED_COLUMNS`
is all it takes to bring a column back. A column that the layout does not know raises, so a new
source column has to be placed here on purpose.

Order of the sheet:
  1. `LEADING_COLUMNS`: identity, provenance, minutes, price, PIR, value and price-projection columns.
  2. `CARRY_OVER_COLUMNS`: the rest of the price projection, `season`, and id / sample-size columns.
  3. Raw stats and metrics: production and availability, offense, defense, then team and lineup
     level (`dunk_plus_minus`, all `bnoo_*`). Within a stat family the sub-order is one pattern:
     per-game attempts, season-total attempts, per-game makes, season-total makes, then the
     percentage / rate (a count family: the count, then its percentage / rate).
  4. The 11 signed PIR contribution shares (`kag_*_contribution_pct`, they sum to 100).
  5. The Kaggle distribution KPIs: per family the average and its recent-window siblings, then
     sd, cv, p10, p50, p90, range; families whose average or sd already lead the sheet only add
     the remainder. The 11 contribution families come last (std, cv, p10, p50, p90, range each).
"""

from __future__ import annotations

from collections.abc import Sequence

from build_game_player_stats import CONTRIBUTION_STATS
from build_player_kpis import DISTRIBUTION_FAMILIES

# Left out of the final Master sheet only: the columns are still computed and used inside the
# build (e.g. `kag_games_played`, `dunk_slug` and `player_id` feed the join provenance check). This
# list is applied as the very last step of column assembly and is intentionally easy to undo:
# remove an entry and the column reappears at its place in the layout below.
EXCLUDED_COLUMNS = [
    "price_rank",
    "dunk_cr",
    "dunk_min",
    "dunk_slug",
    "player_id",
    "bnadv_points",
    "kag_player_id",
    "kag_player_name_raw",
    "kag_player_name",
    "kag_team_id",
    "kag_games_played",
    "kag_recent_games",
]

# Dropped column -> the kept column that measures the same quantity. Decided per group on value
# agreement (direct comparison and correlation across the players both sources have), agreement
# with the Kaggle box-score means, precision and completeness; the excluded `bnadv_points` (kept
# `dunk_pts`) and `dunk_min` (kept `kag_minutes_avg`) belong to the same family of decisions.
DUPLICATE_COLUMNS = {
    # Same BN team rating on court, scraped twice: identical in every row. The on/off total view
    # is kept (its description is verified, the BN advanced one is not).
    "bnadv_offensive_rating_lineup": "bnoo_offensive_rating_lineup_tot",
    "bnadv_defensive_rating_lineup": "bnoo_defensive_rating_lineup_tot",
    # Per-game counts: Dunkest and BN advanced agree to the 1-decimal rounding (corr >= 0.995,
    # >= 92% of rows identical) and both match the Kaggle box-score means; Dunkest is the primary
    # stats source and keeps each shooting / rebounding family complete (attempts + makes).
    "bnadv_3p_attempted": "dunk_tpa",
    "bnadv_ft_attempted": "dunk_fta",
    "bnadv_assists": "dunk_ast",
    "bnadv_turnovers": "dunk_tov",
    "bnadv_offensive_rebounds": "dunk_oreb",
    "bnadv_defensive_rebounds": "dunk_dreb",
    "bnadv_steals": "dunk_stl",
    "bnadv_blocks": "dunk_blk",
    "bnadv_blocks_received": "dunk_blka",
    "bnadv_fouls_received": "dunk_fouls_received",
    # Shooting percentages: three-way (Dunkest 1 decimal and 0.0 for no attempts, BN whole numbers)
    # against Kaggle, which is exact (season totals of the box score) and blank without attempts.
    # The Kaggle ones are fractions (0-1), the others 0-100.
    "dunk_fgp": "kag_fg_pct",
    "dunk_tpp": "kag_fg3_pct",
    "bnadv_3p_percentage": "kag_fg3_pct",
    "dunk_ftp": "kag_ft_pct",
    "bnadv_ft_percentage": "kag_ft_pct",
    "bnadv_ts_percentage": "kag_ts_pct",
}

LEADING_COLUMNS = [
    "player_name",
    "team_name_hist",
    "team_name_current",
    "canonical_team_name",
    "position",
    "found_in",
    "found_in_count",
    "games_played",
    "kag_minutes_avg",
    "kag_minutes_pct",
    "price",
    "kag_pir_avg",
    "kag_pir_sd",
    "kag_pir_per_min",
    "kag_pir_per_min_sd",
    "pir_per_credit",
    "pir_per_min_per_credit",
    "expected_pir",
    "breakeven_pir",
    "expected_price_change",
    "expected_price_next_round",
]
CARRY_OVER_COLUMNS = [
    "capital_yield_pct",
    "price_rank",
    "season",
    "player_id",
    "dunk_slug",
    "kag_player_id",
    "kag_player_name_raw",
    "kag_player_name",
    "kag_team_id",
    "kag_games_played",
    "kag_recent_games",
]
_PRODUCTION_AVAILABILITY = [
    "dunk_pdk",
    "dunk_cr",
    "dunk_min",
    "dunk_starter",
    "kag_starts_rate",
    "kag_games_dnp",
    "kag_dnp_rate",
]
_OFFENSE = [
    "dunk_pts",
    "bnadv_points",
    # Field goals, two-pointers, three-pointers, free throws.
    "dunk_fga",
    "dunk_fga_tot",
    "dunk_fgm",
    "dunk_fgm_tot",
    "kag_fg_pct",
    "bnadv_2p_attempted",
    "bnadv_2p_percentage",
    "dunk_tpa",
    "dunk_tpa_tot",
    "dunk_tpm",
    "dunk_tpm_tot",
    "kag_fg3_pct",
    "bnadv_3p_attempted_rate",
    "dunk_fta",
    "dunk_fta_tot",
    "dunk_ftm",
    "dunk_ftm_tot",
    "kag_ft_pct",
    "kag_ts_pct",
    "bnadv_offensive_rating_ind",
    # Playmaking, offensive rebounds, ball security, usage, fouls drawn, own shots blocked.
    "dunk_ast",
    "bnadv_assist_percentage",
    "bnadv_created_points",
    "dunk_oreb",
    "bnadv_offensive_rebound_percentage",
    "dunk_tov",
    "bnadv_turnover_percentage",
    "bnadv_usage_percentage",
    "dunk_fouls_received",
    "dunk_blka",
]
_DEFENSE = [
    # Rebounds (total, then defensive), steals, blocks, fouls committed, stops, individual rating.
    "dunk_reb",
    "dunk_dreb",
    "bnadv_defensive_rebound_percentage",
    "dunk_stl",
    "bnadv_steal_percentage",
    "dunk_blk",
    "bnadv_block_percentage",
    "dunk_pf",
    "bnadv_stops",
    "bnadv_stop_percentage",
    "bnadv_foul_stops",
    "bnadv_defensive_rating_ind",
]
_TEAM_LINEUP_LEAD = ["dunk_plus_minus"]  # the `bnoo_*` columns follow, in their source order
_CONTRIBUTION_PCT = [f"kag_{prefix}_contribution_pct" for prefix in CONTRIBUTION_STATS]

_SPREAD_STATS = ("sd", "cv", "p10", "p50", "p90", "range")
# Family -> the average column and its siblings that are not spread stats, ahead of the spread stats.
_FAMILY_HEADS = {
    "pir": ["kag_pir_avg", "kag_pir_avg_recent", "kag_pir_median_recent"],
    "pir_per_min": ["kag_pir_per_min"],
    "minutes": ["kag_minutes_avg", "kag_minutes_avg_recent", "kag_minutes_trend"],
    "usage_proxy": ["kag_usage_proxy_avg"],
    "usage_per_min": ["kag_usage_per_min"],
    "fdr_rate": ["kag_fdr_rate"],
}


def _distribution_columns() -> list[str]:
    """The Kaggle distribution KPIs not already in `LEADING_COLUMNS`, in layout order."""
    if set(_FAMILY_HEADS) != set(DISTRIBUTION_FAMILIES):
        raise ValueError(
            f"_FAMILY_HEADS {sorted(_FAMILY_HEADS)} and DISTRIBUTION_FAMILIES {sorted(DISTRIBUTION_FAMILIES)} "
            "must name the same families: place the new family's columns in src/player_master_layout.py."
        )
    columns = []
    for family in DISTRIBUTION_FAMILIES:
        columns += _FAMILY_HEADS[family] + [f"kag_{family}_{stat}" for stat in _SPREAD_STATS]
    for prefix in CONTRIBUTION_STATS:
        columns += [f"kag_{prefix}_contribution_{stat}" for stat in ("std", "cv", "p10", "p50", "p90", "range")]
    leading = set(LEADING_COLUMNS)
    return [column for column in columns if column not in leading]


def order_master_columns(columns: Sequence[str]) -> list[str]:
    """The Master column order for the given columns (see the module docstring).

    Args:
        columns: The columns of the assembled Master, after `DUPLICATE_COLUMNS` were dropped and
            before `EXCLUDED_COLUMNS` are.

    Returns:
        The same columns in sheet order; the `bnoo_*` columns keep their incoming (source) order.

    Raises:
        ValueError: If the layout names a column that is not in `columns`, or `columns` holds one
            the layout does not place (say which, and where to add it).
    """
    onoff = [column for column in columns if column.startswith("bnoo_")]
    ordered = [
        *LEADING_COLUMNS,
        *CARRY_OVER_COLUMNS,
        *_PRODUCTION_AVAILABILITY,
        *_OFFENSE,
        *_DEFENSE,
        *_TEAM_LINEUP_LEAD,
        *onoff,
        *_CONTRIBUTION_PCT,
        *_distribution_columns(),
    ]
    have, placed = set(columns), set(ordered)
    missing = [column for column in ordered if column not in have]
    unplaced = [column for column in columns if column not in placed]
    repeated = sorted({column for column in ordered if ordered.count(column) > 1})
    if missing or unplaced or repeated:
        raise ValueError(
            f"Master columns and the layout disagree. In the layout but not in the Master: {missing}. "
            f"In the Master but not in the layout: {unplaced}. Placed twice in the layout: {repeated}. "
            "Add, remove or move them in src/player_master_layout.py."
        )
    return ordered
