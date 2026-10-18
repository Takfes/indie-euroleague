from pathlib import Path

import pandas as pd

from eupy.entity.matching import AmbiguousNameError, UnknownNameError
from eupy.entity.player_names import PlayerNames
from eupy.transform.build_eubook import fill_empty

pd.set_option("display.max_columns", None)


def write_excel_workbook(worksheets: dict[str, pd.DataFrame], filepath: str | Path) -> None:
    """Write dataframes to an Excel workbook, using dictionary keys as sheet names."""
    with pd.ExcelWriter(filepath) as writer:
        for worksheet_name, dataframe in worksheets.items():
            dataframe.to_excel(writer, sheet_name=worksheet_name, index=False)


def safe_normalize(name: str, team: str | None) -> str | None:
    try:
        return players.normalize(name, team=team)
    except UnknownNameError:
        return None  # name not in the map
    except AmbiguousNameError:
        return None  # shared spelling and no team given/matching


# from eupy.config import load_config
# config = load_config()
YEAR = 2026
SLOW_WINDOW = 5
FAST_WINDOW = 3

boxscorefile = "/Users/takis/Documents/sckool/indie-euroleague/data/stage_01/euroleague_box_score_merged.csv"

playersfile = "/Users/takis/Documents/sckool/indie-euroleague/data/raw_data/fantasy_stats/players.csv"

schedulefile = "/Users/takis/Documents/sckool/indie-euroleague/data/raw_data/game_schedule/schedule_E2026.csv"

df = pd.read_csv(boxscorefile)
pl = pd.read_csv(playersfile)
sc = pd.read_csv(schedulefile)

NEXT_ROUND = df["round"].max() + 1
FILEPATH = "/Users/takis/Documents/sckool/indie-euroleague/data/stage_99/"
FILENAME = f"eubook_{str(NEXT_ROUND).zfill(3)}.xlsx"
WRITEPATH = Path(FILEPATH, FILENAME)

# ==============================================================================
# boxscore dataset
# ==============================================================================

# create year column
df_year = df["season_code"].str.replace("E", "").astype(int)
# find the index of the "season_code" column
df_year_idx = df.columns.tolist().index("season_code") + 1
# and insert the "year" column after it
df.insert(df_year_idx, "year", df_year)

# filter by year and exclude team stats
df = df[(df["year"] >= YEAR) & (df["dorsal"] != "TOTAL")].reset_index(drop=True).copy()

# create minutes and minutes numeric columns
df["minutes"] = df["minutes"].replace("DNP", "0:00")
df_minutes_num = df["minutes"].str.split(":").apply(lambda x: int(x[0]) + int(x[1]) / 60)
# find the index of the "minutes" column
# and insert the "minutes_num" column after it
df_minutes_num_idx = df.columns.tolist().index("minutes") + 1
df.insert(df_minutes_num_idx, "minutes_num", df_minutes_num)

# create home/away column
df_homeaway = df.apply(lambda x: "home" if x["team_id"] == x["game"].split("-")[0] else "away", axis=1)
# find the index of the "team_id" column
# and insert the "homeaway" column after it
df_homeaway_idx = df.columns.tolist().index("team_id") + 1
df.insert(df_homeaway_idx, "homeaway", df_homeaway)

# create opponent column
df_opponent = df.apply(
    lambda x: x["game"].split("-")[1] if x["team_id"] == x["game"].split("-")[0] else x["game"].split("-")[0], axis=1
)
# find the index of the "team_id" column
# and insert the "opponent" column after it
df_opponent_idx = df.columns.tolist().index("team_id") + 1
df.insert(df_opponent_idx, "opponent", df_opponent)

# normalize player names using the PlayerNames class
players = PlayerNames.load()

# create normalized player column
df_player_normalized = [safe_normalize(p, t) for p, t in zip(df["player"], df["team_id"], strict=False)]
# find the index of the "player" column
# and insert the "player_normalized" column after it
df_player_idx = df.columns.tolist().index("player") + 1
df.insert(df_player_idx, "player_normalized", df_player_normalized)

# ! assert df["player_normalized"].isna().sum() == 0, "Some player names could not be normalized"

# inspect the rows with missing normalized player names
if df["player_normalized"].isna().sum() != 0:
    print(f"there are {df['player_normalized'].isna().sum()} rows unmatched")
    print(df["player"][df["player_normalized"].isna()].drop_duplicates())
    # drop rows with missing normalized player names
    df.dropna(subset=["player_normalized"], inplace=True)

# ==============================================================================
# join playersfile to get position and quotation per round
# ==============================================================================

# create normalized player column
pl_player_normalized = [safe_normalize(p, t) for p, t in zip(pl["name"], pl["team"], strict=False)]
# find the index of the "player" column
# and insert the "player_normalized" column after it
pl_player_idx = pl.columns.tolist().index("name") + 1
pl.insert(pl_player_idx, "player_normalized", pl_player_normalized)

# select only the relevant columns and rename position values
pls = pl[["player_normalized", "position", "quotation", "round"]].assign(
    position=lambda x: x["position"].map({"Guard": "G", "Forward": "F", "Center": "C"})
)

# merge the two datasets
df = df.merge(pls, on=["round", "player_normalized"], how="left")

# ! assert df.isnull().sum().sum() == 0, "null values detected after merging boxscore with stats"

# inspect the rows with missing normalized player names
unmatched_players = df[df["quotation"].isna() | df["position"].isna()]
# if there are any unmatched players - drop them so we can continue without NAs
if not unmatched_players.empty:
    print(f"there are {unmatched_players.shape[0]} players unmatched")
    print(unmatched_players.player)
    # drop rows with missing normalized player names
    df.dropna(subset=["quotation", "position"], inplace=True)

# ==============================================================================
# calculate player kpis
# ==============================================================================

df_kpis = (
    df.sort_values("round", ascending=True)
    .groupby(["player_normalized", "team_id", "position"], as_index=False)
    .agg(
        crd_last=("quotation", "last"),
        pir_mean=("valuation", "mean"),
        pir_last=("valuation", "last"),
        pir_lag1=("valuation", lambda x: x.shift(1).iloc[-1]),
        pir_lag2=("valuation", lambda x: x.shift(2).iloc[-1]),
        pir_std=("valuation", "std"),
        pir_p10=("valuation", lambda x: x.tail(SLOW_WINDOW).quantile(0.10)),
        pir_p50=("valuation", lambda x: x.tail(SLOW_WINDOW).quantile(0.50)),
        pir_p75=("valuation", lambda x: x.tail(SLOW_WINDOW).quantile(0.75)),
        pir_p90=("valuation", lambda x: x.tail(SLOW_WINDOW).quantile(0.90)),
        pir_slow=("valuation", lambda x: x.tail(SLOW_WINDOW).mean()),
        pir_fast=("valuation", lambda x: x.tail(FAST_WINDOW).mean()),
        mins_mean=("minutes_num", "mean"),
        mins_std=("minutes_num", "std"),
        mins_slow=("minutes_num", lambda x: x.tail(SLOW_WINDOW).mean()),
        mins_fast=("minutes_num", lambda x: x.tail(FAST_WINDOW).mean()),
    )
    .assign(
        pir_per_crd=lambda x: x.pir_mean / x.crd_last,
        pir_breakeven=lambda x: (x.pir_mean - x.crd_last * 1.1) / 25,
        pir_per_min=lambda x: x.pir_mean / x.mins_mean,
        pir_trend=lambda x: x.pir_fast / x.pir_slow,
        mins_trend=lambda x: x.mins_fast / x.mins_slow,
    )
).reset_index(drop=True)


# ==============================================================================
# team kpis conceded and collected
# ==============================================================================

# first - base grouping
g1 = (
    df.groupby([
        "game_id",
        "team_id",
        "opponent",
        "position",
    ])
    .agg({"valuation": "mean"})
    .reset_index()
)

# Calculate PIR conceded by position - "group by opponent"
g2 = g1.groupby(["opponent", "position"], as_index=False).agg({"valuation": "mean"})
g2["league_average"] = g2.groupby(["position"])["valuation"].transform("mean")
g2["normd_pstn_pir"] = g2["valuation"] / g2["league_average"]
g2["grade"] = g2.groupby("position")["normd_pstn_pir"].rank(ascending=False, method="min")

# PIR conceded by position - pivot table - rank grade
g2b = (
    g2
    # .assign(grade=lambda df: df.groupby("position")["normd_pstn_pir"].rank(ascending=False, method="min"))
    .pivot(index="opponent", columns="position", values="grade")
    .fillna(0)
    .round(2)
    .sort_index()
    .sort_index(axis=1)
    .rename(columns=lambda x: x + "_conceded_pir")
    .reset_index()
    .rename_axis(columns=None)
    .rename(columns={"opponent": "team"})
)

# Calculate PIR collected by position - "group by team_id"
g3 = g1.groupby(["team_id", "position"], as_index=False).agg({"valuation": "mean"})
g3["league_average"] = g3.groupby(["position"])["valuation"].transform("mean")
g3["normd_pstn_pir"] = g3["valuation"] / g3["league_average"]
g3["grade"] = g3.groupby("position")["normd_pstn_pir"].rank(ascending=True, method="min")

# PIR collected by position - pivot table - rank grade
g3b = (
    g3
    # .assign(grade=lambda df: df.groupby("position")["normd_pstn_pir"].rank(ascending=True, method="min"))
    .pivot(index="team_id", columns="position", values="grade")
    .fillna(0)
    .round(2)
    .sort_index()
    .sort_index(axis=1)
    .rename(columns=lambda x: x + "_collected_pir")
    .reset_index()
    .rename_axis(columns=None)
    .rename(columns={"team_id": "team"})
)

# ==============================================================================
# schedule dataset
# ==============================================================================

sc_round = (
    sc.query("gameday==@NEXT_ROUND")[["gameday", "date", "hometeam", "homecode", "awayteam", "awaycode"]]
    .assign(turn=lambda x: x["date"].rank(method="dense").astype(int))
    .sort_values(["turn", "gameday"])
)

home_games = (
    sc_round[["homecode", "awaycode", "turn"]]
    .rename(columns={"homecode": "team", "awaycode": "opponent"})
    .assign(venue="home")
)
away_games = (
    sc_round[["awaycode", "homecode", "turn"]]
    .rename(columns={"awaycode": "team", "homecode": "opponent"})
    .assign(venue="away")
)
all_games = pd.concat([home_games, away_games], ignore_index=True)

# ==============================================================================
# prepare output datasets : df_kpis
# ==============================================================================

dataframes_to_write = {}

# * schedule & teams datasets --------------------------------------------------

dataframes_to_write["schedule"] = sc

teams = (
    sc[["hometeam", "homecode", "hometv"]]
    .drop_duplicates()
    .rename(columns=lambda x: x.replace("home", ""))
    .sort_values(by="team")
)

dataframes_to_write["teams"] = teams

# * raw game_player_id dataset -------------------------------------------------

df_cols_in_order = [
    "season_code",
    "year",
    "game_player_id",
    "game_id",
    "player_id",
    "phase",
    "round",
    "game",
    "team_id",
    "opponent",
    "homeaway",
    "position",
    "dorsal",
    "player",
    "player_normalized",
    "quotation",
    "valuation",
    "is_starter",
    "is_playing",
    "minutes",
    "minutes_num",
    "points",
    "two_points_made",
    "two_points_attempted",
    "three_points_made",
    "three_points_attempted",
    "free_throws_made",
    "free_throws_attempted",
    "offensive_rebounds",
    "defensive_rebounds",
    "total_rebounds",
    "assists",
    "steals",
    "turnovers",
    "blocks_favour",
    "blocks_against",
    "fouls_committed",
    "fouls_received",
    "plus_minus",
]

df = df[df_cols_in_order].copy()

dataframes_to_write["boxscore"] = df

# * pir per position long ------------------------------------------------------

gl = g1.assign(
    game_id_num=lambda x: x.game_id.str.split("_").str[-1].astype(str),
    game_no=lambda x: x.groupby(["team_id", "position"])["game_id_num"]
    .rank(method="min")
    .astype(int)
    .astype(str)
    .str.zfill(3),
    game_no_opponent=lambda x: x.game_no + "_" + x.opponent,
)

dataframes_to_write["hist_long_pir_per_pos"] = gl

# * collected vs concded pir per position --------------------------------------

g2w = g2.rename(
    columns={
        "opponent": "team",
        "valuation": "pir_conceded_per_position",
        "league_average": "pir_conceded_per_position_league_average",
        "normd_pstn_pir": "normalized_pir_conceded_per_position_league_average",
        "grade": "grade_pir_conceded_per_position",
    }
)

g3w = g3.rename(
    columns={
        "team_id": "team",
        "valuation": "pir_collected_per_position",
        "league_average": "pir_collected_per_position_league_average",
        "normd_pstn_pir": "normalized_pir_collected_per_position_league_average",
        "grade": "grade_pir_collected_per_position",
    }
)

gw = g2w.merge(g3w, how="left", on=["team", "position"])

dataframes_to_write["hist_wide_pir_per_pos"] = gw

# * schedule per pir position --------------------------------------------------

sc_cols_in_order = ["hometeam", "homecode", "awayteam", "awaycode", "turn", "date", "gameday"]

sch_pir_pstn = (
    sc_round[sc_cols_in_order]
    .merge(g2b.rename(columns=lambda x: "home_" + x), how="left", left_on="homecode", right_on="home_team")
    .drop(columns=["home_team"])
    .merge(g2b.rename(columns=lambda x: "away_" + x), how="left", left_on="awaycode", right_on="away_team")
    .drop(columns=["away_team"])
    .merge(g3b.rename(columns=lambda x: "home_" + x), how="left", left_on="homecode", right_on="home_team")
    .drop(columns=["home_team"])
    .merge(g3b.rename(columns=lambda x: "away_" + x), how="left", left_on="awaycode", right_on="away_team")
    .drop(columns=["away_team"])
    .assign(
        C_home_collected_away_conceded=lambda x: x.home_C_collected_pir - x.away_C_conceded_pir,
        F_home_collected_away_conceded=lambda x: x.home_F_collected_pir - x.away_F_conceded_pir,
        G_home_collected_away_conceded=lambda x: x.home_G_collected_pir - x.away_G_conceded_pir,
        C_home_conceded_away_collected=lambda x: x.home_C_conceded_pir - x.away_C_collected_pir,
        F_home_conceded_away_collected=lambda x: x.home_F_conceded_pir - x.away_F_collected_pir,
        G_home_conceded_away_collected=lambda x: x.home_G_conceded_pir - x.away_G_collected_pir,
    )
)

dataframes_to_write["next_round_team_pir_per_pos"] = sch_pir_pstn

# * player analytical record ---------------------------------------------------

par_cols_in_order = [
    "player",
    "team",
    "position",
    "turn",
    "venue",
    "opponent",
    "crd_last",
    "normd_pstn_pir",
    "exp_pir",
    "pir_mean",
    "pir_per_crd",
    "pir_breakeven",
    "pir_per_min",
    "pir_last",
    "pir_lag1",
    "pir_lag2",
    "pir_std",
    "pir_p10",
    "pir_p50",
    "pir_p75",
    "pir_p90",
    "pir_slow",
    "pir_fast",
    "pir_trend",
    "mins_mean",
    "mins_std",
    "mins_slow",
    "mins_fast",
    "mins_trend",
]

par = (
    df_kpis.merge(all_games, left_on=["team_id"], right_on=["team"], how="left")
    .drop(columns=["team"])
    .merge(g2[["opponent", "position", "normd_pstn_pir"]], how="left", on=["opponent", "position"])
    .assign(
        exp_pir=lambda x: x.pir_mean * x.normd_pstn_pir,
    )
    .rename(columns={"player_normalized": "player", "team_id": "team"})
)[par_cols_in_order].pipe(fill_empty)

dataframes_to_write["player_analytical_record"] = par


# ==============================================================================
# write workbook with dataframes to disk
# ==============================================================================

write_excel_workbook(dataframes_to_write, WRITEPATH)
