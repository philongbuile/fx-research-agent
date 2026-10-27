# Research and simulation roadmap

This roadmap orders outcomes by dependency, without speculative calendar dates. Existing LEAN/data/MT5 migration tasks remain in [backlog.md](backlog.md); implementation-level simulation tasks are in [simulation-plan.md](simulation-plan.md). Accepted product behavior is in [simulation-design.md](simulation-design.md), and policy decisions are in [decisions.md](decisions.md).

| Milestone | Status | Scope | Exit gate |
|---|---|---|---|
| S0 — Durable synthetic foundation | Implemented locally | Separate memory responsibilities, controller-owned life/job/ledgers, continuous journal/outbox, time-scoped reads, recoverable handoff, bounded final capability and offline demo | Regression suite and demo pass; deployed/runner limitations remain explicit |
| S1 — Verified memory infrastructure | Pending host access and approval | Restore authenticated Hindsight access, verify actual document transport/restore, approve and activate ai-memory, test both coding clients | V-01..V-03 and A-01..A-03 pass with sanitized evidence |
| S2 — Agreed business policy | Owner decisions pending | KPI/review schedule, reassessment/rehire, personal economy and initial balances | Q-10..Q-12 settled with testable examples and versioned policies |
| S3 — Supervised character agents | Not implemented | Real runner, tool/permission isolation, independent expiry scheduler, process timeout, small multi-character exercise | R-01..R-03 demonstrate that agents cannot alter controller state or bypass memory isolation |
| S4 — Reproducible research replay | Future | Connect simulation chronology to LEAN runs and evidence-qualified professional learning | L-01 proves temporal eligibility, provenance and hypothesis/trial accounting; no claim of edge from fixtures or in-sample results |
| S5 — Research operations and optional experience layer | Future, separately scoped | Migration exercises, observability and long-run recovery; optional character-life interface if requested | Operational recovery proven first; any UI/game/economic expansion has separate requirements and review |

S1 verification and S2 policy discussions can proceed independently. S3 requires functioning character memory and agreed policy before an economic exercise is treated as representative. S4 builds on the existing research loop rather than replacing LEAN or its run records.

## Workstream boundaries

- Development memory: ai-memory handles coding context/handoffs only; both Claude and Codex must pass real shared-scope tests. Its installation is not approved merely by merging this roadmap.
- Character memory: Hindsight retains personal/professional narratives; the controller registry governs identity, chronology, evidence and status. Successors do not inherit another identity's lived experience.
- Infrastructure: use the current Hindsight host or the planned MT5 laptop, preserving existing data. Replacement access credentials alone do not constitute a deployed service or a successful rotation.
- Trading research: continue the existing data-quality, cost, OOS and promotion rules. This roadmap authorizes neither strategy selection nor stage promotion, risk-gate changes or broker order sending.
- Product breadth: start with the working simulation flow. A full life/game system, automatic economic model and UI are not prerequisites and are not part of the delivered foundation.

## Risks to resolve before advancing

| Risk | Mitigation / evidence |
|---|---|
| A lost credential is mistaken for lost data | Rotate access keys on the actual host; retain the database password/volume and verify preserved records |
| Documentation/configuration is reported as integration success | Require real Hindsight and both-client ai-memory canaries |
| A Python object allowlist is mistaken for an OS sandbox | Supervise actual agents with no raw storage, shell, Python or memory endpoint access |
| A final writer stalls or dies after revocation | Persist the deadline, run expiry independently, preserve the no-message outcome |
| Professional guesses become inherited instructions | Retain evidence/status; only reviewed validation changes trust annotations |
| A later discovery reaches an earlier backtest | Gate immutable documents/revisions/messages by controller publication time and test replay |
| Memory persistence and controller snapshots diverge | Test coordinated SQLite/Hindsight restore and fail closed on missing/mutated documents |

## Updating the roadmap

Change status only with linked implementation and verification evidence. Put newly settled business requirements in `decisions.md`; leave unanswered items open. Both coding agents read the same canonical documents through `AGENTS.md` and the `CLAUDE.md` import. Once ai-memory is active, curated development handoffs should link milestone/task/decision IDs rather than reproduce these documents.
