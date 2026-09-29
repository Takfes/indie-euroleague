# Spec: entity resolution without in-place mutation; `entity` joins DVC (ticket 8/8)

## Goal

- Crosswalks become a pure function of raw data + tracked verdict files, so `entity` can run under `dvc repro` and rebuilding from scratch reproduces the current crosswalks exactly.
- Remove the last graph cycles; `all` covers `entity`.

## Problem today

- `resolve_player_names`, `resolve_team_names`, `resolve_fantasy_stats_player_names` read and rewrite their own crosswalk ("merged in place", agent-resolved rows preserved); `apply_player_name_verdicts` / `apply_team_name_verdicts` read and write the same file.
- DVC rejects cycles and deletes outputs before a rerun, which would wipe the curated verdicts. It also violates "outputs derivable from raw data + code": the judgements live only inside the output file.

## Scope

- **Extraction (first step, test-first):** derive the existing agent-resolved rows from the three crosswalks into an initial verdict batch per crosswalk.
- **Verdict storage:** immutable batch files in tracked directories under `data/curated/` (e.g. `player_name_verdicts/`, `team_name_verdicts/`, `fantasy_stats_player_name_verdicts/`) — same pattern as the live delta folders. Declared in `raw_sources.toml` as manual raw datasets (stage 0). Later batch wins on the same key.
- **`resolve_*`:** become pure — inputs = raw data + verdict directory; output = crosswalk (no read of the previous output).
- **`apply_*_verdicts`:** become an ingest step — validate a verdicts JSON and write it as a new batch file. Manual/`Impure: true`, not a DVC step, no in-place write.
- Update `resolve-player-names` and `resolve-team-names` skills to append batches instead of patching crosswalks.
- Enable `entity` in `pipelines.toml` (`dvc = true`), add it to `all`, empty the registry cycle allowlist, regenerate `dvc.yaml`, catalogue, graph, HTML.

## Decisions

- One batch = one JSON verdicts file (existing schema) plus ingest metadata; no schema redesign beyond what extraction needs.
- Crosswalk column order/format unchanged, so consumers (`normalize_fantasy_stats`, future features) are unaffected.
- A changed raw price snapshot legitimately reruns `entity` — intended.

## Out of scope

- Changing matching logic (`matching.py`), the optimizer/ML pipelines, the control panel.

## Depends on

- Tickets 5 and 6 (needs `dvc.yaml` generation and the wrapper). Ticket 7 not required.

## Pass criteria

- [ ] Extraction test written first: rebuilding each crosswalk from raw data + extracted batches is **byte-identical** to today's committed file (player, team, fantasy-stats player).
- [ ] No script reads its own output; registry `check` passes with an empty allowlist; graph has no self-loops.
- [ ] `dvc repro entity` runs from raw data; second run is a no-op; adding a batch file reruns only affected crosswalks and downstream (`fantasy_stats_players_normalized`).
- [ ] `eupy run all` includes `entity`.
- [ ] Ingest step: valid batch written, invalid batch rejected, existing batches never modified (tests).
- [ ] Both `resolve-*` skills updated and walked end to end once on a synthetic verdict.
- [ ] Docs/graph/catalogue/HTML regenerated; `ruff check` + `ruff format --check` clean; `uv run pytest` green.
