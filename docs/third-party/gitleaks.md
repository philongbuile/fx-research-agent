# Review note: gitleaks

Used by the `secret-scan` job in `.github/workflows/ci.yml` (backlog B-07). Review format: `docs/security.md`, "Third-party code".

## 1. Source, pin, date

- Source: https://github.com/gitleaks/gitleaks, release `v8.30.1` (published 2026-03-21), tag commit `83d9cd684c87d95d656c1458ef04895a7f1cbd8e`.
- Artifact: `gitleaks_8.30.1_linux_x64.tar.gz`, sha256 `551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb`. Source of the hash: the asset digest reported by the GitHub releases API. The workflow checks it with `sha256sum -c` before unpacking. The hash was not cross-checked against `gitleaks_8.30.1_checksums.txt` (its sha256 per the API: `061476c2...a52fae`), so the owner may want to do that once.
- Reviewed: 2026-10-26.
- Not used: `gitleaks/gitleaks-action` (needs a license key for organization repos and runs a wrapper we have not reviewed), and the container image (no digest was looked up without pulling it).
- Not downloaded, installed or run locally during this review.

## 2. License

MIT. Compatible with Apache-2.0 and commercial use. CI only, not shipped.

## 3. Code read

- Single Go binary. It reads the git history (`gitleaks git`) and matches rules against it. Findings are printed redacted (`--redact`), so a leaked value is not copied into the CI log.
- Network and telemetry: only the download of the binary from GitHub in the workflow. The scanner itself makes no network calls as far as the README and docs show. Source was not audited line by line.
- Install is `curl` from the GitHub release URL, hash check, `tar -xzf`, no install script.

## 4. Dependencies

Go modules compiled into the release binary (listed in `go.mod` upstream), under permissive licenses per upstream. Not enumerated here. The binary is statically built, so nothing else is installed on the runner.

## 5. Permissions

- Reads the checked-out repo and full history (`fetch-depth: 0`). No write access, no secrets, no tokens. Workflow permissions stay `contents: read`.
- Exit code is non-zero when a finding exists, which fails the job. That is the intended behavior.

## 6. Approval

Approved by: pending owner review

Updating: bump `GITLEAKS_VERSION` and `GITLEAKS_SHA256` together, and edit this note.
