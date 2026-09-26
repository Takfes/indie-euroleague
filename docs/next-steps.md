# Next steps

Deferred work, off the critical path.

- **Vacuous skill test:** `tests/test_skill_script_paths.py` scans `.claude/skills/`, which no longer exists, so both tests pass on nothing. Deleting it leaves pytest with zero tests (exit 5, not green). Revisit when the first real test or skill lands: retire it or keep it as a guard.
- **Catalogue/graph generator:** `docs/data-catalogue.md` and `docs/data-graph.md` are empty placeholders. Needs a script that reads the script I/O headers (inputs, outputs, `final`) and emits both with a "GENERATED — do not edit" banner.
- **Fetcher header + tests:** `src/fetch_basketballsphere_prices.py` has no declared inputs/outputs/`final` header and no tests. Add both once the header format is settled.
