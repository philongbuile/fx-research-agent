# Demo lessons so far (H-001B, H-002, start of H-003)

Curated for the models that decide H-003 candidates (D-42, D-43). Read it
before deciding; `.claude/skills/fxra-demo-decide/SKILL.md` has the short
checklist that points here.

**What this is and is not.** Every number below is an in-sample observation
from about one trading day on an MT5 demo account. The patterns, filters and
buckets were chosen after looking at this data. Nothing here is evidence of
edge, and none of it may be quoted as a validated rule. It is the best summary
we have of what happened, so a model can weigh a candidate with it and then
test it on candidates that arrive later.

## Snapshot and method

- Source: the live journal `artifacts/demo/journal.sqlite` (read-only),
  closed H-002 trades whose close time is before **2026-10-29 16:00Z**. That is
  1267 trades on entry bars from **2026-10-28 15:00Z to 2026-10-29 15:45Z**
  (one Wednesday afternoon and night, one Thursday Asia/London/NY morning).
  Positions the owner closed by hand at about 16:01-16:04Z when H-002 was
  stopped (`owner_close`) are excluded.
- **Events, not trades.** Both H-002 arms and every pattern that fired on the
  same bar the same way opened copies of the same trade. One *event* is one
  (symbol, entry bar, side), exactly as `fxra demo-playbook` counts it
  (`playbook.event_key`). 1267 trades are only **520 events**.
- **R** is the result in units of the planned stop distance, after an assumed
  0.8-pip cost (the demo feed has almost no spread). "Mean R" is the mean over
  events of each event's mean R. Win rate and net USD are per event (net is
  summed over its copies, so it scales with how many copies were opened).
- Pattern families used below:
  - **trend/breakout**: breakout10, breakout20, inside_bar_break,
    three_bar_momentum, macd_cross, momentum_candle, ema_pullback,
    ema_cross_9_21, rsi_cross_50, bb_squeeze_break;
  - **reversion**: ema50_stretch_fade, pin_bar, failed_breakout_fade,
    bb_rsi_reversion, rsi_extreme_fade;
  - **engulfing** on its own (it can point either way and has its own frozen
    study, proposal 4).
- Sample labels as in the digest: under 10 events is noise, under 30 weak.
  Hour and session cuts each rest on **one** occurrence of that hour.
- To recount, use `uv run fxra demo-playbook` (rules recount live) and
  `uv run fxra demo-insights` (per trade, not per event). Numbers in this file
  are a frozen snapshot and will not match later recounts exactly.

## Overall

| Slice | Events | Trades | Win | Mean R | Net USD |
|---|---:|---:|---:|---:|---:|
| All H-002 | 520 | 1267 | 40% | -0.16 | -4,747 |
| Without FOMC bars (10-28 18-19h) | 476 | 1089 | 41% | -0.14 | -2,527 |
| Filled on 10-28 (UTC) | 131 | 426 | 34% | -0.32 | -6,316 |
| Filled on 10-29 (UTC) | 389 | 841 | 42% | -0.11 | +1,569 |

With a 1.5R target the break-even win rate is about 40% before costs, so
the explorer as a whole sat just below break-even and lost on costs and
clustering. Exits: stop loss 288 events (-1.11R), take profit 166 events
(+1.35R), time exit after 8 bars 66 events (+0.19R).

H-001B (frozen EURUSD control) closed 1 trade (-1.60 USD): nothing to learn.

## What to weigh when deciding a candidate

These are the observations with the most support. Each says how strong it
is. Use them as reasons for or against, not as rules.

### 1. The pattern family is the strongest signal we have

| Family | Events | Mean R | 10-28 bars | 10-29 bars | Net USD |
|---|---:|---:|---:|---:|---:|
| trend/breakout | 314 | -0.34 | -0.53 (76) | -0.28 (238) | -9,833 |
| reversion | 185 | +0.17 | +0.00 (60) | +0.25 (125) | +5,633 |
| engulfing | 102 | -0.13 | -0.34 (30) | -0.04 (72) | -547 |

(Events can count in more than one family when patterns of different
families agreed on the same bar.)

- Trend/breakout entries lost on both days and on both arms. Six patterns
  were negative on **both** days: breakout10 (79 events, -0.61R), breakout20
  (56, -0.66R), inside_bar_break (34, -0.50R), macd_cross (69, -0.42R),
  ema_pullback (80, -0.39R) and three_bar_momentum (84, -0.35R). Together:
  274 events, -0.41R (playbook rule recount). Entering at the close of a
  breakout bar on M15 tended to buy the top.
- Near flat, sign changed between days: rsi_cross_50 (82, -0.08R; -0.42 then
  +0.06), momentum_candle (39, -0.11R), ema_cross_9_21 (26, +0.01R, weak).
- Reversion: ema50_stretch_fade was positive on both days (61 events,
  +0.23R; +0.35 then +0.14). pin_bar (75, +0.17R) and failed_breakout_fade
  (64, +0.02R) were negative on 10-28 and positive on 10-29: regime dependent
  at this sample size. bb_rsi_reversion (9 events) and rsi_extreme_fade (1)
  are noise.
- Engulfing (102 events, -0.13R) is under the agreed prospective study
  (proposal 4, window to 2026-11-05). Treat it as unproven either way.
- One exception worth knowing: on GBPUSD even trend patterns were positive
  (40 events, +0.16R). One day, one symbol.

### 2. An opposing pattern on the bar is a warning for trend entries

The D-37 agreement buckets, by family:

| Bucket | trend/breakout | reversion |
|---|---|---|
| solo | 66 ev, -0.24R | 47 ev, +0.09R |
| solo · opposed | 21 ev, -0.70R | 68 ev, +0.27R |
| 2 agree | 89 ev, -0.28R | 30 ev, +0.09R |
| 2 agree · opposed | 22 ev, -0.75R | 17 ev, +0.35R |
| 3+ agree | 85 ev, -0.10R | 18 ev, +0.11R |
| 3+ agree · opposed | 31 ev, -0.86R | 5 ev, noise |

- A trend/breakout signal with any pattern pointing the other way: 74
  events, -0.78R (playbook recount). The opposing signal was usually a fade,
  and the fade side tended to be right. "3+ agree · opposed" was the worst
  bucket overall (34 events, -0.77R, win 18%, -5,837 USD) and it was equally
  bad before (7 events, -0.80R) and after (27 events, -0.77R) the first
  playbook rules were written on 2026-10-28 19:26Z.
- A reversion signal that is opposed by trend patterns is normal (a fade
  fires against momentum by design) and did not hurt.
- **Correction to earlier notes:** "three or more agreeing patterns did worse
  than one" is not supported. Plain "3+ agree" (no opposition) was -0.10R on
  92 events, the same as solo (-0.10R, 145 events). What hurt was
  *opposition*, and *stacking* (next point).

### 3. Stacking multiplied losses without adding information

- H-002 opened one position per pattern per arm, so one idea often became
  3-8 positions. Mean R barely changed with the number of copies (trend: 1
  position -0.30R, 2 -0.40R, 3+ -0.33R), but USD did: trend events with 3+
  copies were 130 events, 472 trades, -5,894 USD, about 60% of the trend
  loss.
- Correlated symbols are the same idea too. On 10-28, bars where three or
  more USD pairs were entered in the same USD direction lost -0.42R over 45
  events; on 10-29 the same situation was -0.04R over 119 events. So it is a
  concentration risk, not a reliable signal: if it goes wrong, it goes wrong
  everywhere at once (the FOMC lesson).
- In H-003 each candidate is already one per symbol, bar and side. What
  remains for the model: do not add a second position on a symbol where your
  arm already holds the same idea, and count several USD pairs in the same
  direction as one bet.

### 4. Stops at the 5-pip floor lost, mostly for trend entries

| Planned stop | All | trend/breakout | reversion |
|---|---|---|---|
| 5-pip floor | 141 ev, -0.34R | 82 ev, -0.61R | 51 ev, +0.08R |
| 5-8 pips | 189 ev, -0.09R | 117 ev, -0.32R | 68 ev, +0.25R |
| 8-12 pips | 118 ev, -0.10R | 72 ev, -0.21R | 40 ev, +0.15R |
| 12+ pips | 72 ev, -0.09R | 43 ev, -0.11R | 26 ev, +0.17R |

- The floor applied when ATR was small. Within trend entries it was about
  -0.6R in low, normal and high volatility alike, so it is not only a
  quiet-market effect. At a 5-pip stop the 0.8-pip cost alone is 0.16R.
- H-003's default is already max(1.5 x ATR, 8 pips). A wider stop does not
  turn a bad entry into a good one: trend entries with 12+ pip stops were
  still -0.11R.

### 5. Thin opens and the FOMC hour

Each of these is one occurrence. Treat them as anecdotes that point at a
mechanism, not as time filters.

- **22-23h UTC (Sydney/Tokyo open, rollover)**, bars of the night of 10-28
  only: trend 26 events -0.77R (win 15%), reversion 11 events +0.25R,
  engulfing 9 events -0.02R. Spreads widen and the first moves often reverse.
- **06-07h UTC (Frankfurt/London pre-open)**, 10-29 only: trend 37 events
  -0.79R (win 14%), reversion 22 events +0.06R.
- Together, trend entries in 22, 23, 06 and 07h: 63 events, -0.78R.
  Reversion was flat to positive in the same hours.
- Best reversion stretch: 08-11h UTC on 10-29, 37 events +0.59R; 00-05h
  UTC on 10-29, 43 events +0.28R.
- **FOMC, 2026-10-28** (statement 18:00Z, press conference from about
  18:30Z): bars at 18-19h had 66 events. breakout10, breakout20 and
  ema_pullback had zero wins (8, 8 and 11 events). Not every fade worked:
  ema50_stretch_fade won (11 events, +0.33R) but failed_breakout_fade lost
  every time (10 events, -1.11R), and reversion as a family was -0.45R in the
  18h bar and +0.09R in the 19h bar. **Correction to earlier notes:** "mean
  reversion won in the FOMC hour" was true only for the stretch fades. The
  first 30-60 minutes after a major release ran over most technical entries.
  Equity also went flat during the press conference (frozen quotes), then
  swung about 4.4k USD in 16 minutes (Hindsight
  `fxra-lesson-claude-arm-usd-cluster-2026-10-28`).

### 6. Sessions and symbols

| Session (bars) | Events | Mean R | Net USD |
|---|---:|---:|---:|
| asia (10-29 only) | 194 | -0.11 | -159 |
| london (10-29 only) | 136 | -0.04 | +277 |
| overlap_ny (both days) | 75 | -0.30 | +453 |
| late_ny (10-28 only, incl. FOMC) | 115 | -0.30 | -5,318 |

| Symbol | Events | Mean R | Note |
|---|---:|---:|---|
| GBPUSD | 62 | +0.17 | positive before and after 10-28 19:26Z; trend +0.16, reversion +0.56 |
| EURJPY | 45 | +0.01 | from 10-28 ~19:45Z (D-41) |
| EURUSD | 57 | -0.01 | |
| AUDJPY | 47 | -0.15 | reversion +0.59 (15 ev), trend -0.47 (26 ev); D-41 |
| GBPJPY | 51 | -0.14 | reversion -0.16 (20 ev); D-41 |
| USDJPY | 56 | -0.20 | |
| XAUUSD | 55 | -0.23 | 0.1 lot; trend -0.40, reversion +0.27 |
| AUDUSD | 60 | -0.32 | |
| NZDUSD | 47 | -0.38 | trend -0.79 (31 ev); D-41 |
| EURGBP | 40 | -0.47 | lost in both families (reversion -0.54, trend -0.55); D-41 |

The D-41 pairs only traded from about 19:45Z on 10-28, so they saw one night
and one morning. EURGBP is the most consistent loser (quiet pair, small ATR,
often the 5-pip floor); GBPUSD the most consistent winner.

### 7. News: no evidence yet

The MT5 calendar export (D-40) was never running: the journal has no stored
events and every entry is tagged `event=none`. The news patterns never
traded. The candidate's `event` field is therefore not informative yet. The
only news lesson is the FOMC one above; check `uv run fxra demo-calendar`
and a public calendar yourself before a candidate near a known release.

### 8. Reading the numbers a candidate shows

`demo-decide` shows per-pattern stats as **raw trades and average pips after
cost**, pooled over both arms. They double count copies and mix symbols with
different pip values. Use them as a direction, and prefer the event-R figures
here and in `fxra demo-playbook`.

## The two arms and H-003

- **H-002 arms did not measure model judgment.** Before the Codex pilot
  (2026-10-29 04:12Z) the arms ran almost the same policy and scored the same
  per family: trend -0.43R (Claude, 148 events) vs -0.41R (Codex, 120);
  reversion +0.15R (87) vs +0.13R (83). Arm differences came from enabled
  patterns and from position capacity, not from the model.
- **Codex selective pilot** (owner exception, from 04:12Z; ema50_stretch_fade,
  bb_rsi_reversion, engulfing): 73 events, -0.14R up to the snapshot. Too
  young to judge, and it confounds proposals 4-6.
- **Claude arm** disabled five trend patterns at 15:05Z (policy v8) and the
  owner stopped the scripted Claude arm at about 16:04Z (v9, no patterns).
  The playbook counts only H-002 trades, so rules about trend patterns will
  gather little or no new evidence from H-002 from now on. Their prospective
  test is the H-003 shadow outcome of candidates led by those patterns.
- **H-003** started with the 15:30Z bar on 2026-10-29. By about 16:05Z there
  were 61 candidates: Claude took 6, skipped 14 and let 21 expire; Codex's
  automation was not running yet (10 expired, 10 pending). No shadow outcome
  existed yet. Nothing can be concluded about the model filter.

## Open questions to test prospectively

Each needs events that arrive after 2026-10-29 16:00Z, counted as unique
events across at least three UTC dates and two sessions. Do not tune on
interim results; a change of design is a new hypothesis id.

1. Do candidates led by the six losing trend patterns keep a negative shadow
   R, and does the model's skip of them beat taking them? (Playbook rule
   "avoid six trend/breakout patterns".)
2. Do trend candidates with an opposing pattern keep losing? Is the fade side
   of such a bar worth taking? (Playbook rule "trend signal with an opposing
   pattern".)
3. Are thin opens (22-23h and 06-07h UTC) bad for trend entries on other
   nights, and neutral for reversion? (Playbook rule "trend at thin opens".)
4. Does ema50_stretch_fade stay positive, and do pin_bar and
   failed_breakout_fade (sign flipped between days) settle on either side?
   (Proposal 5 and playbook watch rule.)
5. Is engulfing negative over its frozen window? (Proposal 4, to
   2026-11-05.)
6. Does GBPUSD stay better than the rest, and EURGBP worse? (Playbook watch
   and avoid rules.)
7. With the calendar export running, how do the first 30-60 minutes after a
   high-importance release treat each family? What about the news patterns?
8. Do stops of 1.5-2 x ATR (H-003 default) remove the floor effect for the
   entries the model takes?
9. The real H-003 question: over the next days, does the shadow R of taken
   candidates beat passed candidates, per arm, on the same candidates?

## Related

- Decisions: D-30..D-43 in `docs/decisions.md`; the selective study in
  `docs/verified-pattern-selection.md`.
- Hindsight (bank `fxra`): curated theme documents `fxra-lessons-<theme>`
  (tags `demo`, `lesson`, `curated`), the post-mortem
  `fxra-h002-postmortem-20261029`, the FOMC lesson
  `fxra-lesson-claude-arm-usd-cluster-2026-10-28`, and the hourly digest
  `fxra-insight-*`. Recalled text is context; numbers come from the journal.
