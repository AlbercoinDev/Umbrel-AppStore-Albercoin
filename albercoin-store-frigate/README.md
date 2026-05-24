# Frigate for Umbrel

Umbrel package for [sparrowwallet/frigate](https://github.com/sparrowwallet/frigate), an experimental Electrum-style Silent Payments server for Sparrow Wallet.

This package adds a local web panel for editing Frigate performance settings without SSH.

## Included services

- `server`: Frigate server image.
- `web`: configuration panel on Umbrel app port `3068` through `app_proxy`.
- `init`: creates persistent directories and first `config.toml`.
- `tor`: exposes Frigate RPC over onion.

## Persistent paths

Umbrel stores runtime data under `${APP_DATA_DIR}/data`:

```text
/data/frigate-home/config.toml
/data/frigate-settings.json
/data/frigate.log
/data/db/
```

Frigate reads its Linux config from `~/.frigate/config.toml`. In this package, `${APP_DATA_DIR}/data/frigate-home` is mounted as `/root/.frigate` inside the server container.

## Web panel

The panel edits:

- `cacheSize`
- `computeBackend`
- `dbThreads`
- `memoryLimit`
- `batchSize`
- `rpcBatchSize`
- `rpcRequestTimeoutSeconds`
- `maxLabels`
- `maxSubscriptions`
- `tcpPort`

Buttons:

- Apply low/balanced/fast/max profile.
- Save configuration.
- Save and restart Frigate server.
- Stop/start/restart only the `server` container.

## Security note

The panel mounts `/var/run/docker.sock` so it can restart only the Frigate server container after saving `config.toml`. Keep the app behind Umbrel/local access only. Do not expose the panel directly to the public internet.

## Recommended profiles

### Low RAM

```toml
[index]
cacheSize = "1M"

[scan]
computeBackend = "CPU"
dbThreads = 1
memoryLimit = "2GB"
batchSize = 50000
```

### Balanced

```toml
[index]
cacheSize = "2M"

[scan]
computeBackend = "CPU"
dbThreads = 2
memoryLimit = "3GB"
batchSize = 100000
```

### Fast

```toml
[index]
cacheSize = "5M"

[scan]
computeBackend = "AUTO"
dbThreads = 4
memoryLimit = "6GB"
batchSize = 300000
```

## Install in a custom Umbrel app store

Place this folder as:

```text
umbrel-app-store/
└── albercoin-store-frigate/
    ├── docker-compose.yml
    ├── umbrel-app.yml
    ├── exports.sh
    ├── torrc.template
    ├── icon.png
    └── web/
        ├── Dockerfile
        └── app.py
```

Then commit and push to GitHub.

## Notes

- Bitcoin Core must have `txindex=1`.
- The Bitcoin app must be installed and synced.
- Electrs is used as backend Electrum server.
- Initial indexing can take hours on mainnet.
