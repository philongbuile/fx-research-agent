# Simulation implementation and verification plan

Owner: the repository owner decides policy and deployment approvals; Claude and Codex implement through branches/PRs under `AGENTS.md`. Status reflects the current code and local checks, not proof of deployed integrations. Roadmap ordering is in [roadmap.md](roadmap.md).

## Reading and handoff protocol for both coding agents

Read `AGENTS.md`, architecture and decisions first; then [design](simulation-design.md), this plan and the roadmap when working on memory/simulation. `CLAUDE.md` imports the same canonical rules. Update task status and decision links in these documents when behavior changes.

When ai-memory tools are available, use the actual discovered tool schemas with workspace `personal-development` and project `fx-research-agent`. Retrieve the current scoped development handoff, verify it against Git, and publish a curated handoff at the end: implementation paths, validation, limitations and next steps. Link this plan and decision IDs instead of copying the full specification. Never transfer character memory or secrets into that handoff.

At present ai-memory is not installed/connected. Repository instructions provide the shared reading path for both clients; they are canonical documentation, not a claim that an external memory write succeeded. Do not use Serena memory, Hindsight or another local memory system as a substitute. Activation requires Q-09 and the real Claude↔Codex acceptance test in the runbook.

## Delivered foundation

- [x] F-01 Separate development and character/research memory responsibilities; pin an ai-memory review candidate and prepare the project marker without installing third-party code.
- [x] F-02 Persist identities/original personality, independent life/employment, events and separate financial ledgers in a controller-owned SQLite database.
- [x] F-03 Apply controller-evidenced KPI review, dismissal, reassessment/rehire and the strict debt termination condition. Leave unchosen business rules disabled.
- [x] F-04 Persist departure snapshot and revocation atomically; retain original final outputs with author/time and explicit missing-message behavior.
- [x] F-05 Restrict final capabilities, enforce a persisted deadline/output budget, recover after restart and release messages at simulation publication time.
- [x] F-06 Queue personal/professional narratives continuously, use immutable Hindsight document IDs and select reads by identity/time with original-content integrity checks.
- [x] F-07 Store evidence/status revisions, skill metadata and unfinished work for successor handoffs without importing personal experience.
- [x] F-08 Provide `simulation-demo`, `simulation-recover`, policy examples and SQLite backup support.
- [x] F-09 Exercise the synthetic flow and regression suite locally. The baseline suite has 85 passing tests; this is software verification, not evidence of trading edge.

Implementation: `src/fxra/simulation.py`, `simulation_memory.py`, `simulation_demo.py`, CLI integration and `tests/test_simulation.py`. The offline backend is explicitly an ephemeral test double.

## Next executable work packages

| ID | Work | Dependency | Completion evidence |
|---|---|---|---|
| V-01 | Identify the actual Hindsight host; apply replacement API/UI credentials without changing the database password | Host location/access | Old API credential rejected, new credential accepted, bank and stored data preserved; no credentials in logs |
| V-02 | Exercise authenticated synchronous retain and exact-document read against deployed Hindsight | V-01; existing approved server | Sanitized transport results, restart/retry evidence and simulation isolation checks |
| V-03 | Coordinate SQLite/Hindsight backup and restore into an isolated target | V-02 | Same simulation UUID, event history, pending departure and document integrity; expired sessions recovered |
| A-01 | Complete ai-memory artifact/digest, dependency/license and permission review; obtain explicit installation approval | Q-09 | Approved review note for an exact artifact and installation scope |
| A-02 | Configure the two coding clients on the same authenticated development backend using discovered schemas | A-01 | Same explicit workspace/project; no character endpoint or automatic transcript capture |
| A-03 | Run Claude→Codex→Claude canary and claim-once handoff tests plus wrong-scope checks | A-02 | Actual client versions and sanitized canary/handoff evidence, not only parsed configuration |
| P-01 | Specify KPI, schedule and missed-review behavior | Q-10 | Owner-approved policy with evidence source and boundary examples |
| P-02 | Specify learning/reassessment eligibility, reviewer and waiting period | Q-11 | Owner-approved rehire cases and rejection cases |
| P-03 | Specify initial balances, personal debt creation/repayment, salary/cost/interest schedules | Q-12 | Owner-approved separate-ledger examples; no implicit trading-loss conversion |
| R-01 | Implement a real character runner with identity-bound tools and no direct filesystem/network-memory access | V-02; explicit runner design review | Integration tests prove unauthorized operations cannot reach controller/storage |
| R-02 | Add an independent lifecycle scheduler and final-writer process supervision | R-01 | Stalled/terminated agent cannot extend the persisted deadline or block finalization |
| R-03 | Execute a small multi-character simulation using the approved policies | P-01..P-03, R-02 | Restartable event trace, isolated memories, explicit handoffs and absent-message paths |
| L-01 | Define a LEAN replay bridge with frozen hypothesis/run IDs and knowledge cutoffs | R-03; existing research rules | No future knowledge, preserved trial records, costs/provenance linked to actual `run.json` |

Local infrastructure update (2026-10-27): Docker is running; the engine was empty before bootstrap. A new local deployment was restored from the existing shared archive, with a pre-restore archive preserved. Replacement keys are active locally; missing/wrong keys are rejected, but the lost original key and the other host could not be tested. V-02's actual retain/read, identity/time filtering, MCP allowlist and restart persistence checks pass locally. The backup task produced a new checked archive and copied it to the shared folder. Details and remaining limitations are in the [verification runbook](memory-and-simulation.md#verification-in-this-environment).

V-01 remains open for the other host and canonical-write policy (Q-13); V-03's coordinated SQLite/Hindsight restore remains open. Do not overwrite an existing database or claim live replication merely because both hosts sync archive files.

## Required acceptance matrix

| Requirement | Current evidence | Remaining deployed proof |
|---|---|---|
| Exact debt boundary: equal does not terminate, greater does | Local regression tests | Approved economic policy used by real runner |
| Dismissal preserves identity/history; learning and rehire work | Local regression tests | Real agent reassessment flow |
| Termination is irreversible, including stale capabilities | Local regression tests | Runner/sandbox capability revocation |
| Company/trading/personal balances remain independent | Local regression tests | Approved debt/expense schedule |
| Duplicate events and lost acknowledgements are safe | Local transaction/outbox tests and HTTP fixture; real synchronous retain/read passes | Real server failure/retry |
| Interrupted departure/final submission resumes atomically | Local crash/restart tests | Kill supervised writer and restart host |
| Missing farewell remains missing | Demo and regression tests | Actual agent unavailable/stalled path |
| Personal memories remain isolated; successor starts fresh | Local dispatcher/document tests | Multi-agent runner with denied raw endpoints |
| No future knowledge or later-message leakage | Local revision/cutoff/release tests | LEAN replay bridge |
| Coding clients share development memory and handoffs | Prepared marker and documentation only | A-03, both clients |

## Delivery and stop conditions

Run the existing suite before proposing a change, verify the diff excludes protected gates and secret files, push a feature branch and use a PR. This change is authorized for commit/push/merge by the owner. Wait for CI; do not bypass failed checks or merge protections. A missing third-party installation approval blocks activation, not documentation or the existing offline foundation.

Update this plan with actual results after each work package. Keep unverified claims explicit. Never mark a roadmap milestone complete merely because its setup instructions exist.
