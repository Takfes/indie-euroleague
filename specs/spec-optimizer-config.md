# Spec: optimizer configuration and input (ticket 1 of 3)

Status: delivered, then reshaped. The standalone optimizer TOML (`data/stage_99/optimizer_config.toml`), the CLI flags
and the CSV input of the first version are gone.

## Today

- Parameters live in `[optimization]` of `config/config.toml`, typed in `eupy.config.defaults.Optimization`; a typo'd
  key or section raises (`eupy.config.loader`). Keys: `target`, `robustness_lambda`, `upside_beta`,
  `downside_gamma`, `credit_value`, `cash`, `max_trades`, `unlimited_trades`, `min_players_per_turn`, `must_have`, `avoid`, `solver`,
  `solver_options`. Reference: the commented block in `config/config.toml` and `docs/optimizer.md`.
- Input is the first sheet of `data/stage_03/optimizer_input.xlsx`; columns are mapped in `COLUMNS` / `RISK_COLUMNS`.
- The head coach and `player_id` are dropped: `player` is the unique row key and the squad is 4 G / 4 F / 2 C.
- A fresh team (no `in_prev_roster`) needs `unlimited_trades = true` or `max_trades >= 10`, else the solve is
  infeasible and the error says so.

## Kept from this ticket

- Validation fails early with what / why / next-step messages; the previous roster is empty or a full 4/4/2 squad.
- `unlimited_trades` is its own key because TOML has no null.
