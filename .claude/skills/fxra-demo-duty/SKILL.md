---
name: fxra-demo-duty
description: Use when starting or ending duty on the fx demo agents (Claude or Codex), reviewing demo trades, writing lessons, changing your H-002 arm's policy, proposing or reviewing a rule with the other model, or comparing results between AI models.
---

# Demo duty (Claude and Codex learn together)

Canonical rules: `AGENTS.md`, D-30..D-33 in `docs/decisions.md`, `docs/demo-agent.md`.
This skill is mirrored for Codex in `.agents/skills/fxra-demo-duty/`. Edit the
`.claude/skills` copy, then run `uv run fxra sync-skills`. A test fails if the
copies differ.

## Roles (D-33)

- **Codex** (`gpt-6-sol`) is primary 24/7. It owns operations: the loop, MT5
  problems and hourly review.
- **Claude** (`claude-opus-5-5`) is co-duty 18:00-08:00 local
  (Asia/Ho_Chi_Minh). The owner cannot use Claude 08:00-18:00.
- Each model owns one H-002 arm on the same market:
  - `H-002-claude`, magic `20261029`
  - `H-002-codex`, magic `20261030`

  You change **only your own arm's** policy:
  `uv run fxra demo-policy --arm <yours> --author <agent>/<model> --reason "..." [--set k=v] [--enable p] [--disable p]`.
- Pattern statistics are pooled across arms: shared knowledge. Arm-vs-arm
  results show whose judgment works better on the same bars.
- Roster: `config/duty.toml` (default `config/duty.example.toml`). Cover outside
  the schedule with
  `uv run fxra demo-shift take --agent <a> --model <m> --hours <h> --reason "..."`.

## Start (when you come on duty)

1. `uv run fxra demo-shift status` shows who is on duty, the arm and model
   scoreboard, and proposal status.
2. ai-memory (workspace `personal-development`, project `fx-research-agent`):
   list and accept the latest handoff, then verify it against Git.
3. Hindsight bank `fxra`: recall `demo lesson` and the other model's latest
   hand-over summary (tags `demo`, `shift`, `model:<other-id>`).
4. Review the other agent's open proposals: `uv run fxra demo-propose` lists
   them. Then review each:
   `uv run fxra demo-propose --review <id> --agree|--reject --note "<your evidence>" --author <agent>/<model>`.
   Check against your own arm's numbers, not just theirs.
5. Check the loop at http://127.0.0.1:8765. If it does not answer, restart it
   unsandboxed with
   `powershell -NoProfile -ExecutionPolicy Bypass -File D:\Work\codex\fx-research-agent\artifacts\demo\run-demo-agent.ps1`.
   If MT5 errors appear, the terminal needs a login or Algo Trading on; tell
   the owner.
6. `uv run fxra demo-shift start --summary "<one line: state you found>"`

## About every hour

- `uv run fxra demo-lesson` lists trades and lessons not yet in Hindsight.
  `uv run fxra demo-policy` shows policies and stats by pattern, arm,
  hour and session.
- For each closed trade without a narrative lesson, retain one to Hindsight
  with tags `demo`, `lesson`, the arm and `model:<your-model-id>`. Then run
  `uv run fxra demo-lesson --mark-retained <id> <doc>`.
- Tune your arm with a reason grounded in the stats. Fewer than 10 samples is
  noise; note an observation instead.
- To turn an observation into a rule that both arms should follow, run
  `uv run fxra demo-propose --text "..." --evidence "<arm, pattern, session, n, avg pips>" --author <agent>/<model>`.
  The other agent decides.
- Never raise lots beyond `config/risk.toml` or remove stops. Never chase the
  50 USD/day KPI. Account limits belong to the owner.

## Hand-over (Claude at 08:00, Codex at 18:00 summary)

1. `uv run fxra demo-shift end --summary "<trades, net, policy changes, proposals, open issues>"`
2. Hindsight: retain a hand-over summary with tags `demo`, `shift` and
   `model:<id>`. Cover:
   - the scoreboard lines for your arm;
   - what you learned;
   - what you want the other model to check.
3. ai-memory: `memory_handoff_begin` with `shared: true`, covering code and doc
   changes, what is running and next steps. ai-memory gets development state
   only, never trade results.
4. If you changed code or docs, commit to a branch and open or update a PR, and
   ask the other agent to review. Keep `docs/demo-agent.md` and this skill
   current.

## Comparing models

`fxra demo-shift status` and the dashboard show three views:

- Arm vs arm (same bars, so the fairest comparison), also per session.
- Model on duty at entry.
- Policy author.

Each view reports n, win rate, net, profit factor and average pips after an
assumed cost. Samples below 30 say little.
