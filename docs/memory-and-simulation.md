# Development memory and character simulation

Product intent: [design](simulation-design.md). Execution status: [plan](simulation-plan.md). Milestone order: [roadmap](roadmap.md).

## Recover lost access credentials

Replacement API and UI keys can be generated without resetting an existing database. Preserve its database password and PostgreSQL volume during an in-place rotation. On 2026-10-27 this checkout's machine instead bootstrapped a new local Docker deployment: the engine had no existing containers, images or volumes. Its private, ignored `.env` uses the previously generated replacement API/UI keys and a new database password for the new volume. The keys are now active **on this local deployment only**; the other host's credentials were not changed.

Transfer that file using a private mechanism, and replace only `HINDSIGHT_API_KEY` and `HINDSIGHT_UI_ACCESS_KEY` in the host's ignored `.env`. Do not paste values into chat or shell commands. From `infra/hindsight`, recreate only the application container:

```powershell
docker compose up -d --no-deps hindsight
```

Update authorized client credentials through their existing private configuration. For an in-place rotation, verify that the old API key is rejected, the new key can access bank `fxra`, and existing records remain intact; then check the UI with its replacement key. The lost original key could not be tested here; missing and deliberately wrong keys were both rejected. Do not print tokens or a rendered Compose configuration containing secrets. The private pending file remains a bootstrap copy; remove it through the owner's normal secret-retention procedure when no longer needed.

If the database password is also lost, do not replace it with a random value in `.env` or delete/reinitialize the database volume. Recover the running deployment's private configuration or perform a separately reviewed PostgreSQL credential-reset procedure on the actual host. Lost access keys do not imply lost stored memories.

## Boundaries and storage

| Data | Location / owner | Reader |
|---|---|---|
| Architecture, technical decisions, coding progress and coding handoffs | ai-memory, workspace `personal-development`, project `fx-research-agent`; activation pending review | Claude and Codex coding sessions |
| Canonical project rules and decisions | `AGENTS.md`, `docs/decisions.md`, Git | Coding agents; memory holds links and context, not another authoritative copy |
| Simulation events, raw fixture results, balances, policy, identities, personality, knowledge revisions, handoff snapshots and original final outputs | SQLite selected by `--db`, normally `artifacts/simulation/*.sqlite` | Trusted simulation controller only |
| Personal experience narratives and professional narratives | Hindsight bank `fxra`, immutable simulation/document IDs | Identity-bound gateway only |
| Narrative delivery queue and original text for retry/integrity checks | Same SQLite database | Controller only; not a separate search/RAG store |
| Executable skills/procedures | Versioned source in Git, registry references with version/path/conditions/checks | Shared within a simulation; registry never executes retrieved text |
| Market data and actual backtest metrics | Existing `data/` and `runs/<run_id>/run.json` | Existing research tools; simulation records evidence references |

No automatic sync exists between ai-memory and Hindsight. Never retain character experiences, raw trading results, account data or final life reviews into development memory. Never retain coding progress or coding handoffs into Hindsight. Existing research memories remain in place; this change does not migrate or delete them.

## Run the minimal simulation

Normal setup remains Python 3.12+ and the existing dependency list:

```powershell
uv sync
uv run pytest
uv run fxra simulation-demo
```

The demo requires a new database path. To repeat without deleting an earlier exercise:

```powershell
uv run fxra simulation-demo --db artifacts/simulation/second-demo.sqlite
```

This runs a deterministic **synthetic lifecycle fixture**, not a backtest. There are no market results, order intents or broker calls. The default `OfflineDocuments` is an ephemeral test double; the JSON output identifies it. Its contents disappear at process exit. SQLite persists the exercise and pending handoff, and a `.backup.sqlite` copy is created. Do not treat offline delivery markers as production Hindsight delivery; start a new database for the real transport.

For real Hindsight document persistence, supply `HINDSIGHT_URL` and `HINDSIGHT_API_KEY` to the **trusted host environment** using your existing secret mechanism, then:

```powershell
uv run fxra simulation-demo --hindsight --db artifacts/simulation/hindsight-demo.sqlite
```

Do not paste credentials into a command, prompt, memory, transcript or tracked file. This transport uses existing bank `fxra`, synchronous `POST /v1/default/banks/fxra/memories` and exact `GET /v1/default/banks/fxra/documents/<id>`. Fields/routes were checked against upstream `v1.1.0` `hindsight-api-slim/hindsight_api/api/http.py`. Retain requires a successful synchronous acknowledgement; retrieval requires original document text. A server configured without document text will fail closed. HTTPS is required except on loopback. No Hindsight compose, bank permissions or existing MCP allowlist is changed.

Calls have bounded socket timeouts and response sizes; redirects are refused. A failure leaves delivery pending. Call `engine.flush_memory(backend)` after each accepted memory/knowledge event and from the host's retry loop, not only on exit. Same document IDs are reused on retries, so an interrupted acknowledgement does not create another logical memory. Every simulation has a persisted UUID, so separate exercises cannot collide. The host can retry after restart with `Simulation.open_existing(path)`.

## Authority, clocks and privacy

`Simulation` is trusted controller code. It owns money in integer USD cents and performs state/event/handoff writes in one `BEGIN IMMEDIATE` transaction with full SQLite synchronization. Reused event IDs accept identical inputs and reject changed payloads. Failed commands roll back their journal entry as well. Simulation time is timezone-aware and cannot move backwards; replay a historical run into its own simulation database. Do not import later knowledge and backdate it to an earlier event time. An external evidence timestamp cannot establish when the character learned it.

Life (`alive`, `ending`, `ended`) is separate from employment (`trader`, `dismissed`). Failed KPI review dismisses the worker and retains identity, personality and personal experience. A dismissed living character can learn. Rehire requires an explicit configured rule, evidence from a controller reassessment, and completion of its prior handoff. `require_trader` is the host check before any simulated trader action. There is no order-sending integration and no change to the existing risk gate.

Company cash/trading capital belong to `@company`. Personal cash/debt belong to a character. Trading loss never changes personal debt; an insufficient cash balance is rejected rather than converted to debt. Only explicit configured controller debt adjustments can create debt. Personal debt **strictly greater than USD 50** atomically revokes employment and moves life to `ending`; exactly USD 50 does not. `ending` and `ended` cannot be rehired or restored by repayment. Originals remain archived.

The host chooses identity and cutoff when it creates a `CharacterSession`. `agent_tools(session)` exposes only `read`, `remember`, `propose`. Neither identity nor cutoff is accepted from an agent. An agent can propose a namespaced hypothesis; only the controller can validate knowledge or change state. Knowledge revisions carry evidence event references and explicit `hypothesis`, `validated`, `failed`, `superseded` status. Only validated entries receive `trusted_guidance=true`; that is a registry annotation, not authority to execute a skill. The controller must review actual evidence before validation; this module cannot assess trading edge.

Personal reads select only that character's registered documents at or before the host cutoff. Shared reads select the latest eligible revision at that time, including failed/unverified material with status intact. Unconstrained Hindsight recall/reflect and derived cross-document knowledge are deliberately unavailable. Exact Hindsight text must match the registered original; externally mutated documents fail closed. Shared handoff packets include tasks and knowledge/skill references, never personal memories. A successor gets a fresh identity and empty personal history.

These are **tool/RPC boundaries**, not Python sandboxing. Run an actual character agent without a shell, Python REPL, database mount, repo access, raw Hindsight MCP, ai-memory connection or host credentials; bind only the dispatcher through your agent runner. Do not hand the session Python object to untrusted code. No LLM runner or OS sandbox is implemented in this increment; the demo exercises tool invocations deterministically. Coding sessions remain trusted development sessions and must never be reused as character sessions.

The host must keep secrets out of all inputs. Common credential-shaped payloads are rejected before persistence; this is defense in depth, not a complete detector for arbitrary account identifiers or personal text.

## Departure and interruption recovery

Revocation, departure reason/time and a snapshot of unfinished work/knowledge are committed together. Message generation occurs afterwards. On termination, `final_session(departure_id, backend)` creates a capability with only `read` and `submit`; it cannot trade, retain experiences, propose knowledge or alter any state. It can submit `cause_report`, `life_review`, `successor_message`, bounded by a total character budget and an absolute wall-clock deadline persisted at departure.

No agent process is invoked inside a transaction or awaited by the lifecycle engine. The host must run `recover()` at startup, on a regular independent scheduler tick, and after killing a stalled final writer. Tick at least once per second while running a final writer; terminate its process at the deadline. A restart uses the original deadline. The library also checks expiry on final reads/submissions. To recover a stopped host manually:

```powershell
uv run fxra simulation-recover --db artifacts/simulation/hindsight-demo.sqlite
```

The tick finishes expired departures even if the agent writes nothing. An identical submitted response can be retried after a lost acknowledgement; changed responses are refused after completion. The original output is immutable, with author and wall-clock creation time. Missing or blank last words are `no_message`; the engine never invents text. Final report and life review are private archive outputs for the owner/researcher; only the successor message is released to another character.

The controller explicitly calls `release_handoff(event_id, simulation_time, departure_id)` after completion/timeout. That makes final text visible at the correct simulation time, preventing a later-written message from appearing in an earlier backtest view. The message carries sender, departure time/reason and original text; it is personal testimony, never binding instructions. Without release, recipients see the original pending handoff snapshot, not later completion details.

## Configure unanswered business rules

`config/simulation.example.toml` leaves KPI, review schedule, rehire and debt mechanisms unset; these paths reject decisions until configured. The separate `simulation-demo.example.toml` contains synthetic fixtures only. `Policy.load` validates the table, and a database persists its policy so reopening with different rules is refused. Use a new simulation for changed rules until a reviewed policy migration exists.

The implemented opt-in rehire rule is a controller attestation referencing a recorded reassessment. The debt rule accepts explicit evidenced controller adjustments; there is no automatic salary, living expense, interest or loss-to-debt mechanism. The owner still needs to decide Q-10..Q-12. Final session limits are configurable operational limits, not trading rules.

## Activate development memory after approval

First approve [the review note](third-party/ai-memory.md) and complete its remaining artifact/dependency checks. No third-party code was installed for this change. Candidate source pin: `0e5488073d7073caa3181bbaf184102b215dcf66` (manifest version `3.2.8`). Do not substitute a `latest` binary/container. This Windows host currently has no `ai-memory` executable, and no ai-memory tools are connected to this session.

After an approved matching binary/service is available, consult that binary's `--help` and the pinned upstream [installation documentation](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/docs/install.md) before applying client settings. Upstream offers non-applying registration previews:

```powershell
ai-memory install-mcp --client claude-code
ai-memory install-mcp --client codex
```

Use the same authenticated loopback backend, the same user and `.ai-memory.toml` scope for both clients. Keep token configuration outside Git. Registration application can change user settings; do it only for these clients after review. Avoid `setup`, global hook installation, auto-upgrade and automatic bootstrap in this repo. No guessed tool schemas or token-bearing project MCP files are supplied.

For the initial MCP-only workflow, inspect the server's actual `tools/list` schemas. Every scoped call must explicitly name workspace `personal-development` and project `fx-research-agent`. Use upstream `memory_handoff_begin`, `memory_handoff_list`, `memory_handoff_accept` following the returned schemas; do not guess their argument names. Keep coding handoffs to code paths, decisions with links, work completed, failed approaches, tests and next steps. Only curate development content; do not mirror simulation/research narratives.

Acceptance test after activation:

1. Claude writes a harmless unique development canary in this scope and creates a manual handoff.
2. Start Codex in the same checkout. It must find that exact canary and handoff, then accept the listed handoff ID once. A second acceptance must fail or show already accepted.
3. Codex writes a second development canary/handoff; Claude finds and accepts it in the same scope.
4. A different project/workspace must not return these project-scoped canaries. Neither client may fetch character experiences through ai-memory.
5. Save sanitized evidence (client versions, selected scope and handoff IDs, no transcripts/credentials) in an ignored artifact. Configuration parsing alone is not a passing integration test.

The checked marker disables cross-project profile and excludes generated/sensitive paths and canonical decisions from capture as defense in depth. Exclusions do not provide complete prompt/output privacy. Automatic lifecycle capture remains disabled in the proposed deployment; manually curated MCP handoffs support continuity without importing all session text.

## Backup and restore

Both machines now target the same existing Drive archive folder (D-24). The installed additive sync task and its reusable source are documented in [shared backup sync](../infra/backup-sync/README.md). File synchronization does not merge running Hindsight servers or determine which database is authoritative. Verify the actual host, replacement credentials and preserved records before restoring anything.

- Simulation: `engine.backup(Path(...))` uses SQLite's consistent backup API. Copy that snapshot off the host with the existing backup workflow. Do not copy a database during a write. Restore to a new path, reopen with `Simulation.open_existing`, run recovery and verify journal/identity/departure contents before any agent session. Keep the persisted simulation UUID unchanged.
- Hindsight: use the existing `infra/hindsight/README.md` backup/restore procedure. The SQLite registry and Hindsight documents must both be backed up. Restore a coordinated pair; if Hindsight lost documents that SQLite marks delivered, re-retain the registered immutable originals through a reviewed controller recovery procedure before opening agents. Never silently substitute another character's documents.
- ai-memory after activation: stop its sole writer and back up the entire data directory, including wiki Git history, SQLite, config and protected authentication material. Restore into a private directory with the same approved binary, reapply file permissions and run the cross-client canary test. Do not assume copying only Markdown preserves open handoffs/index metadata. Exact upstream backup/reindex commands have not been verified here, so none are invented.
- Code/skills/config examples: back up through normal Git. Generated simulation databases and memory credentials stay ignored.

## Verification in this environment

Verified locally on 2026-10-27 after Docker Desktop installation:

- Pinned Hindsight and PostgreSQL images start from the existing Compose configuration, on loopback only. Existing volumes were absent before bootstrap; a pre-restore archive was taken before importing `hindsight-20261026T060702Z.zip` from the shared backup folder.
- The restored `banks`, `documents` and `memory_units` table counts match the archive manifest. Bank `fxra` is present. This comparison preceded synthetic verification writes.
- Health returns 200; a valid replacement API key is accepted; missing/wrong keys return 401. MCP initialization and tool listing work, with deletion/update/invalidation tools absent.
- Actual synchronous Hindsight retain and exact-document read pass using explicitly synthetic trading-training narratives. Identity isolation, the historical cutoff and untrusted hypothesis status pass against the real transport. No coding handoff or development lesson was written to Hindsight.
- An existing personal document remains readable after restarting the application container. The Control Plane renders its login screen on port 9999; interactive login with the UI key was not automated.
- Task `fxra-hindsight-backup` completed with result 0, made a new consistent archive and copied it to `D:\Work\codex\gs_learner\Backups`. It runs daily at 12:35 local time, after the other host's documented 12:30 backup slot. The separate shared-folder sync runs every five minutes.

Sanitized verification artifacts remain ignored under `artifacts/`; credentials remain in private ignored files. Docker's CLI is at `%LOCALAPPDATA%\Programs\DockerDesktop\resources\bin\docker.exe`; the agent's older process PATH did not include it, so commands and the backup task add that directory to their own process PATH only.

Remaining limits: the other host was not contacted, and the two writable databases are not continuously synchronized (Q-13). A coordinated restore of a matching simulation SQLite/Hindsight pair remains to be exercised. Claude/Codex ai-memory acceptance is unverified because installation awaits review; no real character LLM runner/sandbox is installed. `uv` is absent in the current process; local tests use Python and declared dependencies isolated in ignored `artifacts/test-deps`, while CI runs the standard `uv` suite. No global environment, live execution adapter, risk config, approval artifact or strategy stage was changed.
