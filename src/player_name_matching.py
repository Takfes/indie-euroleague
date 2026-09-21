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
ROMAN_SUFFIXES = {"ii", "iii", "iv", "v"}
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


def kaggle_display_name(raw: str) -> str:
    """Convert a Kaggle `LAST, FIRST` name in capitals to "First Last" in title case.

    Suffixes stay attached to the surname and roman numerals stay upper-case, so
    "BALDWIN IV, WADE" becomes "Wade Baldwin IV". Multi-word surnames, particles and
    hyphens are kept ("DE COLO, NANDO" -> "Nando De Colo"); `name_key` normalises the
    result like every other source, so the suffix is ignored when matching.

    Args:
        raw: Kaggle player name, `LAST, FIRST` (a name without a comma is only title-cased).

    Returns:
        The cleaned display name.
    """
    last, _, first = raw.partition(",")
    tokens = [_title_token(token) for token in [*first.split(), *last.split()]]
    return " ".join(tokens)


def _title_token(token: str) -> str:
    """Title-case one name token, keeping roman-numeral generational suffixes upper-case."""
    if token.strip(".").lower() in ROMAN_SUFFIXES:
        return token.upper()
    return token.title()


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


def first_names_equivalent(key_a: str, key_b: str) -> bool:
    """Whether the first names are identical or members of one nickname group ("Sasha"/"Aleksandr")."""
    first_a, first_b = key_a.split()[0], key_b.split()[0]
    return first_a == first_b or any(first_a in group and first_b in group for group in NICKNAME_GROUPS)


def first_names_compatible(key_a: str, key_b: str) -> bool:
    """Whether the first names of two name keys look like spellings of one name.

    True for equivalent names (see `first_names_equivalent`), prefixes of at least
    three letters ("Zac"/"Zachary") and near-identical spellings.
    """
    if first_names_equivalent(key_a, key_b):
        return True
    first_a, first_b = key_a.split()[0], key_b.split()[0]
    shorter = min(len(first_a), len(first_b))
    if shorter >= 3 and (first_a.startswith(first_b) or first_b.startswith(first_a)):
        return True
    return SequenceMatcher(None, first_a, first_b).ratio() >= FIRST_NAME_MIN_RATIO


def surname_extends(key_a: str, key_b: str) -> bool:
    """Whether one surname is the other's start, e.g. "hayes" vs "hayes davis" (compound surname).

    Only the tokens after the first name are compared; identical surnames do not count.
    """
    rest_a, rest_b = key_a.split()[1:], key_b.split()[1:]
    shorter, longer = sorted((rest_a, rest_b), key=len)
    return bool(shorter) and shorter != longer and longer[: len(shorter)] == shorter


def map_alternate_spellings(base: pd.DataFrame, other: pd.DataFrame, spelling_cols: list[str]) -> pd.Series:
    """Map `other` rows spelled like another source's spelling of a `base` row onto that row's key.

    The master's `name_key` follows the first source a player came through, so a later
    source may spell the player exactly like a different source did ("Iffe" Lundberg is
    "Gabriel" in basketnews). Only unambiguous spellings are used (one base row), and if
    two `other` rows land on the same base row both stay unmapped.

    Args:
        base: Frame with a `name_key` column and the raw-name columns in `spelling_cols`.
        other: Frame with a `name_key` column.
        spelling_cols: Raw full-name columns of `base` (e.g. Dunkest, basketnews, price names).

    Returns:
        Series aligned to `other.index` with the mapped name keys (unchanged where no rule applied).
    """
    aliases: dict[str, set[str]] = {}
    for column in spelling_cols:
        for key, own in zip(base[column].map(name_key), base["name_key"], strict=True):
            if key and key != own:
                aliases.setdefault(key, set()).add(own)
    own_keys = set(base["name_key"])
    proposals = {
        idx: next(iter(aliases[key]))
        for idx, key in other["name_key"].items()
        if key not in own_keys and len(aliases.get(key, ())) == 1
    }
    targets = pd.Series(proposals, dtype=object)
    result = other["name_key"].copy()
    for idx, target in targets[~targets.duplicated(keep=False)].items():
        result.at[idx] = target
    return result


def resolve_names(
    base: pd.DataFrame,
    other: pd.DataFrame,
    other_team_col: str,
    other_games_col: str | None = None,
    extend_surnames: bool = False,
) -> pd.Series:
    """Map each row of `other` onto a `base` name key where it is safely the same player.

    Rows whose `name_key` already exists in `base` keep it (exact match, which also
    claims that base row). Remaining rows are tried against the still-unclaimed base
    rows with progressively looser rules, each applied only when it yields exactly
    one candidate:

    1. same surname (suffix excluded) and an equivalent first name (identical or a
       nickname group), or a compatible first name (prefix, near-identical spelling)
       on a compatible team; team narrows the candidates when several remain;
    2. same team and near-identical full name (covers spelling variants);
    3. same surname, compatible team and identical games played, for nicknames no
       name rule can link ("Iffe" vs "Gabriel" Lundberg). Only used when
       `other_games_col` is given, since a surname and team alone would also merge
       teammates who merely share a surname.

    4. same team, an equivalent or compatible first name and a surname that is the
       other's start ("Nigel Hayes" vs "Nigel Hayes-Davis"). Only used when
       `extend_surnames` is True.

    If two `other` rows resolve onto the same base row, both resolutions are reverted
    so two different players are never merged.

    Args:
        base: Frame with `name_key` and `team_name` columns (plus `games_played`
            when `other_games_col` is used).
        other: Frame with a `name_key` column and the team column named below.
        other_team_col: Name of the team/club column in `other`.
        other_games_col: Optional games-played column in `other`, enabling rule 3.
        extend_surnames: Enable rule 4.

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

        # A loose first-name match (prefix/similar spelling) is only trusted on the same
        # team; across teams (traded players) the first name must be equivalent.
        by_name = [
            i
            for i in same_surname
            if first_names_equivalent(key, pool_keys[i])
            or (i in same_team and first_names_compatible(key, pool_keys[i]))
        ]
        by_name_and_team = [i for i in by_name if i in same_team]
        by_spelling = [i for i in same_team if SequenceMatcher(None, key, pool_keys[i]).ratio() >= FULL_NAME_MIN_RATIO]
        by_games = (
            [i for i in same_surname if i in same_team and pool_games[i] == other.at[idx, other_games_col]]
            if other_games_col
            else []
        )
        by_surname_extension = (
            [i for i in same_team if surname_extends(key, pool_keys[i]) and first_names_compatible(key, pool_keys[i])]
            if extend_surnames
            else []
        )
        for candidates in (by_name, by_name_and_team, by_spelling, by_games, by_surname_extension):
            if len(candidates) == 1:
                resolved[idx] = pool_keys[candidates[0]]
                break

    proposals = pd.Series(resolved, dtype=object)
    collisions = proposals.duplicated(keep=False)
    for idx, match in proposals[~collisions].items():
        result.at[idx] = match
    return result
