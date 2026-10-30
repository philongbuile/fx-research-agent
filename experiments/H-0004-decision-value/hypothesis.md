# H-0004 Incremental value of model decisions under executable costs

## Id, stage and claim

H-0004, execution-aware rule/model/memory comparison. **Proposal only, stage 0.**
At equal initial risk, common information and execution cutoff, a frozen model
selector improves net portfolio R over a predeclared simple rule on future data.
Separately, frozen Hindsight context improves the same model relative to that
model without memory. Comparing different models cannot isolate memory.

This follows inspection of H-003 outcomes; H-003 is discovery data, not a clean
OOS test of the revised idea. No policy, execution or strategy-stage changes.

## Mechanism and counterargument

The model may reject conflicting combinations better than a pattern predicate
and use prior operating lessons to avoid repeated context mistakes. Alternatively,
it adds latency and narrative priors but no new information beyond technical
inputs. This is not a claim that an LLM predicts prices.

## Falsification and continuation proposal

Primary measurement: paired daily net R difference against the rule, with identical
cost/exposure conventions. Memory effect: paired daily R with minus without memory
for the identical model. A nonpositive difference or unacceptable drawdown at the
predeclared endpoint fails the corresponding claim. Uncertainty overlapping zero
is inconclusive. No interim parameter tuning or selection of a winning session.

Proposed minimum evidence: 20 UTC entry days and 200 closed simulated entries per
arm. These counts do not establish power, independence or edge. Owner/reviewer
must set the actual endpoint, sample policy and drawdown bound before freeze
(Q-15). Adjacent days/currency bets are dependent. Session review and collector
completeness remain manual prerequisites. Changed designs after future results
require another hypothesis ID.

## Costs and execution

- Spread: observed bid/ask, with verified capture provenance.
- Slippage: explicit adverse per-side pips and observed stop gaps.
- Commission/swap: explicit normalized R charges calibrated from verified facts.
- Fill: first executable quote at/after common cutoff; holding duration from fill.
- Missing/old quotes: unavailable, never zero spread or prefill target awards.
- Additional delay/cost stress: separate variants, logged before inspecting results.

## Data and treatments

Source, existing H-003 symbols, exact model/version and data resolution need
review (Q-15). No broker, pair or trading strategy is selected by this file.
The proposed comparator is an EMA50-stretch-fade predicate, subject to review.

All arms receive the same opportunities and information cutoff with the same
normalized initial risk, same-symbol exclusion and concurrent-position cap.
Models run in isolated contexts, blind to other decisions and future quotes.
Freeze and truth-check memory against run records before the study; attribute
content hashes and document IDs. No hourly memory refresh during this ablation.
Adaptive memory requires a separate future design.

## Ranges and design freeze

- Discovery: `20261030T020423Z-h003-overnight-review`.
- Future window: **unset**, must start after freeze and exclude inspected history.
- Freeze commit: **unset**, tooling requires clean committed code.
- Trading remains paused. No automatic resumption or order sending.

## Trial log

Every evaluation writes a new run record, including validation failures. Keep
all variants. Synthetic unit tests validate software, not a strategy.

| run_id | date | what changed | kind |
|---|---|---|---|
| None | — | No reviewed/frozen prospective trial started | — |

Trials so far: 0.

## Stage and gate decisions

| date | stage | decision | by | reason |
|---|---|---|---|---|
| 2026-10-30 | 0 | proposed; no gate passed | Codex | Owner requested improved measurement and future comparison |

[Implementation and packet contract](../../docs/decision-study.md).
`research.toml` stays an unfilled template: this is not a LEAN strategy or a
backtest-ready config. `study.example.json` is the separate offline observation
contract; its proposed thresholds are not approved gates.
