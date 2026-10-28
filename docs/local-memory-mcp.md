# Local memory MCP installation — 2026-10-28

The owner's request to install the missing MCP tools completes local setup
within D-26. D-27's second-machine connection remains explicitly deferred.
This is installation and protocol evidence, not a passing real
Claude-to-Codex-to-Claude acceptance test or a completed S1 milestone.

## Hindsight

The existing Hindsight 1.1.0 service was already installed. Registered its
single-bank `http://127.0.0.1:8888/mcp/fxra/` endpoint in Codex and Claude user
configuration, referring to `HINDSIGHT_API_KEY` rather than embedding a key.
The authorized private `.env` supplied the key in process; it is now also in
the current Windows user's environment for new client processes. No credential
was put in this repository, command arguments, URLs or verification output.

Authenticated MCP initialize/tools-list succeeds. Its 18 exposed tools exclude
delete, clear, update and invalidation tools. The existing server, credentials,
database, Compose settings and research documents were not changed. No research
result was written during these development-memory installation checks.

Fully exit/reopen the coding clients to load the new MCP configuration and user
environment. If a launcher preserves its old environment, relaunch it from a
new Windows login/session; never paste the key into chat to work around this.

## ai-memory

A-01's exact-artifact and lockfile/license checks pass for version 3.2.8 at the
D-26 source pin; see the [review](third-party/ai-memory.md). The verified Linux
service is installed using [this Compose deployment](../infra/ai-memory/README.md).
It uses the existing Docker Desktop WSL2 backend, not experimental native
Windows deployment and not a new distro.

Both clients' user configurations register the same upstream stdio bridge:

```powershell
docker exec -i fxra-ai-memory /usr/local/bin/ai-memory --config /data/config.toml mcp-bridge --no-session-id
```

The registered Docker executable uses its actual absolute local path, so it
does not depend on the launcher PATH. Tokens stay in private data; the command
contains none. Claude's existing unrelated user settings were preserved and a
private pre-change backup retained. Codex exposes only query/read/recent/status/
briefing, curated write, handoff begin/list/accept and feedback tools; destructive,
cross-project messaging and maintenance tools are not enabled there. This is
trusted single-operator access: the marker and Codex's tool filter are not a
server-side project authorization boundary. Never give either coding-client
configuration to simulation characters. Claude's registration does not grant
automatic tool approval or install hooks.

Runtime has no published ports, external network or host mounts. LLM and
embeddings are disabled; so are profile, watcher, maintenance, automatic
improvement, evaluation commands and transcript capture. Only the dedicated
Linux volume is writable (plus bounded temporary memory). The private config
and token have mode 0600 and service ownership. Missing/empty config/token files
fail the checked startup guard.

## Verification and limits

- Upstream stdio bridge initializes and lists actual tool schemas against the
  authenticated service; no guessed tool arguments.
- Two independent protocol clients write/read a harmless development canary in
  workspace `personal-development`, project `fx-research-agent`. A test handoff
  is listed and claimed exactly once; a repeated claim is not successful.
  Handoff ID: `01a180d2-1967-76b3-910d-ce9390843697`.
- Explicit queries against a nonexistent alternate workspace/project are
  rejected without returning the canary. This is a routing negative test, not
  proof that the operator token cannot access another authorized project.
- Missing and deliberately wrong tokens both return HTTP 401 from the internal
  listener. Service restart persistence and a complete private snapshot are
  recorded in the ignored installation evidence.
- Initial provisioning exposed a Docker Desktop bind-mount problem: the
  service saw an empty config directory and fell back to defaults. Its internal
  network blocked the attempted default model download. The service was stopped,
  private config moved into its Linux volume, and startup hardened. Final
  verification uses authenticated config, disabled embeddings and no networking.

Sanitized schemas, dependency/license inventory, protocol checks and preserved
notices are in ignored `artifacts/ai-memory-review/`. No market data, trading
metrics, personal character experiences or captured transcripts were put in
ai-memory. The canary/handoff notes are explicitly installation fixtures.

Actual Claude↔Codex runtime acceptance remains open: the Claude executable is
not available on PATH or the inspected standard install paths, and this active
Codex chat cannot reload its tool inventory in place. Protocol clients are not
reported as Claude/Codex sessions. After reopening both installed clients, run
A-03's real bidirectional canary/claim test. The second-machine canary, routing
cutover, scheduled ai-memory backup and isolated restore drill remain future
work. S1 is incomplete.
