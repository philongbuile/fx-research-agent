# H-001: EURUSD H1 channel breakout

Owner selected candidate A (channel breakout) on 2026-10-28. This document
proposes the initial specification before any strategy run. Parameter values
below are design proposals, not optimized values or approved execution limits.
No candidate promotion, deployment or broker order is authorized by selection.

## Claim and mechanism

A close beyond a previously established price range may be followed by enough
directional continuation to survive realistic execution costs. Clustered stops
and delayed reactions are possible mechanisms, not established facts about this
particular rule. The competing explanation is false breakouts and efficient
pricing after costs. Test both long and short symmetrically.

## Proposed frozen rules

- EURUSD, UTC-aligned hour bars aggregated from verified minute observations
  (D-17). Require complete constituent minutes; do not fill missing bars.
- Channel: highest bid high and lowest bid low of the preceding 24 completed
  hour bars, excluding the signal bar.
- Signal: the completed signal bar closes strictly above/below that channel.
  Use only information available at that bar's end; never fill retrospectively
  at its close. Enter at the first executable quote of the next hour, with
  the actual bid/ask side and modeled slippage.
- One position maximum; no pyramiding. A missed entry is cancelled rather than
  carried forward. No new entry while the position is being closed.
- ATR: arithmetic mean of true ranges over the preceding 14 completed bars,
  including the signal bar, with previous closes available. Require warm-up.
- Initial stop: twice that ATR from actual entry, snapped conservatively to
  tick size. If the price has invalidated the proposed stop or the stop cannot
  be represented under SymbolSpec, skip entry. Stops are never widened.
- Exit at the first of: stop, close beyond the opposite preceding 10-bar
  channel, or 24 completed holding bars. Non-stop exits execute on the next
  available quote. No take-profit or trailing-stop optimization initially.
- On missing data, forbid entries and preserve an existing stop; mark an
  affected exit/execution interval as uncertain rather than inventing a fill.
  A weekend gap through a stop fills at the first executable worse quote,
  including modeled slippage, not at the unavailable stop price.
- Backtest sizing: proposed fixed 0.01 lot, only if the actual SymbolSpec permits
  that size/step; convert lots through its contract size. This is a research
  convention, not a change to approved strategy lists or risk configuration.
- Every simulated order gets a unique deterministic client_order_id. Entries
  require stop protection. No raw trading MCP or broker execution is used.

## Costs and data prerequisites

Historical UTC/DST mapping, source session calendar, bid/ask quote fidelity,
hour aggregation and adequate history must be verified first. The short
TECH-0002 observation window is technical-only and is ineligible for this study.

Historical spread, commission, slippage and swap inputs remain unresolved; do
not replace them with zero or apply current swap observations to all history.
Use a new research.toml only after these inputs can be stated with provenance.
Minute minimum spread is insufficient to claim executable intrabar costs.

## Falsification and evaluation proposal

Reject this specification if net expectancy after verified costs is nonpositive
in its frozen evaluation, if gains depend on a narrow subperiod, or if modest
predeclared cost stress removes the result. Sparse trades or unresolved data
quality mean inconclusive, not passed. Positive in-sample performance alone is
never evidence of edge or permission for promotion.

Proposed chronological split: first 70% of the agreed eligible date range for
in-sample work, final 30% held out. Exact dates, minimum trade count, stability
criteria and stress magnitudes must be fixed before the first run. Do not inspect
the holdout while choosing those criteria. Dates are not set from the currently
bounded capture; no OOS execution is authorized until the lifecycle gate passes.

## Unresolved before implementation/run

- Owner review of these proposed parameters, sizing and falsification thresholds.
- Eligible historical range and verified source costs/time/calendar.
- Exact split dates, minimum evidence threshold and design-freeze commit.

No strategy trials have been run. Record every later variant and failure here
with its run_id; never delete unsuccessful trials. Stage remains idea/proposal;
no stage gate is passed by this document.
