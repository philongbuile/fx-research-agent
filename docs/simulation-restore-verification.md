# Coordinated simulation restore drill (V-03)

The trusted host can exercise a matching SQLite/Hindsight backup pair with the
existing pinned images and declared Python dependencies:

```powershell
uv run python infra/hindsight/verify-simulation-restore.py
```

The local source must already be running at `http://127.0.0.1:8888`, with its
private credentials in `infra/hindsight/.env`. Both pinned images must already be
present. The drill does not pull images, install packages, restart the source,
or restore into its database. Run it only on the authorized local host.

The fixture creates synthetic trading-training narratives in bank `fxra` and
keeps them after verification. It does not write development notes or actual
market results. A new simulation UUID and a new `fxra-restore-<uuid>` Docker
project are used for every attempt. Failed attempts remain recorded.

## Coordination and isolation

The fixture flushes all its immutable documents, closes its sole SQLite writer,
then takes the Hindsight backup. Nothing writes to that simulation until both
backups are complete. Other simulations can continue writing disjoint document
IDs. This is a quiesced-writer protocol, not an atomic transaction across SQLite
and PostgreSQL. A future multi-writer controller needs a reviewed coordination
mechanism before using this procedure.

The full Hindsight archive can contain other private research memories. Keep the
generated directory private and ignored; do not attach the archive to a PR.
The target has a fresh database volume, an isolated Compose network, no published
database/UI ports and a randomly assigned loopback API port. Temporary access
credentials are generated in memory and passed through process environments.
No credential values are written into the generated Compose file or report.
Docker/server output is suppressed; failures expose fixed diagnostic categories.

Hindsight's admin restore imports data into an existing compatible schema. The
drill first starts the pinned application against the empty target to run its
migrations, then stops that application's writers before restoring. It starts
the restored application and discovers its current API port again; Docker may
reassign a dynamic port after stop/start or restart.

## Acceptance evidence

The fixture verifier checks all persisted SQLite tables before startup recovery,
including the simulation UUID, journal, personality, ledgers, knowledge, delivery
markers and pending departure. Every registered document must exist in restored
Hindsight and match its original text. Missing/mutated documents fail closed;
the drill never repairs delivery markers or re-retains missing memories.

Identity-scoped reads, historical cutoffs and untrusted hypothesis annotations
must survive restore. A controlled synthetic wall clock then advances beyond
the original persisted final-session deadline. Startup recovery must end the
departing character and release `no_message`, without fabricating final outputs.
The same verification runs from another untouched SQLite copy after restarting
the isolated Hindsight service.

Each attempt keeps `source.sqlite`, `checkpoint.sqlite`, `restored.sqlite`,
`hindsight.zip`, a credential-free `compose.json` and `verification.json` under
`artifacts/simulation/fxra-restore-<uuid>/`. The report includes backup hashes,
simulation/project IDs, completed checks and status. The source archive also
remains in `infra/hindsight/backups/`. The drill removes only its own disposable
containers/network/volume in `finally`; failed cleanup is recorded in the report.
The backup pair remains available for another recovery exercise.

Regression coverage is in `tests/test_simulation_restore.py` and
`tests/test_restore_drill.py`. These include mismatched SQLite, missing/changed
documents, attempted in-place recovery, remote bindings and credential-safe
failure output. They use the explicit offline test double; passing them alone
does not establish deployed restore success.

This drill verifies the local synthetic infrastructure. It does not demonstrate
cross-host reconciliation (Q-13), a real character runner, approved economic
policy, or both-client ai-memory acceptance (Q-09).

## Local result (2026-10-27)

Passed against the real local Hindsight transport, including isolated service
restart, with simulation UUID `49ac548224694621ae25fa707b344237` and project
`fxra-restore-b5a4f25a7dc74102888ab94b4cbcc02b`. The ignored evidence is
`artifacts/simulation/fxra-restore-b5a4f25a7dc74102888ab94b4cbcc02b/verification.json`.
All reported checks passed and the disposable containers/network/volume were
removed. The source service was neither restored nor restarted.

Backup pair SHA-256:

| File | Digest |
|---|---|
| `checkpoint.sqlite` | `5c50aa83e9fa66550ad0126b1887a533a8b80f713b9c9cd2a4a56de35517e1b4` |
| `hindsight.zip` | `767ea65e67e2f76f4ced95bdee133877dbf7d6d3a2ab384b5026244dc43c1e78` |

Earlier failed attempts remain in ignored artifacts. They identified the need
to initialize the target schema/extensions and rediscover dynamic ports after
service starts. No failed attempt restored into the source database.

Local regression result: 94 tests passed using Python 3.13 and existing declared
dependencies under ignored `artifacts/test-deps`. `uv run pytest` was attempted,
but this process has no `uv` executable. CI runs `uv sync --locked` and
`uv run pytest` on Python 3.12; local fallback success does not establish that
CI has passed.
