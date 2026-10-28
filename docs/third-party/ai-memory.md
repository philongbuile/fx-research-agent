# ai-memory integration review

Reviewed 2026-10-27; exact-artifact and dependency review updated 2026-10-28.
Adoption and bounded MCP-only installation are authorized under D-26 and the
owner's 2026-10-28 request to install the missing memory MCP tools. The checks
below permit this exact local Linux container; client acceptance is separate.

- Source: <https://github.com/akitaonrails/ai-memory>.
- Candidate commit: `0e5488073d7073caa3181bbaf184102b215dcf66` (HEAD resolved with `git ls-remote`). Its workspace manifest declares version `3.2.8`. This is a source pin, not a verified release asset or container digest. Re-review if the commit changes.
- License: [MIT](https://github.com/akitaonrails/ai-memory/blob/0e5488073d7073caa3181bbaf184102b215dcf66/LICENSE), permits commercial use and is compatible with this project's proposed Apache-2.0 license; preserve notices.

## Inspected surfaces

Read upstream README, installation, support matrix, architecture, marker-file and usage documentation. Inspected source portions of `crates/ai-memory-core/src/{handoff,sanitize}.rs` and `crates/ai-memory-cli/src/commands/{install_mcp,install_hooks}.rs` at the candidate commit, plus the workspace manifest. This is an integration assessment, not a full security audit.

The project declares shared project memory across Claude Code and Codex. Scope uses a workspace/project pair, resolved through a repository marker. Its wiki is Markdown backed by Git, with a derived SQLite index; this repo adds no vector database. Handoffs carry sender/session, scope, summary, questions and next steps; acceptance is an explicit operation. These are upstream capabilities, **not a successful local cross-client test**.

MCP registration generators can edit client settings when applied. Hook installers can write client configuration and launch capture commands. Full setup can create user services, logon startup entries and token files. Those effects exceed a repo-only edit. Native Windows is marked experimental; WSL2 is supported. Do not run remote installer scripts through `iex`, `sh`, or enable auto-upgrade here.

Capture can send prompts, tool activity and session events to the memory server. The sanitizer covers common credential patterns, but cannot prove that arbitrary account information is absent. Marker exclusions are lexical, and explicitly do not cover every prompt, shell, unknown-tool or output path. Start with **MCP-only, manually curated development notes and handoffs**; do not enable automatic hooks, transcript import, bootstrap of all docs, cross-project profile, remote LLMs, embeddings or automatic improvement. No broker capability is needed or permitted.

## Initial review limitations (2026-10-27)

The Rust workspace includes Tokio, serde, rusqlite, git2/libgit2, rmcp, axum, reqwest/rustls, regex and optional Candle/tokenizers model components, among others. The manifest also patches zip from a pinned upstream Git commit. The initial review left artifact and dependency checks open. The dated A-01 addendum below records the subsequent checks; it is still not an exhaustive source audit. Do not infer zero telemetry or zero egress from the zero-LLM mode alone.

## Permission proposal and approval

Only a dedicated development-memory data directory, loopback authenticated HTTP service and explicit project-scoped MCP tools are needed. Keep its token outside the repo. Character sessions receive neither this endpoint nor its credentials. Use the same backend and owner identity for Claude and Codex; the marker is routing, not authorization. Restrict permissions server-side if a server is shared.

Owner authorization: on 2026-10-27 the owner explicitly delegated the ai-memory and cross-machine memory decisions and requested merging them. D-26 selects the candidate source and authorizes installation/registration within the MCP-only scope above once exact-artifact checks pass. D-27 selects a single primary host; see [topology and acceptance](../memory-topology.md). Further adoption approval is not pending within this scope.

The decision change itself did not verify or install an artifact. The A-01 addendum below now passes the scoped artifact checks. Material changes to the source pin, permission scope or capture policy require another review under `docs/security.md`. Actual client acceptance and both-machine canaries remain separate requirements.

## A-01 exact-artifact review (2026-10-28)

- Official GitHub tag `v3.2.8` resolves to the D-26 source pin above.
- Official release archive `ai-memory-linux-x86_64.tar.gz` SHA-256:
  `a7c787a4941e86aba77ec970ccc333c34698ab7e4219c0cb813febf23fe8ac34`.
  Downloaded over HTTPS and matched against the GitHub release asset digest.
- Official Docker Hub Linux/amd64 image:
  `akitaonrails/ai-memory@sha256:271ae16138d052b970d0bb7c9d83b2a32713d2722b8655228f20561c690207ca`.
  Its executable was copied from an unstarted container and is byte-identical
  to the verified release executable, SHA-256
  `361922cca63bfafb07d47e79ad2b25afd4a5b4e1c217e395433abeacb4b72ee7`.
  This establishes publisher-artifact consistency, not a reproducible source
  build or an independent provenance attestation.
- Compared all 506 registry package versions/checksums in the pinned
  `Cargo.lock` against the official sparse registry and retrieved each exact
  version's declared license from crates.io; no missing records or licenses.
  The only Git dependency is `zip` 8.6.0 at
  `771dfc534d2614158af5497ea3dff4d4208d7db1`, whose manifest declares MIT.
  This conservatively includes optional, test and non-Linux packages; it is a
  lockfile inventory, not a measured SBOM of the linked executable.
- Available license choices cover MIT, Apache-2.0 (including the LLVM
  exception), BSD, ISC, Zlib, Unicode-3.0, CC0-1.0,
  CDLA-Permissive-2.0, and MPL-2.0. Choose permissive alternatives on OR clauses
  (including the clause offering LGPL); MPL remains file-level copyleft.
  These allow commercial service use. Preserve notices and meet any applicable
  source/notice obligations before redistributing modified components.
- The upstream runtime Dockerfile uses Debian bookworm-slim, CA certificates,
  a non-root UID 1000 and the prebuilt executable. Preserved its package
  inventory (91 packages) and `/usr/share/doc` archive (90 copyright files).
  Debian contains separately licensed system components, including GPL/LGPL;
  this is not a claim that every image component is Apache-2.0. Native bundled
  libraries include SQLite (public domain) and libgit2 (GPLv2 with its linking
  exception). No image redistribution or modified native-library build is
  part of this installation.
- Read the pinned Dockerfile, release workflow, default config, CLI argument
  declarations, init/serve/config implementation and MCP server surface,
  in addition to the earlier review. Default local embeddings download a
  model, and scheduled maintenance/auto-improvement are enabled by default:
  explicitly disable embeddings, maintenance, auto-improvement and profile.
  Leave the LLM provider unset, disable the wiki watcher, and install no hooks,
  setup/bootstrap, auto-updater, web console or transcript importer.
- Final deployment uses `network_mode: none`, an authenticated loopback HTTP
  listener inside the container, and the upstream stdio-to-HTTP MCP bridge
  through `docker exec -i`. There is no published port or outbound interface.
  The sole mount is its dedicated data volume, containing mode-0600 private
  config/token files; there are no host, repo, Hindsight or Docker socket mounts.
  Non-root, no additional capabilities, no-new-privileges, read-only image
  filesystem, bounded memory/processes. This is a Linux service on the existing
  Docker Desktop WSL2 backend, without installing another distro. The checked
  entrypoint refuses missing/empty config and token files before upstream can
  fall back to defaults.

Sanitized review evidence and preserved notices are in the ignored
`artifacts/ai-memory-review/` directory. Registry metadata and the publisher's
checksums are trust inputs; this scoped integration review is not a complete
code audit, vulnerability scan or legal certification. A-01 passes for the
exact deployment above. Installation, actual MCP calls, real two-client
acceptance (A-03) and the deferred second-machine canary must be reported
separately.
