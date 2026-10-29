# Decision trace and learning log (D-44)

Owner request, 2026-10-29: record every market evaluation and decision of the
demo agents in a form that lets us judge decision quality and improve it over
time, and show it on the dashboard.

## What is recorded, and what is not

No chain-of-thought, hidden reasoning or scratchpad is collected or shown. A
trace holds only:

1. **Inputs**: what the decider was given, recorded at decision time by the
   loop (bar, quote, indicator values, pattern matches, context, account state).
2. **Decision as recorded**: the decision, the strategy/policy version, the
   decider (script or `<agent>/<model>`), the prompt reference, the plan (entry
   reference, stop, target, lots, risk gate result) and its invalidation
   conditions. Reasons are short codes, each with an origin:
   - `rule`: what the script's rule recorded when it fired (H-001B, H-002, the
     H-003 loop), mapped one-to-one from the journal row.
   - `model`: codes, confidence and the short written reason the model
     submitted **with** its `fxra demo-decide` call. The written reason is
     stored as given. It is the model's stated justification, not its internal
     reasoning, and the UI says so.
   - `owner`: an owner-requested close (`fxra demo-close`).
3. **Derived afterwards** (`derived`): context codes that fxra computes from the
   recorded inputs when it builds the trace, for example `h1_trend_against`.
   The decider did not state them. They exist so decisions can be grouped and
   compared; they are never presented as the decider's reasons.
4. **Outcome**: measured after the decision. Broker P&L (net, gross,
   commission, swap), R, slippage, maximum favourable/adverse excursion, the
   shadow outcome of H-003 candidates, and checks of whether the planned
   conditions happened (`target_reached`, `stop_hit`, `time_exit`,
   `skip_avoided_loss`, `skip_missed_gain`).

No API keys, credentials, account numbers, server names or personal data are
stored. Account state is equity, balance, currency and the open-position count.

## Observation mode

The traces are a read model over the existing journal tables. Strategies,
the risk gate and the demo adapter are unchanged:

```
H-001B / H-002 / H-003 loop ──writes──> decisions, candidates, orders, entry_tags, trades (unchanged)
fxra demo-decide (model)    ──writes──> candidates (+ reason_codes, confidence, prompt_ref)
                                          │
decision_log.sync (after each tick,      ▼ reads only
  and on /api/decision-log)  ──writes──> decision_traces   (the only table it writes)
decision_log.observe (with the tick's    ──writes──> decision_traces.excursion
  M15 bars)
dashboard Decision log panel <── /api/decision-log?symbol=&strategy=&version=&decision=&decider=&since=&until=
fxra demo-trace (same filters, JSON)
```

`sync` runs after every strategy in the tick has finished, inside a
`try/except`; a failure is reported on the panel and never reaches trading.
Tests check that a sync leaves every other table byte-for-byte unchanged.

The only change on the decision path is additive: `fxra demo-decide` accepts
optional `--codes` (from the vocabulary below) and `--confidence` (0 to 1), and
records a hash of the decide skill the model was following (`prompt_ref`).
Nothing about which orders are sent, their size or their gate changes.

## Schema

Migration: new table `decision_traces` (created by `SCHEMA`, so old journals
get it on open) and three nullable `candidates` columns added through
`MIGRATIONS`: `reason_codes` (JSON list), `confidence` (REAL), `prompt_ref`
(TEXT). Old rows stay NULL.

| Column | Meaning |
|---|---|
| `id` | the order id for entries and exits (`H3GC-…`, `H2CL-…`, `H2X-…`), the candidate id for H-003, else the journal decision key |
| `at`, `bar` | decision time; open time of the bar evaluated (UTC) |
| `strategy`, `arm` | `H-001B`, `H-002`, `H-003`; arm such as `H-003-claude` |
| `symbol`, `timeframe`, `session` | e.g. `EURUSD`, `M15`, `london` (session of the bar hour) |
| `version`, `policy_version` | `H-003@gated-v1`; H-002 arm policy version in force (H-003 inherits its lots) |
| `decider`, `prompt_ref` | `script`, `claude/claude-opus-5-5`, `owner:<author>`; `fxra-demo-decide@<sha256 12>` |
| `decision` | `buy`, `sell`, `hold`, `close`, `no_trade`; NULL while an H-003 candidate waits |
| `status` | what happened to it: `pending`, `filled`, `sent`, `skipped`, `expired`, `risk_rejected`, `not_sent`, … |
| `confidence` | model-stated, 0 to 1; NULL when nothing computed one (scripts compute none) |
| `inputs` | JSON: `market` (source, timeframe, bar open/close, observed_at, quote, close, ATR), `features`, `account` |
| `reasons` | JSON list of `{code, origin, detail}`; origins `rule`, `model`, `owner`, `derived` |
| `model_reason` | the written reason submitted with an H-003 decision, verbatim |
| `plan` | JSON: basis (`order` or `shadow`), entry reference, stop, target, stop distance, lots, risk gate result, `invalidation` list |
| `outcome` | JSON: `trade` (net, R, slippage, exit), `shadow`, `checks`, `verdict` |
| `excursion` | JSON: MFE/MAE in pips and R from the M15 bars of the tick snapshot (approximate) |
| `final` | 1 when nothing will change; non-final traces are rebuilt each sync |
| `source`, `src_at` | `decisions:<key>`, `candidates:<id>` or `orders:<id>`; source row time |
| `codes_version`, `updated_at` | reason vocabulary version; last rebuild |

Which journal row becomes which trace:

| Source | Traces |
|---|---|
| `decisions` H-001B row | one trace per closed H1 bar (exit rows come from `orders`) |
| `decisions` H-002 row | one trace per entry or risk-rejected order; otherwise one `hold`/`no_trade` trace for the evaluation |
| `decisions` H-003 row with no pattern | one `no_trade` trace (the model never saw it) |
| `candidates` | one trace per H-003 candidate and arm |
| `orders` of kind `exit` | one `close` trace per exit (time exit, channel exit, owner close) |

Traces are rebuilt from their source while not final, so the outcome fills in
as trades close and shadows are scored. Everything except `excursion` can be
rebuilt from the journal; excursion needs the bars of the tick snapshot
(about 30 hours of M15), so traces older than that keep NULL. Traces become
final at the latest three days after the decision.

## Reason codes

`decision_log.CODES` is the vocabulary (`codes_version` 1), one description
per code. Model codes accepted by `fxra demo-decide --codes`:

`trend_aligned`, `trend_against`, `signals_agree`, `signals_opposed`,
`solo_signal`, `pattern_history_positive`, `pattern_history_negative`,
`news_risk`, `volatility_high`, `volatility_low`, `spread_wide`,
`extended_move`, `exposure_conflict`, `session_thin`, `playbook_prefer`,
`playbook_avoid`, `risk_reward_ok`, `risk_reward_poor`, `other`.

Rule and derived codes are listed with their descriptions in the module and
on the dashboard.

## Using it

```powershell
uv run fxra demo-trace --strategy H-003-claude --decision no_trade --since 2026-10-29
uv run fxra demo-trace --symbol EURUSD --limit 20
uv run fxra demo-decide --skip <id> --codes trend_against,exposure_conflict --confidence 0.6 --reason "..." --author claude/claude-opus-5-5
```

Questions the log is meant to answer: do taken candidates beat skipped ones
on shadow R, per model and per code; how often is the stop (the stated
invalidation) hit; how much does a late decision cost (`entry_vs_bar_close_pips`);
does stated confidence track outcome. These are demo forward observations,
not evidence of edge, and in-sample for any rule distilled from them.
