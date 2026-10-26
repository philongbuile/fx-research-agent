# <H-id> <short title>

Copy this folder to `experiments/<H-id>-<short-name>/`. Fill every section before any run. Do not pick a pair, timeframe, broker or strategy here unless the owner has decided it (see `docs/decisions.md`).

## Id and title

<H-id, next free number>, <one-line title>

## Claim

<The market behavior you claim exists, stated so a result can contradict it.>

## Why it should exist

<The mechanism: who is on the other side of the trade and why they persist. No mechanism, no hypothesis.>

## Falsification test

<The result that kills it, written before running: metric, threshold, data range.>

## Costs it must survive

Listed in `research.toml` `[assumptions]`.

- Spread: <source and stress multiples>
- Slippage: <model>
- Commission: <model>
- Swap: <model>

## Data needed

- Source: <adapter/source>
- Symbols: <symbols>
- Resolution: <resolution>

## Ranges (fixed before any run)

- In-sample: <start> to <end>
- Out-of-sample: <start> to <end>, run once after the design is frozen
- Design freeze commit: <hash, filled at stage 4>

## Trial log

Every variant is a trial. Never delete failed runs.

| run_id | date | what changed | kind (in-sample / stress / neighborhood / oos) |
|---|---|---|---|
| <run_id> | <YYYY-MM-DD> | <change> | <kind> |

Trials so far: <n>

## Stage and gate decisions

| date | stage | decision (passed / failed) | by | reason |
|---|---|---|---|---|
| <YYYY-MM-DD> | <0-6> | <decision> | <who> | <why> |
