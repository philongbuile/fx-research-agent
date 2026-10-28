# Local LEAN environment verification

Verified on this development machine on 2026-10-28. The owner deferred the
two-machine connection and authorized preparing the local environment and
running the existing technical smoke test. This is engine verification, not a
strategy promotion or evidence of trading edge.

## Environment and image

- Project-local uv `0.12.7`, matching CI. Its official Windows wheel SHA-256
  was checked before execution; see [review](third-party/uv.md).
- The existing Python `3.13.9` was selected explicitly. `uv sync --locked`
  created `.venv` from the checked lockfile; no global Python/profile/PATH or
  another interpreter was installed.
- `uv run pytest`: all 94 tests passed locally. CI also checks the locked
  environment on Python 3.12. These are software tests, not trading evidence.
- Installed image: `quantconnect/lean:18206`, Linux amd64 platform manifest
  `sha256:11c2930873f56a37f76aab97fbed92c9f9089029a2e39d7d713b09caaa889826`.
- Official config digest:
  `sha256:8fc4e01f3f81fc24315c3c7b43f196d4ae754fe47e8c1234caa1c83624cd8fa5`.

The normal Docker Desktop pull was unusually slow here. The same approved
platform artifact was instead fetched from official Docker Hub through the host,
with the raw manifest and every config/layer blob verified against their SHA-256
digests. A standard OCI load through Docker's bundled Linux client/API imported
the unchanged artifact. The canonical upstream digest was then registered and
tagged as `quantconnect/lean:18206`; the loaded immutable descriptor, architecture
and filesystem layer chain match the verified upstream config.

This Docker deployment reports the platform manifest digest as the image's
`.Id`. Comparing that field directly with a config digest is incorrect; the
final verification used the immutable descriptor and filesystem chain instead.
No engine version, daemon configuration, distribution, broker adapter or source
code was changed. Local installation evidence is ignored under
`artifacts/lean-setup/engine-installation.json`; verified OCI blobs remain in
`artifacts/lean-oci/`. They are generated installation artifacts, not Git content.

## Recorded smoke trial

Canonical result: `runs/20261028T042758Z-df1c5e1b/run.json`.

| Run-record field | Value |
|---|---|
| `run_id` | `20261028T042758Z-df1c5e1b` |
| `status` | `completed` |
| `reproducible` | `true` |
| `technical_only` | `true` |
| Code commit | `fa8572752882083fe4ecb95687c0a76bbbcc12be` |
| `git_dirty` | `false` |
| Hypothesis | `TECH-0001-engine-smoke-test` |
| Segment | `in_sample` |
| Window | `2014-05-05` through `2014-05-09` |

The command was `uv run fxra backtest lean/TechnicalSmokeTest/research.toml`.
The engine returned success, emitted its `SMOKE_TEST_DONE` marker and produced
the summary/artifacts collected by the run record. The runner used `--network
none`, the algorithm mount was read-only, and results stayed in the run folder.
The run uses bundled sample data and the existing incomplete smoke-test cost
assumptions. Do not interpret any simulated return as an edge or promote this
fixture. No backtest failure or trial was deleted.

The comparison report was generated with
`uv run fxra report --hypothesis TECH-0001-engine-smoke-test` and saved locally at
`artifacts/lean-setup/report.md`. Metrics remain sourced from each `run.json`.

## Next learning work and limitations

The local engine/tooling prerequisite is complete. Follow
[the first-session guide](research-first-session.md) to inspect provenance,
cost assumptions and the research workflow before choosing a new hypothesis.
Real broker-data ingestion/quality and a properly costed strategy evaluation
have not been performed on this machine.

Development memory is not activated and this session has no Hindsight/ai-memory
MCP tools. No external research-result note or development handoff write is
claimed. The run record and Git documentation remain available locally. The
two-machine connection stays deferred; S1 acceptance and supervised-character
simulation milestones remain incomplete.
