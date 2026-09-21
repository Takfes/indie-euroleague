#!/usr/bin/env python3
"""Fetch EuroLeague Fantasy player and head coach prices from basketballsphere.com
and write them to CSV.

The site's "EuroLeague Fantasy Player Prices" page looks like it needs per-position
scraping (chips for All players / Guards / Forwards / Centers / Head coaches, a club
dropdown, a name search box, and a "Show More" button that only reveals 25 rows at a
time). It doesn't: all of that is client-side JS/CSS filtering over rows that are
already present in the initial HTML response. There is no XHR/fetch call for the
table data at any point (confirmed by capturing network requests while loading the
page and clicking through the filters) -- a single plain GET of the page URL already
returns every player and every head coach. This script does exactly that: one HTTP
GET, then a regex pass over the embedded `<tr data-pos=... data-club=... data-price=...>`
rows.

Usage:
    python src/fetch_basketballsphere_prices.py [--out PATH]

Re-run this any time to refresh data/euroleague-fantasy/basketballsphere_prices.csv --
prices change after every round, so the output is fully overwritten each run.
"""

from __future__ import annotations

import argparse
import csv
import html
import re
import urllib.request
from pathlib import Path

URL = "https://basketballsphere.com/en/euroleague-fantasy-player-prices/"

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "data" / "euroleague-fantasy" / "basketballsphere_prices.csv"

# Row markup is stable and simple enough to regex directly rather than pulling in an
# HTML-parsing dependency. Each `<tr>` carries the position/club/price as data
# attributes AND repeats them in table cells; the name cell is either a bare string
# or a link to a per-player/per-coach profile page -- the (?:...)? groups handle both.
ROW_RE = re.compile(
    r'<tr data-pos="(?P<pos>[^"]*)" data-club="[^"]*" data-price="[^"]*">'
    r'<td class="n r">(?P<rank>\d+)</td>'
    r'<td class="nm">(?:<a[^>]*>)?(?P<name>[^<]+)(?:</a>)?</td>'
    r'<td class="c">(?P<club>[^<]*)</td>'
    r'<td class="ps">[^<]*</td>'
    r'<td class="n p">(?P<price>[^<]*)</td>'
    r"</tr>"
)


def fetch_html(url: str) -> str:
    """Download the page HTML with a desktop User-Agent (the site 403s bare urllib UAs)."""
    request = urllib.request.Request(  # noqa: S310
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return response.read().decode("utf-8")


def parse_rows(page_html: str) -> list[dict[str, str]]:
    """Extract every player/head-coach row from the page's embedded price table.

    `pos` is `G`/`F`/`C` for outfield players and `HC` for head coaches -- that's the
    only thing distinguishing the two in this single combined table, so it's used to
    derive the `role` column (`player` vs. `head_coach`).
    """
    rows = []
    for match in ROW_RE.finditer(page_html):
        pos = match.group("pos")
        rows.append({
            "rank": match.group("rank"),
            "name": html.unescape(match.group("name")).strip(),
            "club": html.unescape(match.group("club")).strip(),
            "position": pos,
            "price": match.group("price"),
            "role": "head_coach" if pos == "HC" else "player",
        })
    return rows


def write_csv(rows: list[dict[str, str]], out_path: Path) -> None:
    if not rows:
        raise ValueError("No rows parsed -- refusing to overwrite the CSV with an empty file")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["rank", "name", "club", "position", "price", "role"])
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="CSV output path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = parse_rows(fetch_html(URL))
    write_csv(rows, args.out)

    players = sum(1 for r in rows if r["role"] == "player")
    coaches = sum(1 for r in rows if r["role"] == "head_coach")
    print(f"Wrote {len(rows)} rows ({players} players, {coaches} head coaches) to {args.out}")


if __name__ == "__main__":
    main()
