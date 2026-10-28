# H-001B: H-001 breakout rules on broker H1 bars, MT5 demo forward test

- **Parent:** H-001 (`experiments/H-001-eurusd-breakout/hypothesis.md`). The rules are unchanged: 24-bar close breakout, 2×ATR(14) stop, 10-bar opposite-channel exit, 24-bar maximum hold, one position, 0.01 lot.
- **Difference:** the input bars are the broker's native H1 bars from MT5, not hours aggregated from complete verified M1 data. MT5 M1 history regularly skips single quiet minutes, so H-001's strict rule would almost never signal. The input source changes, so this is a separate id.
- **Venue:** MT5 demo account, autonomous execution under D-30. Implementation: `src/fxra/demo_agent.py`, `STRATEGY_VERSION = "h001b-v1"`.
- **Question:** does live demo execution of these rules behave as specified (fills, stops, exits, costs)? What do realised spread, commission and swap look like per trade?
- **Not claimed:** edge. Forward demo results are a small, non-reproducible sample. They are not in-sample/OOS evidence for H-001 and must not be used to tune it.
- **Kill criteria for the demo run:** the risk gate's 1% daily loss limit, the owner's kill switch, or any execution behaviour that differs from the specification (record a postmortem).
