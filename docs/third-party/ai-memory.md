# ai-memory integration review — pending owner approval

Reviewed 2026-10-27. **Not approved, installed, downloaded as executable code, or enabled.**

- Source: <https://github.com/akitaonrails/ai-memory>.
- Candidate commit: `0e5488073d7073caa3181bbaf184102b215dcf66` (HEAD resolved with `git ls-remote`). Its workspace manifest declares version `3.2.8`. This is a source pin, not a verified release asset or container digest. Re-review if the commit changes.
- License: [MIT](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/LICENSE), permits commercial use and is compatible with this project's proposed Apache-2.0 license; preserve notices.

## Inspected surfaces

Read upstream README, installation, support matrix, architecture, marker-file and usage documentation. Inspected source portions of `crates/ai-memory-core/src/{handoff,sanitize}.rs` and `crates/ai-memory-cli/src/commands/{install_mcp,install_hooks}.rs` at the candidate commit, plus the workspace manifest. This is an integration assessment, not a full security audit.

The project declares shared project memory across Claude Code and Codex. Scope uses a workspace/project pair, resolved through a repository marker. Its wiki is Markdown backed by Git, with a derived SQLite index; this repo adds no vector database. Handoffs carry sender/session, scope, summary, questions and next steps; acceptance is an explicit operation. These are upstream capabilities, **not a successful local cross-client test**.

MCP registration generators can edit client settings when applied. Hook installers can write client configuration and launch capture commands. Full setup can create user services, logon startup entries and token files. Those effects exceed a repo-only edit. Native Windows is marked experimental; WSL2 is supported. Do not run remote installer scripts through `iex`, `sh`, or enable auto-upgrade here.

Capture can send prompts, tool activity and session events to the memory server. The sanitizer covers common credential patterns, but cannot prove that arbitrary account information is absent. Marker exclusions are lexical, and explicitly do not cover every prompt, shell, unknown-tool or output path. Start with **MCP-only, manually curated development notes and handoffs**; do not enable automatic hooks, transcript import, bootstrap of all docs, cross-project profile, remote LLMs, embeddings or automatic improvement. No broker capability is needed or permitted.

## Dependencies and unresolved review work

The Rust workspace includes Tokio, serde, rusqlite, git2/libgit2, rmcp, axum, reqwest/rustls, regex and optional Candle/tokenizers model components, among others. The manifest also patches zip from a pinned upstream Git commit. Direct and transitive dependency licenses, bundled native notices, release provenance, binaries, installer behavior, network/telemetry behavior and the exact feature set **have not been exhaustively audited**. Produce a lockfile-based license/SBOM review and verify the chosen artifact digest before installation approval. Do not infer zero telemetry or zero egress from the zero-LLM mode.

## Permission proposal and approval

Only a dedicated development-memory data directory, loopback authenticated HTTP service and explicit project-scoped MCP tools are needed. Keep its token outside the repo. Character sessions receive neither this endpoint nor its credentials. Use the same backend and owner identity for Claude and Codex; the marker is routing, not authorization. Restrict permissions server-side if a server is shared.

Owner approval: **pending**. Activation and the actual Claude→Codex→Claude handoff test are blocked by AGENTS.md hard limit 4 and `docs/security.md` third-party review policy. The prepared marker and [runbook](../memory-and-simulation.md) are reviewable without changing machine-wide settings.
