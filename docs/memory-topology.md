# Shared memory topology and cutover

D-26 and D-27 record the owner's delegated decisions on 2026-10-27. Adoption and
topology are settled. This document is an implementation contract, not evidence
that ai-memory is installed or the second machine is connected.

## One authority for both machines

The current development machine is the primary memory host. Its verified local
Hindsight deployment is authoritative after cutover. A separate ai-memory service
on the same host will hold curated development notes and Claude/Codex handoffs
in workspace `personal-development`, project `fx-research-agent`. Use the reviewed
source candidate in D-26 and a Linux/WSL2 service; resolve the executable artifact
and verify its digest, dependencies/licenses and permissions under A-01 before
execution. The host choice minimizes migration from the verified deployment.

Both machines' coding clients use the same development backend and identity.
Research/character narratives use Hindsight bank `fxra` through the existing
trusted gateway. A character receives neither ai-memory nor a raw Hindsight
endpoint. Git remains authoritative for rules, code and technical decisions.

Each memory service binds to loopback. The second machine uses authenticated SSH
local forwarding to the primary's loopback services, with host-key verification
and credentials kept outside Git. The tunnel's local listener also binds only to
loopback. Provision and test SSH access before changing client routing. Exact
hostnames, addresses, account identifiers, keys and client tokens stay in private
configuration; no values are invented here. Existing public or broad LAN
listeners are not part of this design. D-27 supersedes the D-16 direct HTTP LAN
path for memory clients; it does not authorize a firewall change on an unknown
host. No new networking package is selected or installed by this document.

One service is the write authority for each memory layer. The other laptop's
existing Hindsight data remains preserved as a standby/archive; its clients
leave that database's shared write path during cutover. A second live writer
must never be described as a synchronized replica. No active SQLite database,
PostgreSQL volume or ai-memory data directory is synchronized through Drive.
The existing additive Drive helper continues copying backup archives (D-24).

If the primary or tunnel is unavailable, shared memory operations fail closed.
Coding can continue using Git, with the memory limitation reported. Do not
silently redirect writes to the local standby or another memory product.
Simulation narrative delivery remains pending in its controller outbox and can
retry against the same primary with the same immutable document IDs. No
automatic standby promotion or restore is allowed.

## Cutover checklist

1. Record private primary/secondary connection settings and verify SSH host
   identity and loopback forwarding. Keep the existing working local service.
2. Pause shared-memory writers on the secondary. Back up both Hindsight
   histories and matching simulation SQLite registries; preserve their UUIDs,
   immutable documents and departure deadlines. Retain both original archives.
3. Compare document IDs and original-content hashes with authorized host tools.
   Review secondary-only documents and any conflicting IDs before importing.
   Use an explicit reviewed import plan if histories diverged; never restore
   one full archive over the other or silently overwrite a document. Existing
   run records remain the source of truth for research metrics.
4. Complete A-01's artifact review and install only the D-26 MCP scope, without
   hooks, auto-upgrade, transcript import or a remote LLM. Register Claude and
   Codex against the primary's authenticated development backend using its real
   tool schemas. Discovery/configuration alone does not pass acceptance.
5. Point clients on both machines at the primary, through loopback forwarding
   on the secondary. Verify the exact Hindsight canary document from both
   machines. Run A-03's Claude-to-Codex-to-Claude handoff, claim-once and
   wrong-workspace/project tests. Include an actual cross-machine canary.
6. Disconnect the tunnel and verify that neither client writes to the standby.
   Reconnect and verify pending simulation delivery uses the original IDs.
   Record sanitized client versions, scope, document/handoff IDs and checks.

Until this checklist passes, report the current local verification and the
remaining deployment work separately. Existing local histories are independent;
choosing an authority does not reconcile them retroactively.

## Recovery and changing the primary

Back up each memory layer through its documented consistent backup procedure.
Pair simulation SQLite snapshots with Hindsight as in the V-03 drill. Back up
ai-memory's complete private data directory after activation, including its wiki
history, index/handoff state and protected authentication material. Protect each
archive with the same access restrictions as the source data.

A deliberate primary change requires stopping the old shared writers, preserving
their latest consistent backups, restoring to an isolated target, verifying
integrity and expired-session recovery, then changing both clients together.
Fence the former primary from shared writes before enabling the replacement.
Keep the old archives for rollback. If authority is uncertain, stop writes until
the conflicting histories are reviewed. Automatic bidirectional database merge
and automatic failover remain outside this scope.
