# Local development-memory service

D-26 authorizes curated development notes only. Exact artifact and dependency
review: [ai-memory review](../../docs/third-party/ai-memory.md).
Verification and limitations: [local memory MCP](../../docs/local-memory-mcp.md).

The pinned Linux service runs in Docker Desktop's existing WSL2 backend. It has
no network interface except its own loopback, no published port, no host mounts,
and no Docker socket. Claude and Codex launch the upstream stdio bridge using
the host Docker CLI:

```powershell
docker exec -i fxra-ai-memory /usr/local/bin/ai-memory --config /data/config.toml mcp-bridge --no-session-id
```

Both bridges connect to the same authenticated server inside the container.
`--no-session-id` is deliberate for this MCP-only setup: neither hooks nor an
automatic lifecycle session is installed. Every scoped tool call must explicitly
pass workspace `personal-development` and project `fx-research-agent`.
The operator token is trusted local development access, not a project ACL.
Do not expose this bridge to simulation characters or untrusted operators.

## Existing installation

The external volume `fxra_ai_memory_data` holds the wiki Git history, SQLite,
private `config.toml` and `auth.token`. The latter files are mode `0600`, owned
by the service UID 1000. Its image filesystem is read-only. The private Windows
provisioning copy is under `%LOCALAPPDATA%\fxra\ai-memory`, restricted to the
current user and SYSTEM. Never print/copy its contents into chat or the repo.

The checked example shows the non-secret config. The installation generated
the bearer token and pepper privately with the OS CSPRNG before starting the
authenticated service. It did not run `setup`, `init`, hook installers or an
automatic bootstrap. Do not regenerate keys/pepper on an existing database.
Do not start this compose file with an empty or unverified external volume:
upstream silently falls back to defaults when an explicit config is absent.

After restoring/provisioning that private volume, from this directory:

```powershell
docker compose up -d
docker compose exec memory /usr/local/bin/ai-memory --config /data/config.toml status
```

Expect bearer authentication, LLM disabled and embedding disabled. The Docker
healthcheck alone is insufficient: verify actual authenticated MCP calls and
that missing/wrong credentials return 401. No host firewall change is needed.

## Backup and recovery

Stop this sole writer before copying the complete `/data` directory with
`docker cp` to a private protected archive, then restart it. Include the SQLite
state, wiki history, config and token together. A complete private snapshot was
taken during local verification; its location is recorded only in private
installation evidence. It is not sent through the public repo or ordinary
artifact sync. A scheduled ai-memory backup and isolated restore drill remain
future work; existing Hindsight backup tasks do not cover this new volume.

Do not run `docker compose down -v`, delete the external volume, copy active
SQLite files or restore over the running primary. Restore to a new private
volume using the same pinned image, verify integrity/scope/handoff state, then
perform an explicit primary change as in [topology](../../docs/memory-topology.md).

The second machine remains disconnected. Network/tunnel provisioning and actual
Claude-to-Codex-to-Claude acceptance are separate unfinished checks. Do not
re-enable networking or publish a port as an automatic fallback.
