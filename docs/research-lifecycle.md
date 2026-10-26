# Research lifecycle

A strategy moves forward only through these gates. Every gate needs evidence in the structured store (run records), and every promotion needs the owner. LLMs can do the work inside a stage, but they cannot pass a gate.

```
idea -> candidate -> in-sample backtest -> robustness -> out-of-sample / walk-forward -> demo -> owner approval -> (live: out of scope)
```

| Stage | Entry needs | Work | Exit evidence | Who passes the gate |
|---|---|---|---|---|
| 0. Idea | — | Write `experiments/<H-id>/hypothesis.md`: the market behavior you claim, why it should exist, how you would falsify it, and the costs it must survive. Record it in Hindsight with the `H-id`. | Hypothesis file merged | Owner (or a reviewer) |
| 1. Candidate | Hypothesis merged | LEAN code in `lean/<Name>/` on a branch. Parameters are fixed in `research.toml`, not tuned on the full history. | PR reviewed; the code is a git commit | Reviewer (a human, or an agent review **plus** a human merge) |
| 2. In-sample backtest | Candidate commit | `fxra backtest` on the **in-sample** range only. Real costs: spread from data, slippage, commission, swap. | `run.json` with `reproducible: true` | — (information only) |
| 3. Robustness | In-sample result | Cost stress (spread ×1.5 and ×2, plus slippage), parameter neighborhood (not search: small perturbations around the frozen values), sub-period stability, trade count large enough to mean something. Use LEAN's `Probabilistic Sharpe Ratio` and keep a count of every variant tried. | Runs for each stress test; the number of variants tried, written in the hypothesis file | Owner |
| 4. Out-of-sample / walk-forward | Design frozen (commit hash written down) | Run once on data never touched before. Walk-forward: re-fit only through rules fixed in advance. **No changes after you see the OOS result.** A failure means a new hypothesis id, not a tweak. | OOS run record; result compared to the in-sample result | Owner |
| 5. Demo | OOS passed | Paper (LEAN) or broker demo account through the risk gate (`mode` widened in a reviewed change). At least N weeks (owner sets N), with fills compared to backtest assumptions. | Fills dataset (environment=`demo`), daily reconciliation, incident log | Owner |
| 6. Owner approval | Demo report | Written approval naming the strategy commit, risk limits, max size and kill criteria. | Approval record (`reports/approvals/`) | **Owner only** |

## Commands per stage

| Stage | Command | Records |
|---|---|---|
| 2 | `fxra backtest <config> [--segment in_sample]` | `kind=backtest`, `window.segment=in_sample`, dates from `[split]` |
| 3 | `fxra stress <config> --spread 1.5 --spread 2 --slippage-points N` | one `kind=stress` run per variant, in-sample only, `stress` block |
| 4 | `fxra backtest <config> --segment out_of_sample` | refused if the hypothesis already has a completed OOS run (failed runs do not block, and stay recorded) |
| 4 | `fxra walk-forward <config>` | windows from `[walk_forward]` in `research.toml`, same code and parameters in every window, one `kind=walk_forward` OOS run each, shared `group_id`. Refuses dirty code. Counts as the OOS look: refused if a completed OOS run exists |
| any | `fxra report [--hypothesis H]` | markdown per hypothesis: trial count, in-sample and OOS tables, an "evidence" column that flags technical-only, non-reproducible, in-sample and failed runs |

## Rules that apply at every stage

- **One hypothesis, one id.** If you change the idea after seeing results, you have a new id. Keep the old one and its failure.
- **Count the trials.** Every backtest variant is a run record. Selection bias grows with the number of variants, so report how many were tried.
- **In-sample results are never evidence of profit.** Do not cite them in an approval.
- **Technical runs are not research.** `technical_only: true` runs, such as the smoke test, only prove the engine works.
- **Dirty code is not evidence.** `reproducible: false` runs (uncommitted code) cannot pass any gate.
- **Postmortem every failure** worth remembering: one Hindsight memory with the `run_id` and the lesson, tagged `postmortem`.

## What goes into Hindsight at each stage

| Event | Memory content | Tags |
|---|---|---|
| New hypothesis | Claim, rationale, falsification test, `H-id` | `hypothesis`, `H-id` |
| Run finished | `run_id`, stage, one-line outcome, link to `run.json` (numbers stay in the run record) | `result`, `H-id` |
| Gate decision | Passed or failed, by whom, why | `decision`, `H-id` |
| Failure or incident | What happened, cause, what changes | `postmortem` |
| Operating lesson | Tooling or data gotchas (for example, broker timezone found to be UTC+3 with DST) | `ops` |
