# Spec: graph renderer + skill rewrite (ticket 4/8)

## Goal

- `docs/data-graph.md` generated deterministically from the registry, with consistent shapes, naming and colors.
- The `update-data-docs` skill drives the generators instead of describing a by-hand procedure.

## Scope

- `src/eupy/registry/render_graph.py` + subcommand `python -m eupy.registry graph`; umbrella `python -m eupy.registry docs` (catalogue + graph).
- Rewrite `.claude/skills/update-data-docs/SKILL.md`: run `docs`, then verify. Remove the "don't build a parser" stance and the manual derivation steps.

## Rules (approved)

- **Shapes by kind:** external source = stadium `(["…"])`, script = rectangle `["…"]`, dataset (raw or produced) = cylinder `[("…")]`. `final` is shown by a `★` suffix and a thick border (class), never by shape.
- **Ids:** `x_<source>`, `s_<script>`, `d_<dataset>`, full names (sanitized), no ad-hoc abbreviations.
- **Labels:** dataset = name (raw prefixed `<source-dir>/`, as in the catalogue); script = script name; stage shown in the dataset label.
- **Colors by stage:** one hue per stage; a dataset takes its stage hue; a script takes a neutral fill with a stroke in the hue of its (highest) output stage; sources are gray. A new stage automatically gets a new hue (fixed palette, error if exhausted).
- In-place (allowlisted) scripts are drawn with their self-loop until ticket 8 removes them.

## Decisions

- Coverage invariant enforced in the generator (and tested): every declared node has exactly one class; every class member is a declared node. Replaces the ad-hoc check in the old skill.

## Out of scope

- Pipeline subgraphs (ticket 6), HTML (ticket 7).

## Depends on

- Ticket 3 (shares the registry-driven doc pipeline).

## Pass criteria

- [x] Two consecutive runs are byte-identical.
- [x] All registry nodes/edges present; edge set equals the registry's (test).
- [x] Coverage invariant test passes; shapes/ids/colors follow the rules above (golden test on a synthetic registry).
- [x] Graph renders in `mkdocs build`/GitHub preview with no Mermaid errors (screenshot checked once).
- [x] Graph and catalogue agree on dataset names and stages (test).
- [x] `update-data-docs` skill updated; running it end to end reproduces both docs.
- [x] `ruff check` + `ruff format --check` clean; `uv run pytest` green.
