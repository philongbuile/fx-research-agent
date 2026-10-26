# Hindsight server runbook

One Hindsight server on one Docker host, shared by both dev machines. Verified locally on 2026-10-26 with Docker Desktop 4 (Compose v5.3) on Windows 11. The checks were: auth (401 without the key, 200 with it, on the REST API and on MCP), retain and recall, backup, restore, and data surviving `down`/`up`.

| Port | Service | Auth |
|---|---|---|
| 8888 | REST API (`/v1/...`), MCP (`/mcp/<bank>/`), `/health` | `Authorization: Bearer $HINDSIGHT_API_KEY` (`/health` is open) |
| 9999 | Control Plane UI | `HINDSIGHT_UI_ACCESS_KEY` login |

Data lives in the named volume `fxra-hindsight_pg_data` (PostgreSQL + pgvector). `docker compose down` keeps it. `docker compose down -v` **deletes it**.

## 1. First start on the Docker host

```bash
cd infra/hindsight
cp .env.example .env
# Fill .env. Generate each secret with:
python -c "import secrets; print(secrets.token_urlsafe(32))"
docker compose config --quiet          # validates; prints nothing on success
docker compose up -d
docker compose logs -f hindsight       # first start downloads models; wait for "Ready"
curl -s http://127.0.0.1:8888/health
```

Linux host only: the container runs as uid 1000. Make `backups/` writable by that uid:

```bash
mkdir -p backups && sudo chown 1000:1000 backups
```

## 2. Network exposure (choose one)

**A. Tailscale (recommended).** Keep `HINDSIGHT_BIND_IP=127.0.0.1`. On the Docker host, with MagicDNS and HTTPS certificates turned on for the tailnet:

```bash
tailscale serve --bg --https=8888 http://127.0.0.1:8888
tailscale serve --bg --https=9999 http://127.0.0.1:9999
tailscale serve status
```

Clients use `https://<host>.<tailnet>.ts.net:8888`. Traffic stays inside the tailnet and uses TLS. Restrict access to the two dev devices with a Tailscale ACL. **Never** use `tailscale funnel`, because that publishes the service to the Internet. (Check the `serve` flags against `tailscale serve --help` for your Tailscale version.)

**B. LAN only.** Set `HINDSIGHT_BIND_IP=<host LAN IP>` (not `0.0.0.0`), then run `docker compose up -d`. Allow TCP 8888 and 9999 in the host firewall from the two dev machines' IPs only. Traffic is plain HTTP, so the bearer key crosses the LAN unencrypted. Use this only on a network you trust.

Check what is bound:

```bash
docker inspect fxra-hindsight-hindsight-1 --format '{{json .HostConfig.PortBindings}}'
```

## 3. Connect each dev machine

Both machines use the same URL, the same key and the same bank, `fxra`. Store the key in the user's own config, never in the repo.

```bash
# set once per machine (shell profile, or the Windows user environment)
export HINDSIGHT_URL=https://<host>.<tailnet>.ts.net:8888    # or http://<LAN IP>:8888
export HINDSIGHT_API_KEY=<key from the host's .env>
```

**Claude Code** (user scope, so the key is not written into this repo):

```bash
claude mcp add --transport http --scope user hindsight "$HINDSIGHT_URL/mcp/fxra/" --header "Authorization: Bearer $HINDSIGHT_API_KEY"
claude mcp list
```

**Codex CLI** (`~/.codex/config.toml`). Streamable HTTP MCP with a bearer token taken from the environment. Check the key names against the Codex docs for your version:

```toml
[mcp_servers.hindsight]
url = "https://<host>.<tailnet>.ts.net:8888/mcp/fxra/"
bearer_token_env_var = "HINDSIGHT_API_KEY"
```

**Smoke test from a dev machine:**

```bash
curl -s -o /dev/null -w "%{http_code}\n" "$HINDSIGHT_URL/v1/default/banks"                                          # expect 401
curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $HINDSIGHT_API_KEY" "$HINDSIGHT_URL/v1/default/banks"   # expect 200
```

Use the single-bank endpoint `/mcp/fxra/` for agents. The multi-bank `/mcp/` exposes bank create, list and delete tools. The single-bank endpoint is filtered too: `HINDSIGHT_API_MCP_ENABLED_TOOLS` in `compose.yaml` removes every delete, clear and update tool. To change the list, set `HINDSIGHT_MCP_ENABLED_TOOLS` in `.env`.

## 4. Backup

`hindsight-admin backup` makes a consistent snapshot (one `REPEATABLE READ` transaction) of all Hindsight tables into a zip file:

```bash
docker compose exec hindsight hindsight-admin backup /backups/hindsight-$(date -u +%Y%m%dT%H%M%SZ).zip
```

On Windows Git Bash, prefix the command with `MSYS_NO_PATHCONV=1`, or Git Bash rewrites `/backups/...` into a Windows path.

Files appear in `infra/hindsight/backups/`, which is gitignored. Copy them **off the host** (another disk, or the second machine). Schedule the backup daily with cron or Windows Task Scheduler. Optional second layer: `docker compose exec db pg_dump -U hindsight -Fc hindsight > hindsight.dump`.

## 5. Restore

**Restore deletes all existing data in the target schema.** Make a fresh backup first.

```bash
cp <backup>.zip infra/hindsight/backups/
docker compose exec hindsight hindsight-admin restore /backups/<backup>.zip --yes
```

Move to a new host: install the same `HINDSIGHT_VERSION`, run `up -d`, restore the latest zip, and point both clients at the new URL.

## 6. Upgrade

1. Back up (section 4).
2. Read the Hindsight release notes for breaking changes between versions.
3. Change `HINDSIGHT_VERSION` in `.env`. Then run `docker compose pull`, and `docker compose up -d`. Migrations run at start-up.
4. Run the smoke test. If it fails, set the old version back and restore.
