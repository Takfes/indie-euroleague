# Spec: option value, bench-swap and captain-move terms (ticket 3 of 3)

Status: **deferred, not implemented.** Not to be built as drafted: the per-pair constraints produced about 460k rows at
realistic size (~700 candidates, ~115k pairs) and HiGHS did not finish in 10+ minutes. See `docs/next-steps.md`
("Squad optimizer") for the findings and an aggregated per-player formulation that solves in about 47 s. The
objective modes in `docs/optimizer.md` (risk adjustments on one shared score) do not model this; they were chosen
first because they are cheap.

## Idea worth keeping

Between turns the real game allows a bench-to-active swap and a captain move. Each is a put option on the earlier-turn
player's score `X ~ N(mu, sd^2)`: with strike `K` (the later-turn player's mean) and notional `n` (0.5 for the swap,
1.0 for the captain move)

```
value = n * ((K - mu) * Phi(d) + sd * phi(d)),  d = (K - mu) / sd;   sd <= 0:  n * max(K - mu, 0)
```

using `math.erf` (no scipy). Reference values: `(12, 8, 10, 0.5) = 1.145`, `(15, 7, 10, 0.5) = 0.488`,
`(20, 9, 16, 1.0) = 1.939`, `(20, 5, 16, 1.0) = 0.601`, `(20, 9, 22, 1.0) = 4.679`.

## Design to redo

- Pairwise terms `v[i,j]` (swap) and `w[i,k]` (captain) for every pair with `turn_i < turn_j`; assignment variables
  `a`, `z` in [0, 1]. Swap: donor active, receiver on the bench, each used at most once. Captain move: donor is the
  captain, receiver starts.
- Accepted approximation: the two terms are additive, so one player can be valued as donor of both, which the real
  game cannot deliver in one round (needs a two-stage model).
- The `sd = 0` limit is not inert: formation constraints can make `max(K - mu, 0)` positive without any spread data.
- The needed input is the standard deviation, now the `pir_std` column of the eubook (filled with 0 for one-game
  players).

## Pass criteria for whoever builds it

Put-value references to 1e-3; assignment constraints hold on a solved instance; a fixed-assignment brute-force match
for `a`/`z`; solve time at ~700 rows measured and reported (stop and report rather than loosen a timeout); a crafted
case where the term changes the roster and one where it changes the captain; one showing a player used by both
terms; ruff and pytest clean.
