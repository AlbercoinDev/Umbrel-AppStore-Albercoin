# AGENTS.md — Umbrel Frigate

This repo is an **Umbrel app store wrapper** for [Frigate](https://github.com/sparrowwallet/frigate) (Silent Payments Electrum server). It is **not** upstream source code.

## Structure

```
docker-compose.yml       # 5 services: web, api, init, server, tor
umbrel-app.yml           # Umbrel manifest (id: albercoin-store-frigate)
exports.sh               # Exports APP_FRIGATE_NODE_PORT and APP_FRIGATE_RPC_HIDDEN_SERVICE
torrc.template           # Tor hidden service config template
nginx/default.conf       # Custom nginx config: proxies /api/ → api:8081
scripts/init.sh          # Generates config.toml + config.json from env vars at startup
scripts/api.sh           # HTTP API handler (socat) for POST /api/save → writes config.env
settings/settings.html   # Writable settings page (served at /settings.html)
icon.png
```

## Services

| Service | Image | Role |
|---------|-------|------|
| `web` | `ghcr.io/4rkad/frigate-umbrel-web` | Nginx UI, serves settings page, mounts `config.json` + `settings.html`, serves `icon.png` as `/favicon.ico` |
| `api` | `alpine:3.20` | HTTP API (socat) on 8081, handles POST /api/save → writes `/data/config.env`, re-runs init.sh, restarts server via Docker socket (using socat, not curl) |
| `init` | `alpine:3.20` | One-shot: runs `scripts/init.sh` → creates data dir, writes `config.toml` + `config.json` from env vars |
| `server` | `ghcr.io/4rkad/frigate-umbrel` | Frigate server, port 57001 |
| `tor` | `getumbrel/tor` | Tor hidden service → `server:57001` |

`web` and `server` depend on `init` completing first.

## Configurable env vars (docker-compose.yml `server.environment`)

All set with defaults. Change via Umbrel env vars or edit `docker-compose.yml` directly:

| Variable | Default | Description |
|----------|---------|-------------|
| `FRIGATE_COMPUTE_BACKEND` | `CPU` | `AUTO`, `GPU`, or `CPU`. **Change to `AUTO` on machines with GPU.** |
| `FRIGATE_CACHE_SIZE` | `10M` | ScriptPubKey cache entries (~4GB RAM per 10M). Reduce on low-RAM. |
| `FRIGATE_MEMORY_LIMIT` | _(empty)_ | DuckDB memory limit (e.g. `4GB`, `2048MB`). Default: 80% of system RAM. |
| `FRIGATE_DB_THREADS` | _(empty)_ | DuckDB CPU threads for CPU scanning. Empty = all cores. |
| `FRIGATE_BATCH_SIZE` | `300000` | GPU dispatch batch size. Reduce for old GPUs. |
| `FRIGATE_RPC_REQUEST_TIMEOUT` | `60` | Bitcoin Core RPC read timeout in seconds. |
| `FRIGATE_RPC_BATCH_SIZE` | `100` | Max sub-requests per JSON-RPC batch. |
| `FRIGATE_START_HEIGHT` | _(empty)_ | Index start height (default: Taproot activation 709632 mainnet). |

`scripts/init.sh` reads these and generates `config.toml` (for Frigate) and `config.json` (for the settings page).

## Settings page

Available at `/settings.html` on the app's web UI, or click the **⚙ Settings** button (injected into the generated index.html at container start). Shows current config values read from `config.json` in editable form fields with quick presets (Default/GPU/Low RAM). Click **Save &amp; Restart** to POST JSON to `/api/save` → writes `/data/config.env`, runs `init.sh` to regenerate `config.toml` + `config.json`, then restarts the server container via the Docker socket API. The page reloads automatically after 3s.

`web` startup injects the Settings button and favicon link into the environment-substituted `index.html` via `sed` in the container command.

## Important notes

- **Docker socket**: The `api` container mounts `/var/run/docker.sock` **read-write** (required for the HTTP POST to restart the server container). Read-only would silently fail.
- **Navigation**: The main status page (`/`) gets a Settings button injected via `sed` during the web container's startup command. No template modification needed.
- **Favicon**: `/favicon.ico` is served from `icon.png` via nginx rewrite. The browser doesn't need an HTML link tag.

## Upstream config.toml mapping

| config.toml | Env var | Notes |
|---|---|---|
| `[core].server` | `APP_BITCOIN_NODE_IP` + `APP_BITCOIN_RPC_PORT` | Set by Umbrel |
| `[core].zmqSequenceEndpoint` | `APP_BITCOIN_ZMQ_SEQUENCE_PORT` | Set by Umbrel |
| `[core].rpcRequestTimeoutSeconds` | `FRIGATE_RPC_REQUEST_TIMEOUT` | |
| `[core].rpcBatchSize` | `FRIGATE_RPC_BATCH_SIZE` | |
| `[index].cacheSize` | `FRIGATE_CACHE_SIZE` | |
| `[index].startHeight` | `FRIGATE_START_HEIGHT` | |
| `[scan].computeBackend` | `FRIGATE_COMPUTE_BACKEND` | Default `CPU` — change to `AUTO` for GPU |
| `[scan].batchSize` | `FRIGATE_BATCH_SIZE` | |
| `[scan].dbThreads` | `FRIGATE_DB_THREADS` | |
| `[scan].memoryLimit` | `FRIGATE_MEMORY_LIMIT` | |
| `[server].tcp` | `FRIGATE_PORT` | Fixed at 57001 |
| `[server].backendElectrumServer` | `APP_ELECTRS_NODE_IP` + `APP_ELECTRS_NODE_PORT` | Set by Umbrel |

## Umbrel constraints

- All `${...}` env vars injected by Umbrel runtime. `docker compose up` outside Umbrel will fail.
- Requires `bitcoin` + `electrs` Umbrel apps installed and synced.
- `exports.sh` reads Tor hostname file; empty string if Tor hasn't started.
- All images pinned by SHA256 digest.
- `server` runs as `root` (`user: "0:0"`), `tor` as `user: "1000:1000"`.
- Stop grace period on `server`: 1m.

## Ports

- `57001` — Frigate RPC
- `3068` — Umbrel UI (declared in manifest)
- `app_proxy` routes `3068` → `web:8080`

## Source repos

- **Upstream**: https://github.com/sparrowwallet/frigate
- **This wrapper**: https://github.com/AlbercoinDev/Umbrel-AppStore-Albercoin/tree/master/albercoin-store-frigate

## No developer tooling

No `package.json`, no build/lint/test commands. Validate: `docker-compose config` syntax check only.
