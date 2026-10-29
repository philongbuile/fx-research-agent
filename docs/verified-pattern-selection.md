# Prospective selective demo study

Status: demo proposal 6 awaiting cross-agent review, not an active policy.
Owner direction, 2026-10-29: move from broad exploration toward verified
selection and investigate patterns that currently perform well. This changes
the research objective; historical winners are still discovery observations.
No execution-stage promotion or risk-limit change is part of this proposal.

## Proposed treatment

- Codex arm: enable exactly `ema50_stretch_fade`, `bb_rsi_reversion` and
  `engulfing`. The first two are the selected research group. Engulfing remains
  a separately reported observation cohort under agreed proposal 4; it is not
  selected as a winner. Do not mix its results into the selected-group score.
- Stop new Codex entries from other patterns after the agreed activation time.
  Existing positions keep their original stops, targets and exit management;
  never force-close them merely to change a pattern list.
- Claude arm stays unchanged as the broad-exploration comparator. Its owner
  retains control; record any later change as a comparison confound.
- Keep symbols, lots, stops, targets, time exits and all account gates unchanged.
  Do not multiply entries or increase lots to obtain samples faster. The existing
  first-party loop remains the only execution path. H-001B remains unchanged.

The current code records all matched patterns before filtering the enabled list
(`h002.py` signal matches). That preserves signal observations; it is not a
counterfactual fill or proof that a disabled trade would have executed. The
Claude arm supplies actual broad-exploration observations, subject to its own
positions and shared account capacity.

## Freeze before activation

Proposed entry window: 2026-10-30T00:00Z inclusive through
2026-11-06T00:00Z exclusive (local 07:00 to 07:00). Activate only after the other
agent reviews the exact change under D-39. If review misses the proposed start,
record replacement future dates before reading their outcomes; never backdate.
Freeze code commits, both policy versions/checksums and the proposal reference
at activation. Do not start from a dirty strategy checkout.

Proposal 5 already proposes an observational study of the two selected patterns
over this interval. This treatment is a separate follow-up: do not silently
rewrite proposal 5 or claim its observation-only approval permits selection.
Proposal 4's engulfing window remains 2026-10-29T00:00Z to 2026-11-05T00:00Z;
preserve its definition and record the arm-policy change as a possible confound.

## Evaluation fixed in advance

At the window end, let positions opened inside it finish under their existing
rules, then evaluate once. Operational safety checks continue throughout, but
do not tune the selection list from interim performance.

- Count unique symbol/bar/side events, not duplicated arms or co-firing pattern
  trades. Report distinct days and bar clusters, with day/news dependence intact.
- Report each selected pattern separately, each arm, solo/confluence, symbol,
  session, fills and cost assumptions. Use initial-risk-normalized returns as
  the main unit; do not rank mixed-symbol averages of pips or raw USD alone.
- Require at least 30 unique events per selected pattern across at least three
  distinct UTC dates and two sessions for an informative review. These are
  proposed minimum evidence conditions, not a statistical guarantee of edge.
  Sparse/concentrated evidence is inconclusive, including a perfect win rate.
- A pattern needs positive event-average R after the stated costs, no single
  day accounting for all positive R, and a separately declared cost sensitivity
  before it can motivate another reviewed preference proposal. A common pip
  surcharge is an approximation; validate symbol-specific costs before claiming
  anything about real execution or profitability.
- Compare selected and broad groups over the same future interval, including
  opportunity count, initial-risk exposure, drawdown, costs and skipped/blocked
  signals. Shared account limits and different open positions prevent treating
  this as an independent randomized experiment or attributing outcomes to an
  LLM's forecasting ability.

Results are demo forward observations, not evidence of live edge. Keep weak and
failed results. Any new filter, threshold, symbol subset or date extension after
the endpoint needs a new recorded study design. No automatic promotion or size
increase follows from a positive result.

## Operational state

At proposal creation no policy has changed. Review uses `fxra demo-propose`,
then the Codex owner applies the exact approved enabled list through
`fxra demo-policy --arm codex`, with author and reason. Keep the previous policy
reference for an explicitly logged operational rollback. No direct JSON edit,
raw broker call, loop duplication or protected-gate edit is needed.

See [demo workflow](demo-agent.md), [playbook decisions](decisions.md#trading-playbook-d-39)
and the existing [H-001 historical implementation limits](h001-implementation.md).
