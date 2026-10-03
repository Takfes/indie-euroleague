"""Shared plumbing of the EuroLeague live-API fetchers (`fetch_euroleague_live_boxscores.py`, `fetch_euroleague_live_headers.py`).

Library module (no I/O header, never run directly): endpoint URLs, the polite-client constants, the JSON
fetchers and the ingestion state file both fetchers read and write. Scripts import it instead of each
other, so DVC and the registry lint see one shared dependency.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any

HEADER_URL = "https://live.euroleague.net/api/Header"
BOXSCORE_URL = "https://live.euroleague.net/api/BoxScore"

# The live API 403s urllib's default User-Agent ("Python-urllib/x.y"); any other
# value, including this one, passes -- verified empirically against the real API.
USER_AGENT = "Mozilla/5.0 (compatible; euroleague-live-fetcher/1.0)"

# Polite-client delay between sequential requests.
REQUEST_DELAY_SECONDS = 0.3

DEFAULT_OUT_DIR = Path(__file__).resolve().parents[3] / "data" / "raw_data" / "euroleague_net"


def fetch_json(url: str) -> dict[str, Any] | None:
    """Fetch a URL and parse it as JSON.

    Returns None when the response body is empty -- the live API's way of
    saying a gamecode doesn't have a game (yet).
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        body = response.read()
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def fetch_header(season: str, gamecode: int) -> dict[str, Any] | None:
    """Fetch the Header endpoint for one game."""
    return fetch_json(f"{HEADER_URL}?gamecode={gamecode}&seasoncode={season}")


def fetch_boxscore(season: str, gamecode: int) -> dict[str, Any] | None:
    """Fetch the BoxScore endpoint for one game."""
    return fetch_json(f"{BOXSCORE_URL}?gamecode={gamecode}&seasoncode={season}")


def load_state(state_path: Path) -> dict[str, dict[str, Any]]:
    """Load the ingestion state file, or an empty dict if it doesn't exist yet."""
    if not state_path.exists():
        return {}
    return json.loads(state_path.read_text(encoding="utf-8"))


def save_state(state: dict[str, dict[str, Any]], state_path: Path) -> None:
    """Write the ingestion state file, creating parent dirs as needed."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
