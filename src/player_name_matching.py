"""Name normalisation and cross-source player matching helpers.

The four EuroLeague sources spell the same player differently ("Wade Baldwin IV"
vs "Wade Baldwin", "Sasha" vs "Aleksandr" Vezenkov, "Maccabi Rapyd Tel Aviv" vs
"Maccabi Tel Aviv"). These helpers reduce names to comparable keys and resolve
rows of one source onto the player keys of another, never merging two different
players.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

import pandas as pd

GENERATIONAL_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
TEAM_ALIASES = {"milano": "milan"}
MIN_SHARED_TEAM_TOKENS = 3
FIRST_NAME_MIN_RATIO = 0.8
NICKNAME_GROUPS = [
    frozenset({"aleksandr", "aleksander", "alexander", "alexandros", "alex", "sasha", "sasa"}),
    frozenset({"nathan", "nate"}),
    frozenset({"nikolaos", "nikos", "nick", "nicholas"}),
    frozenset({"dimitrios", "dimitris", "dimitri"}),
    frozenset({"konstantinos", "kostas", "costas", "konstantin"}),
    frozenset({"ioannis", "giannis", "yannis", "john"}),
    frozenset({"georgios", "giorgos", "george"}),
    frozenset({"vasileios", "vassilis", "vasilis"}),
    frozenset({"michael", "mike", "michail"}),
    frozenset({"william", "will", "bill"}),
]
FULL_NAME_MIN_RATIO = 0.85


def normalize_name(name: object) -> str:
    """Lowercase and strip accents/punctuation, keeping only a-z and single spaces.

    Hyphens become spaces ("Jean-Charles" -> "jean charles"); apostrophes and
    dots are dropped ("O'Neale" -> "oneale", "Jr." -> "jr").

    Args:
        name: Raw name; non-strings (e.g. NaN) normalise to an empty string.

    Returns:
        The normalised name.
    """
    if not isinstance(name, str):
        return ""
    spaced = re.sub(r"[-\u2010-\u2015]", " ", name)  # hyphen, en/em dashes: word separators
    ascii_name = unicodedata.normalize("NFKD", spaced).encode("ascii", "ignore").decode().lower()
    ascii_name = re.sub(r"[^a-z ]", "", ascii_name)
    return re.sub(r"\s+", " ", ascii_name).strip()


def name_key(name: object) -> str:
    """Normalise a full name and drop trailing generational suffixes.

    A suffix is only dropped when at least one other token remains.

    Args:
        name: Raw full name.

    Returns:
        Normalised name without jr/sr/ii/iii/iv/v ("Wade Baldwin IV" -> "wade baldwin").
    """
    tokens = normalize_name(name).split()
    while len(tokens) > 1 and tokens[-1] in GENERATIONAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def surname_key(key: str) -> str:
    """Return the surname of a suffix-free name key (its last token)."""
    return key.split()[-1] if key else ""


def team_tokens(team: object) -> frozenset[str]:
    """Return the set of normalised tokens of a team/club name (with aliases applied)."""
    return frozenset(TEAM_ALIASES.get(t, t) for t in normalize_name(team).split())


def teams_compatible(team_a: object, team_b: object) -> bool:
    """Whether two team names denote the same club.

    True when one name's tokens are a subset of the other's ("Virtus Bologna" vs
    "Virtus Segafredo Bologna"), or when both start with the same word and share
    at least three tokens, which absorbs sponsor changes ("Maccabi Playtika Tel
    Aviv" vs "Maccabi Rapyd Tel Aviv"). "Hapoel Tel Aviv" vs "Maccabi Playtika Tel
    Aviv" is not compatible. Missing names never match.

    Args:
        team_a: First team name.
        team_b: Second team name.

    Returns:
        True if the teams are compatible.
    """
    tokens_a, tokens_b = team_tokens(team_a), team_tokens(team_b)
    if not tokens_a or not tokens_b:
        return False
    if tokens_a <= tokens_b or tokens_b <= tokens_a:
        return True
    same_first_word = normalize_name(team_a).split()[0] == normalize_name(team_b).split()[0]
    return same_first_word and len(tokens_a & tokens_b) >= MIN_SHARED_TEAM_TOKENS


def first_names_compatible(key_a: str, key_b: str) -> bool:
    """Whether the first names of two name keys look like spellings of one name.

    True for identical names, prefixes of at least three letters ("Zac"/"Zachary"),
    near-identical spellings, and members of a known nickname group ("Sasha"/"Aleksandr").
    """
    first_a, first_b = key_a.split()[0], key_b.split()[0]
    if first_a == first_b or any(first_a in group and first_b in group for group in NICKNAME_GROUPS):
        return True
    shorter = min(len(first_a), len(first_b))
    if shorter >= 3 and (first_a.startswith(first_b) or first_b.startswith(first_a)):
        return True
    return SequenceMatcher(None, first_a, first_b).ratio() >= FIRST_NAME_MIN_RATIO


def resolve_names(
    base: pd.DataFrame, other: pd.DataFrame, other_team_col: str, other_games_col: str | None = None
) -> pd.Series:
    """Map each row of `other` onto a `base` name key where it is safely the same player.

    Rows whose `name_key` already exists in `base` keep it (exact match, which also
    claims that base row). Remaining rows are tried against the still-unclaimed base
    rows with progressively looser rules, each applied only when it yields exactly
    one candidate:

    1. same surname (suffix excluded) and compatible first name (team narrows the
       candidates when several remain; this also covers players traded between sources);
    2. same team and near-identical full name (covers spelling variants);
    3. same surname, compatible team and identical games played, for nicknames no
       name rule can link ("Iffe" vs "Gabriel" Lundberg). Only used when
       `other_games_col` is given, since a surname and team alone would also merge
       teammates who merely share a surname.

    If two `other` rows resolve onto the same base row, both resolutions are reverted
    so two different players are never merged.

    Args:
        base: Frame with `name_key` and `team_name` columns (plus `games_played`
            when `other_games_col` is used).
        other: Frame with a `name_key` column and the team column named below.
        other_team_col: Name of the team/club column in `other`.
        other_games_col: Optional games-played column in `other`, enabling rule 3.

    Returns:
        Series aligned to `other.index` with the resolved name keys.
    """
    result = other["name_key"].copy()
    claimed = set(other["name_key"]) & set(base["name_key"])
    pool = base[~base["name_key"].isin(claimed)]
    pool_keys = pool["name_key"].tolist()
    pool_surnames = [surname_key(k) for k in pool_keys]
    pool_teams = pool["team_name"].tolist()
    pool_games = pool["games_played"].tolist() if other_games_col else []

    resolved: dict[object, str] = {}
    for idx in other.index[~other["name_key"].isin(claimed)]:
        key = other.at[idx, "name_key"]
        team = other.at[idx, other_team_col]
        same_surname = [i for i, s in enumerate(pool_surnames) if s == surname_key(key)]
        same_team = [i for i, t in enumerate(pool_teams) if teams_compatible(t, team)]

        by_name = [i for i in same_surname if first_names_compatible(key, pool_keys[i])]
        by_name_and_team = [i for i in by_name if i in same_team]
        by_spelling = [i for i in same_team if SequenceMatcher(None, key, pool_keys[i]).ratio() >= FULL_NAME_MIN_RATIO]
        by_games = (
            [i for i in same_surname if i in same_team and pool_games[i] == other.at[idx, other_games_col]]
            if other_games_col
            else []
        )
        for candidates in (by_name, by_name_and_team, by_spelling, by_games):
            if len(candidates) == 1:
                resolved[idx] = pool_keys[candidates[0]]
                break

    proposals = pd.Series(resolved, dtype=object)
    collisions = proposals.duplicated(keep=False)
    for idx, match in proposals[~collisions].items():
        result.at[idx] = match
    return result
