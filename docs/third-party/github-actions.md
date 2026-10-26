# Review note: GitHub Actions `actions/checkout` and `astral-sh/setup-uv`

Used by `.github/workflows/ci.yml` (backlog B-07). Review format: `docs/security.md`, "Third-party code".

## 1. Source, pin, date

| Action | Source | Version | Pinned commit | Reviewed |
|---|---|---|---|---|
| checkout | https://github.com/actions/checkout | v7.0.1 (released 2026-07-20) | `3d3c42e5aac5ba805825da76410c181273ba90b1` | 2026-10-26 |
| setup-uv | https://github.com/astral-sh/setup-uv | v10.3.2 (released 2026-10-22) | `07a047e608db84b3d8c06cd9a7c7dcc9c355c18e` | 2026-10-26 |

SHAs come from the GitHub API (`git/ref/tags/<tag>`, type `commit`, so not annotated-tag objects) for the latest release of each repo. setup-uv also downloads `uv`, which the workflow pins to `0.12.7` (the version used locally).

## 2. License

Both MIT. Compatible with Apache-2.0 and commercial use. The actions run only in CI and are not shipped.

## 3. Code read

- Both are JavaScript actions (`runs.using: node24`) with a bundled `dist/`. Not read line by line: `action.yml` and inputs only. The reviewer should skim the diff of the pinned commit's `dist/` if they want more than the reputation of the publishers (GitHub, Astral).
- checkout: clones the repo with the workflow token. We set `persist-credentials: false`, so the token is not left in `.git/config`. We set `fetch-depth: 0` only in the secret-scan job.
- setup-uv: downloads the `uv` release from GitHub releases for the pinned version, puts it on `PATH`, and sets `UV_PYTHON`. Its cache is turned off (`enable-cache: false`). No telemetry known beyond what `uv` itself does (none by default).
- Network: github.com and api.github.com, plus PyPI (via `uv sync`, not the action).

## 4. Dependencies

- checkout: bundled into `dist/`. Direct deps are the `@actions/*` toolkit packages (MIT).
- setup-uv: bundled. Direct deps: `@actions/cache`, `core`, `exec`, `glob`, `io`, `tool-cache` (MIT), `@renovatebot/pep440` (MIT), `smol-toml` (BSD-3-Clause), `undici` (MIT). Transitive deps not enumerated. Python deps of this project are pinned by `uv.lock` and installed with `--locked`.

## 5. Permissions

- Workflow-level `permissions: contents: read`, no other scopes. No repository secrets are used. The default `GITHUB_TOKEN` is read-only here.
- checkout needs contents read to clone. setup-uv's `github-token` input defaults to the same token and is used only to fetch the uv release without hitting rate limits.
- No shell tools, MCP servers or order-sending code are involved.

## 6. Approval

Approved by: pending owner review

Updating a pin (Dependabot or by hand) needs a new SHA lookup and an edit of this note.
