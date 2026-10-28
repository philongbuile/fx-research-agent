# H-001 implementation and remaining prerequisites

The owner merged the channel-breakout proposal in PR #23 and requested the next
steps on 2026-10-28. This authorizes continuing its research implementation;
it does not pass a research gate or authorize an order.

## Implemented software boundary

- `src/fxra/hour_bars.py`: pure UTC-aligned M1-to-H1 aggregation. Requires
  canonical, ordered, unique, valid bars and a closed hour range. Missing minutes
  suppress the affected hour, including entirely absent hours, with explicit
  missing timestamps. No sorting, forward fill or timezone inference. Unknown
  tick volume stays unknown. Hour spread stays null: minute spread minima cannot
  reconstruct execution quotes. Source side, provenance and technical-only
  restrictions remain the caller's responsibility; this function certifies none.
- `src/fxra/breakout.py`: stateless calculation of the merged proposal's entry
  channel, ATR and opposite-channel exit thresholds. Requires the exact complete
  warm-up and signal interval. Signal time is the completed hour's end; a strict
  breakout excludes the signal bar from both channels. Missing input suppresses
  all signals/thresholds. Existing stops must be maintained by a future runner.
- Synthetic regression tests cover midnight, incomplete hours, wholly absent
  hours, invalid/duplicate/unordered input, strict long/short breakout equality,
  previous-close ATR gaps, channel exclusion and future-input refusal.

This is not a LEAN strategy or a fill simulator. There are no simulated orders,
positions, sizing, stops, client order IDs, costs or broker calls in these pure
functions. No strategy trial or new LEAN invocation was performed. There are no
performance results to report. Fixtures are software tests, not market evidence.

## Data inspection

A fresh restricted MCP capture was retained in quarantine as
`20261028T114642Z-0be9c694`, SHA-256
`fb0040e8e6ce4b110ab53d3e487dc70168e4c60853a9f92c75e49c2cbc3d67f6`.
The MCP marked it `research_ready: false` with unverified raw MT5 epochs and
pending timezone/calendar verification. No rows were imported or converted.
The earlier technical replay remains ineligible for H-001 strategy research.

## Remaining sequence

1. Obtain source-backed historical UTC/DST and session rules covering the
   actual returned history. Establish quote-level bid/ask fidelity and historical
   cost inputs: commission, slippage and swap with units and dated evidence.
   Current offsets or swaps cannot stand in for that history. The restricted MCP
   currently captures bounded M1 bars, not historical ticks or swap series; any
   extension requires fixture tests and review within the approved data scope.
2. Audit eligible history without viewing strategy returns. Propose exact IS/OOS
   dates, minimum evidence and predeclared stability/cost stresses for owner
   review (Q-14). Do not fabricate dates from the quarantined capture.
3. Implement `lean/EurusdBreakout/main.py` and its `research.toml` once data and
   execution assumptions can be stated honestly. Add side-correct entry/exit
   fills, stops/gaps, lots via SymbolSpec, deterministic order IDs and retention
   of uncertainty; verify parity against the pure indicator fixtures. Do not use
   the technical replay's artificial algorithm calendar as a source calendar.
4. Freeze the complete design and data references, then run through
   `uv run fxra backtest`, retaining every failure and variant. Run IS and frozen
   robustness studies first; OOS remains subject to the lifecycle gate.

Protected risk/execution settings and strategy stage remain unchanged. See
[the hypothesis](../experiments/H-001-eurusd-breakout/hypothesis.md),
[workflow](mt5-lean-workflow.md) and [lifecycle](research-lifecycle.md).
