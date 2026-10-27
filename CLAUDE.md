@AGENTS.md

# Claude Code notes

Shared rules are in AGENTS.md (imported above). Only Claude-specific notes go here.

For the simulation initiative, follow the shared reading path and development-handoff protocol in AGENTS.md. The specification, plan and roadmap are canonical repo documentation; ai-memory activation and cross-client verification are still pending.

- The Hindsight MCP server is added per user (`claude mcp add --scope user`, see `infra/hindsight/README.md`). Do not add a project `.mcp.json` that contains the key.
- If the `hindsight` MCP tools are missing, work without memory and say so. Do not fall back to writing memories into repo files.
- Development memory uses ai-memory, not Hindsight. Follow the separate scopes and pending activation instructions in AGENTS.md; if ai-memory tools are missing, report that limitation and continue without it.
- Windows Git Bash: prefix `docker exec ... /path` commands with `MSYS_NO_PATHCONV=1`.
