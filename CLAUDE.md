@AGENTS.md

# Claude Code notes

Shared rules are in AGENTS.md (imported above). Only Claude-specific notes go here.

- The Hindsight MCP server is added per user (`claude mcp add --scope user`, see `infra/hindsight/README.md`). Do not add a project `.mcp.json` that contains the key.
- If the `hindsight` MCP tools are missing, work without memory and say so. Do not fall back to writing memories into repo files.
- Windows Git Bash: prefix `docker exec ... /path` commands with `MSYS_NO_PATHCONV=1`.
