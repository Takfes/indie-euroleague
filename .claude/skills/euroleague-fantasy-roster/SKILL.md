---
name: euroleague-fantasy-roster
description: |
  Capture EuroLeague Fantasy Challenge player roster data from a team page.
  Use this whenever the user wants to extract player statistics and pricing information
  from the EuroLeague Fantasy Challenge website. The skill automates visiting the team
  roster page, extracting visible player data (name, position, average fantasy points,
  popularity percentage, and price), and saving it as a CSV file.
compatibility: Browser automation tools (Claude in Chrome)
---

# EuroLeague Fantasy Roster Data Capture

## Purpose

Extract player roster data from an EuroLeague Fantasy Challenge team page and save it as a CSV file with columns: name, position, avg_fpt, pop, price.

## Setup

### Required Information

Before starting, you'll need:
- The team page URL: `https://euroleaguefantasy.euroleaguebasketball.net/10/fantasy/{team_id}/team`
- Browser automation tools (Claude in Chrome) with network request monitoring capability

### Create Output Directory

```bash
mkdir -p data/euroleague-fantasy
```

## Procedure

### 1. Load Browser Tools

Load the Chrome browser automation tools via ToolSearch in one batch call:
- mcp__claude-in-chrome__tabs_context_mcp
- mcp__claude-in-chrome__navigate
- mcp__claude-in-chrome__computer
- mcp__claude-in-chrome__read_page
- mcp__claude-in-chrome__tabs_create_mcp
- mcp__claude-in-chrome__tabs_close_mcp
- mcp__claude-in-chrome__read_network_requests

### 2. Navigate to Team Page

Use tabs_context_mcp with createIfEmpty:true to create a new tab, then navigate to the team page URL. If there's a cookie consent dialog, dismiss it by clicking "Allow All".

### 3. Check for API Endpoint (Optional)

Use read_network_requests to look for API calls to `fantaking-api.dunkest.com`. The endpoint typically looks like:
```
https://fantaking-api.dunkest.com/api/v1/players-lists/{id}/matchdays/{matchday}/players?per_page=-1&page=1
```

**Note:** This API endpoint requires authentication and is not directly accessible. The UI scroll approach is the reliable method.

### 4. Capture Visible Player Data

The right-hand side of the page contains a player list panel. Manually read and extract the visible player information:
- **Name** — Player's full name (e.g., "VEZENKOV SASHA")
- **Position** — Single letter position code (G, F, C, or HC)
- **Avg FPT** — Average Fantasy Points (usually shown as "AVG FPT" label)
- **Pop** — Popularity percentage (shown as "POP XX.X%")
- **Price** — Player price in credits (rightmost value in box)

Use screenshot captures to read data clearly. For each visible player row, extract all five fields.

### 5. Handle Scrolling Limitation

**Current Status:** The Flutter-based web app does not respond to standard scroll interactions. If the list does not scroll:
- Try scrolling at different coordinates within the player list area
- Use arrow keys or Page Down (though these may trigger unwanted interactions)
- Document any scroll attempts and the number of visible players captured

If unable to scroll, capture all visible players in the current viewport and note this limitation in your report.

### 6. Save Data as CSV

Create a CSV file in `data/euroleague-fantasy/` with the header:
```
name,position,avg_fpt,pop,price
```

For each player, add a row with values in the same order. Ensure:
- Names are in UPPERCASE (or preserve original case as displayed)
- Position codes are single letters (G, F, C, HC)
- avg_fpt is numeric (can be 0 or decimal)
- pop is numeric without the % symbol (e.g., "37.4" not "37.4%")
- price is numeric (can be decimal like 15.5)

**Example:**
```csv
name,position,avg_fpt,pop,price
VEZENKOV SASHA,F,0,37.4,17
JAMES MIKE,G,0,2.3,16
```

### 7. Verify Data Quality

- Check row count matches players captured
- Spot-check a few rows against the live page screenshots
- Ensure all required columns are present
- Validate numeric fields contain valid numbers

## Troubleshooting

### Cookie Dialog Won't Dismiss

If pressing "Allow All" doesn't work, try:
- Pressing Escape key
- Clicking the X button on the dialog
- Waiting longer for the page to fully load

### Players List Not Visible

- Ensure you're on the Players tab (not Schedule, Lineups, or Stats)
- Wait for the loading spinner to complete
- Try clicking on the "Players" tab button to refresh

### Data Extraction Difficulties

If player information is hard to read from screenshots:
- Take higher-resolution screenshots
- Zoom into the player list area using the zoom action
- Cross-reference with page source or network requests if needed

## Notes

- The page uses Flutter rendering, which can limit standard web automation interactions
- Avg FPT values are often 0 early in the season (before matches are played)
- Popularity percentage reflects how many fantasy players have selected that player
- Price may change with each round/matchday
- This captures a single team's roster view; for full player pool data, a different URL or authenticated API access would be needed

## Output Location

```
data/euroleague-fantasy/euroleague-fantasy-players.csv
```

For skill automation, save to: `data/euroleague-fantasy/{team_id}_roster.csv`
