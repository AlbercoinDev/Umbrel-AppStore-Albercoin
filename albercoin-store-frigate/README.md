# Frigate for Umbrel

Umbrel package for [Frigate Electrum Server](https://github.com/sparrowwallet/frigate), a Bitcoin Silent Payments (BIP352) Electrum server.

The app starts only the web controller after installation. Frigate itself starts after the first-run setup saves the initial block height. The default mainnet height is `709632`, Bitcoin Taproot activation.

## Requirements

- Bitcoin Core with `txindex=1`
- Electrs installed as the Electrum backend
- Around 18 GB of disk for the Frigate DuckDB index
- 8 GB RAM minimum; 16 GB recommended

## Ports

- UI: `2107`
- Frigate TCP: `57001`

## Data

Persistent data is stored under Umbrel's app data directory at `data/frigate`.

The UI runs from the public `node:22-bookworm-slim` image and Frigate runs from `ghcr.io/remcoros/frigate-docker:latest`, so no custom image build is required during Umbrel installation.
