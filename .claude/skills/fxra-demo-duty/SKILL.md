---
name: fxra-demo-duty
description: Use when starting or ending a duty shift on the fx demo agents (Claude or Codex), reviewing demo trades, writing lessons, changing the H-002 policy, or comparing results between AI models.
---

# Demo duty shift (Claude and Codex)

Canonical rules: `AGENTS.md`, D-30/D-31 in `docs/decisions.md`, `docs/demo-agent.md`.
This skill is mirrored for Codex in `.agents/skills/fxra-demo-duty/`. Edit the
`.claude/skills` copy, then run `uv run fxra sync-skills`. A test fails if the
copies differ.

## Who is on duty

Default roster (`config/duty.example.toml`, local time Asia/Ho_Chi_Minh):
Codex (`gpt-6-sol`) 08:00-18:00, Claude (`claude-opus-5-5`) 18:00-08:00.
`config/duty.toml` overrides the roster. When someone covers outside their
shift:
`uv run fxra demo-shift take --agent <claude|codex|other> --model <model-id> --hours <h> --reason "<why>"`.
Any agent or model may be on duty. Use your real model id, because results are
compared per model.

## Shift start (in order)

1. `uv run fxra demo-shift status` shows who is on duty and the model
   scoreboard. If it is not you, take over with a reason, or stop.
2. ai-memory (workspace `personal-development`, project `fx-research-agent`):
   list and accept the latest handoff, then verify it against Git.
3. Hindsight bank `fxra`: recall `demo lesson` (tags `demo`, `lesson`, and
   `model:<id>`) and the latest shift summaries.
4. Check the loop: open http://127.0.0.1:8765. If it does not answer, start it
   from the repository root in a terminal that is not sandboxed (MT5 IPC):
   `uv run fxra demo-run`. If MT5 errors appear, the terminal needs a login or
   Algo Trading on; tell the owner.
5. `uv run fxra demo-shift start --summary "<one line: state you found>"`

## During the shift (about every hour)

- `uv run fxra demo-lesson` lists trades and lessons not yet in Hindsight.
  `uv run fxra demo-policy` lists stats by pattern, hour and session.
- For each closed trade without a narrative lesson, retain one to Hindsight with
  tags `demo`, `lesson`, the strategy id and `model:<your-model-id>`. Then run
  `uv run fxra demo-lesson --mark-retained <id> <doc>`, or `--add "<text>" --trade <pid> --author <agent>/<model>`.
- Change the policy only with a reason grounded in the stats:
  `uv run fxra demo-policy --set key=value --reason "..." --author <agent>/<model>`.
  Fewer than 10 samples is noise; prefer noting an observation.
- Never raise lots beyond `config/risk.toml`, remove stops, or chase the
  50 USD/day KPI. Account limits belong to the owner.

## Shift end

1. `uv run fxra demo-shift end --summary "<trades, net, policy changes, open issues>"`
2. Hindsight: retain a shift summary. Include the scoreboard lines for your
   model and what you learned (tags `demo`, `shift`, `model:<id>`). Hindsight
   holds the trading knowledge.
3. ai-memory: `memory_handoff_begin` with `shared: true` covering code and doc
   changes, what is running and next steps. ai-memory gets development state only,
   never trade results.
4. If you changed code or docs, commit to a branch and open or update a PR.
   Keep `docs/demo-agent.md` and this skill current.

## Model comparison knowledge

`fxra demo-shift status` reports, per model on duty, the trade count, win rate,
net, profit factor and average pips after an assumed cost. It also reports the
same by policy author, and lesson and policy-change counts. Results also depend
on the market of that shift's hours (Codex covers London/NY open, Claude covers
Asia/late NY), so compare per strategy and session, not only per model. Small
samples (n < 30) say little.
