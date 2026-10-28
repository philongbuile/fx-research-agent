# uv local development-tool review

Reviewed 2026-10-28 for the owner's explicit request to prepare uv, install the
already approved pinned LEAN engine and run the repository checks/smoke test.
This scope is authorized by that request. uv is already the project's declared
environment manager and CI pins the same version in `.github/workflows/ci.yml`.

- Upstream: <https://github.com/astral-sh/uv>, official
  [installation documentation](https://docs.astral.sh/uv/getting-started/installation/).
- Version: `0.12.7`; no upgrade to the latest version.
- Artifact: official PyPI `uv-0.12.7-py3-none-win_amd64.whl`, from
  <https://pypi.org/pypi/uv/0.12.7/json>.
- Expected SHA-256:
  `277d326d7e63b912f3425c6e6d7d5d49f21b43d080d21859ff3c6819353f1847`.
  Verify before installing/executing; this is a hash obtained from the publisher's
  package index, not an independent signature or a full supply-chain audit.
- License: PyPI metadata declares `MIT OR Apache-2.0`, compatible with the
  project's license. Preserve the wheel's bundled license notices.
- Runtime Python dependencies: the artifact metadata declares none. The native
  executable contains bundled Rust/native dependencies; this limited review does
  not claim an exhaustive source/dependency audit. Inspect the wheel's metadata,
  file list and license notices before execution. No source build is selected.

The verified wheel includes both license notices and a CycloneDX SBOM. Its
declared component licenses include permissive licenses and MPL-2.0 (including
dual-license alternatives); this deployment uses the unmodified binary locally
and does not redistribute or vendor it. Preserve the bundled notices and SBOM.
Package metadata and declared SBOM licenses were inspected; this is not a
component-by-component source audit or a legal opinion.

## Execution and permissions

Use an ignored project-local installation under `artifacts/tools/uv`, without
running a remote PowerShell installer or changing global Python, shell profiles,
user PATH, hooks or services. Keep the downloaded artifact and verification
metadata ignored. Install only the verified wheel, without resolving additional
tool dependencies. Standard wheel installation does not run a source build hook.

Use uv to synchronize `uv.lock` into this repository's `.venv`, then run the
existing tests and CLI. Those operations can execute the local project's declared
build backend, install its locked dependencies and launch the test/CLI code.
Select an already installed compatible Python; do not silently install another
interpreter. Limit caches to ignored artifacts where practical.

Network use is package-index/artifact retrieval for declared locked dependencies;
no remote LLM or trading service is configured. uv can perform other network/file
operations when explicitly invoked; this review does not authorize arbitrary
tools, plugins, packages or automatic upgrades. No broker credentials or order
tools are supplied. Existing LEAN approval remains the separate Docker engine
scope documented in `docs/security.md` and D-07.
