# Execution-aware decision evaluation

Owner direction, 2026-10-30: improve measurement, freeze a new study, compare a
simple rule with the same model without/with memory, and evaluate across future
days. Trading remains paused. This change is offline tooling, not a deployed
collector, a completed forward trial, strategy promotion or permission to resume.

## First audit the existing journal

```powershell
uv run fxra demo-evaluate --since 2026-10-29T18:00:00+07:00 --until 2026-10-30T08:21:01+07:00
```

This opens SQLite with `mode=ro`, never instantiates the migrating `Journal`, and
reads candidates, fills and equity in one transaction. It writes a new ignored
`runs/<run_id>/run.json` containing sanitized source projections and their hash.
Source account identifiers and raw order responses are not selected. Windows are
half-open. Candidate cohorts use creation time; realized results use closing time.
The output separates explicit skips, expirations, rejected approvals, confirmed
fills, unscored decisions, memory attribution and matched actual/shadow results.

`legacy_shadow_*` means the original bar-close, default-stop/target M15
simulation. It is never renamed to executable performance. Broker net includes
recorded deal charges; price R and price R minus the additional assumed 0.8-pip
cost are different metrics. The paired comparison uses exactly the same closed
candidates, excluding recent candidates with no shadow. No broker reconciliation
is performed: open positions/equity are last-known journal state. Historical
shadow maturation times were not stored, so an earlier cutoff cannot reconstruct
the memory/feedback that was available then.

## H-0004 prospective protocol

See [the hypothesis](../experiments/H-0004-decision-value/hypothesis.md). The
checked-in `study.example.json` is a concrete **proposal**, with review/window/
scope fields deliberately unset. Q-15 names the remaining owner/reviewer choices.
No config here edits live policy, lots, risk limits, approvals or kill switch.

1. Review the proposed rule and thresholds. Set the exact existing H-003 symbol
   scope, future window, model/version, observation constraints and cost estimates.
   Use one same-symbol position and one normalized initial risk unit per entry in
   every arm; the shared open-position cap is an observation parameter, not a
   modification of the broker risk gate. Cross-currency concentration is not yet
   modeled. Do not describe this normalized portfolio as matching live USD risk.
2. Save the exact prompt and a curated, timestamped memory snapshot. Snapshot
   shape: `{"as_of":"<UTC>","documents":[{"id":"...","available_at":"<UTC>","content":"..."}]}`.
   No secrets, broker/account identifiers or raw trading datasets. Review memory
   arithmetic against run records first. Every document must be available by the
   snapshot cutoff and before freeze. Do not refresh memory during this experiment.
3. With clean committed code, set config `status=reviewed` and record the reviewer.
   These fields document experiment review, not a strategy-stage approval. Freeze
   to a new file; existing files are never overwritten:

   ```powershell
   uv run fxra decision-study freeze --config artifacts/study/study.json --output artifacts/study/frozen.json
   ```

   Freeze pins the config, exact prompt/memory text, content hashes and code commit.
   It refuses dirty code and a window that starts in the past. Keep the manifest
   immutable; any changed design after seeing results requires another hypothesis.
4. A trusted, separately reviewed collector must supply the full opportunity
   ledger and observed quote history with provenance. **No collector or model
   invocation is supplied by this increment.** Do not start the order-capable
   trading loop just to obtain these inputs. Reuse reviewed data adapters/storage
   contracts; a new adapter must follow AGENTS.md.
5. Run separate isolated model contexts with the same model/version and prompt;
   only the memory arm receives the frozen memory. Neither context sees another
   arm's decisions or future quotes. Both receive identical structured inputs.
   Missing replies stay missing. Common entry cutoff includes the time required
   for both replies; the rule waits until that cutoff too. This measures decision
   selection, not a speed advantage. A later experiment can test latency separately.
6. Evaluate the frozen packet offline:

   ```powershell
   uv run fxra decision-study evaluate --manifest artifacts/study/frozen.json --packet artifacts/study/packet.json
   ```

   The current checkout must be clean at the frozen commit. Every evaluation gets
   a new run record, including validation failures. Successful records pin the
   exact manifest and packet. Do not repeatedly inspect and retune OOS results.

## Packet contract

Top level: `manifest_sha256`, `provenance` (source/capture/checksum evidence), and
`opportunities`. Every opportunity contains:

| Field | Meaning |
|---|---|
| `id`, `symbol`, `side` | Unique opportunity; frozen symbol scope; buy/sell |
| `spec` | Full `SymbolSpec`; explicit source, pip size and contract facts |
| `available_at`, `inputs_as_of` | When packet was available, and latest input timestamp; both at/before cutoff |
| `decision_at` | Common simulated entry cutoff; within the future study window |
| `inputs` | Identical model/rule data, including `atr` and `patterns_for` |
| `decisions` | `model_no_memory` and `model_memory`; omit missing replies |
| `quotes` | Strictly increasing UTC `time`, `bid`, `ask`; no account data |

A model decision contains boolean `take`, `at`, `model`, `input_sha256`,
`prompt_sha256` and `memory_refs`. The memory arm also supplies `memory_sha256`
and all frozen document IDs; the no-memory arm supplies an empty reference list.
Input hashes use `decision_study.input_hash(op)` to bind identity, symbol/side,
contract, information/entry cutoffs and structured inputs, excluding future quotes
and other decisions. Asset/manifest hashes use `demo_evaluation.digest` (canonical
sorted JSON), not pretty-printed file bytes. The rule uses the frozen pattern.
No explicit model confidence is required or invented.

These hashes enforce declared packet equality; they cannot prove timestamps,
collector completeness, provenance or model isolation. Independent review remains
necessary. See `tests/test_decision_study.py` for a complete synthetic packet.

## Execution and comparison semantics

- Entry is the first quote at/after the common cutoff, within the frozen delay
  bound. Buys enter ask and exit bid; sells enter bid and exit ask. Historical
  intrabar extrema before entry cannot award a target.
- Stops/targets are anchored to the simulated executable entry. Stops gap at the
  observed price; targets receive their limit price. Adverse slippage is explicit
  per side. Commission/swap are explicit assumed R charges. A spread assumption
  of zero is never substituted for observed bid/ask.
- Holding duration starts at simulated fill time, with exit at the first quote
  at/after the deadline. This is a new common observation policy, **not** H-003's
  old floored bar-time exit. It does not change that execution policy.
- Sparse quote paths remain approximate; gaps beyond the bound, missing/late
  entries and unmatured paths are coverage failures. An unresolved selected
  position reserves capacity through the study end, conservatively preventing
  look-ahead-based reuse of its unknown exit.
- Every arm has the same normalized initial risk, max concurrent positions,
  same-symbol exclusion and deterministic timestamp/ID tie order. Risk/exposure
  blocks are separate from model skips. Portfolio differences due to prior
  selections are part of the treatment; exposure constraints themselves are common.
- Reports show net assumed-cost R, closed-equity drawdown, daily paired differences
  against the rule and between memory treatments, and exploratory UTC-day bootstrap
  intervals. Closed-equity drawdown omits unrealized intratrade drawdown. Adjacent
  days can be dependent; the interval is not proof of an edge. Each opportunity in
  a day's portfolio contributes to its entry day's return, including zero for
  explicit skips and exposure blocks. Missing decisions invalidate completeness.
- Meeting proposed minimum samples produces `review_required`, never pass,
  promotion, resumed trading or an adopted policy. Positive net after costs,
  incremental value against the rule, stable results across days/sessions and
  drawdown within the predeclared bound all require reviewer assessment.
  `frozen_criterion_checks` separately shows positive net, the frozen closed-
  drawdown bound and incremental-mean threshold. Meeting a numeric threshold is
  not statistical significance or permission to promote a strategy.

## Record contract and limitations

Observation records use `schemas/demo-evaluation.schema.json`, separate from
the LEAN backtest schema. They retain the common report fields and are always
`technical_only=true`; dirty-code audits also set `reproducible=false`. No claim
of strategy validity follows from a successful test or schema validation.

Open prerequisites: reviewed source/quote collector, model invocation isolation,
actual commission/swap and slippage calibration, full-ledger verification,
currency-factor exposure, intratrade drawdown and a reviewer-approved future
evaluation window/thresholds. Existing results are discovery data only.
