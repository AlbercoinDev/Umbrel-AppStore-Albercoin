# AGENTS.md

Custom Umbrel app store ("Albercoin Store"). Five apps live as sibling
`albercoin-store-*` directories at the repo root. Docs and most user-facing prose
are Spanish; keep new documentation in Spanish unless an existing app's doc is
English.

## Ground truth

- App version: `albercoin-store-*/umbrel-app.yml`.
- Runtime image/digest, volumes, ports and env: that app's `docker-compose.yml`.
- Binary URL/hash and image labels: `Dockerfile`; publication: `.github/workflows/`.
- README versions can lag. Example: `albercoin-store-simplex-chat-cli/README.md`
  still says 1.0.4 while the manifest, Dockerfile and workflow are 1.0.5.
- Directory names, app IDs, ports, `${APP_DATA_DIR}` paths and raw GitHub URLs
  are deployment contracts. Keep app directories at the root; do not restructure.
- Editing sources here does not update an installed app. Umbrel consumes pinned
  images/digests (and for Onion Rotator, the installed `APP_DATA_DIR/src` copy).
  Rebuild/publish, update the Compose digest, then reinstall/upgrade.

## Per-app gotchas

| App | What is easy to miss |
| --- | --- |
| e-lite | AMD64 only. The GHCR image contains the multi-network Python controller; e-lite, Bitcoin Core and Tor are downloaded and SHA256-verified on first start. Compose publishes six ports: mainnet 55011/55022, testnet4 55111/55122, signet 55211/55222. Tor is managed by e-lite per network; there is no Tor sidecar. Per-network ports, metrics, data dirs, TOML, `public-servers.toml` and rate-limit settings are generated/handled in code. The image is pinned by digest. |
| Frigate | No Dockerfile. The Python web UI and the runtime shell script live inline in `docker-compose.yml`; `tests/test_frigate.py` extracts them from exact `- |` / `command: |` blocks. Keep the manifest revision, `FRIGATE_VERSION` and both arch SHA256 values aligned. Real use requires the `bitcoin` and `electrs` apps. |
| Onion Rotator | Stdlib-only Python in `src/`. Its `Dockerfile` is unused by Compose, which runs `python:3.12-slim` and mounts `${APP_DATA_DIR}/src`. Compose needs the Docker socket and real Tor data; real rotation deletes `hs_ed25519_*` keys and restarts containers. |
| Machine Doctor | FastAPI image pinned at `0.1.9`; no automated test suite. `src/modules/{disk,thermal,ram}` contain only future-scope READMEs, not implementations. Runs privileged with host `/proc`, `/sys`, `/dev`. |
| SimpleX Chat CLI | `1.0.5`, image pinned by digest. Python `unittest` plus a separate JS DOM test. `tests/integration.py` is destructive and only run manually inside Docker with disposable identities. |
| SimpleX Server | Preview `0.1.0`, build `tor-check-1`; version is intentionally unchanged. Dockerfile composes official SMP plus a CLI binary copied from the chat-cli image. Most real tests need a locally built Docker image. |

## Commands

e-lite, from the app directory (offline, stdlib only):

```sh
cd albercoin-store-e-lite
PYTHONPATH=src python3 -m unittest discover -s tests -v
docker build -t e-lite:dev .
```

Frigate, from the repo root (offline, stdlib only):

```sh
python3 -m unittest discover -s albercoin-store-frigate/tests -v
```

Onion Rotator:

```sh
cd albercoin-store-onion-rotator
python -m pip install -r requirements.txt pytest
PYTHONPATH=src python -m pytest tests/ -v
PYTHONPATH=src python -m pytest tests/test_detector.py -v   # focused file
./scripts/test.sh                                            # installs into active venv
```

SimpleX Chat CLI:

```sh
cd albercoin-store-simplex-chat-cli
python3 -m pip install -r requirements.txt
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s tests -p 'test_network.py' -v   # focused file

npm install --prefix /tmp/opencode/simplex-ui-tests jsdom@26
NODE_PATH=/tmp/opencode/simplex-ui-tests/node_modules node tests/ui.cjs
```

SimpleX Server (unit tests and smoke tests run inside the image; `smoke_packaged.py`
runs on the host and drives Docker):

```sh
docker build -t simplex-server:dev albercoin-store-simplex-server
docker run --rm --entrypoint python3 -v "$PWD/albercoin-store-simplex-server:/workspace:ro" \
  simplex-server:dev -m unittest discover -s /workspace/tests -v
docker run --rm --entrypoint python3 -v "$PWD/albercoin-store-simplex-server:/workspace:ro" \
  simplex-server:dev /workspace/tests/smoke_runtime.py
python3 albercoin-store-simplex-server/tests/smoke_packaged.py
```

Online/optional probes (`smoke_diagnostics.py`, `smoke_upgrade.py`,
`probe_client.py`, `PACKAGED_TOR_PROBE=1`) need Tor egress or extra images;
see the server README before running them.

Machine Doctor has no test suite. Do not run `stress-ng` on the host; manual
checks belong in a disposable container with `TEST_DURATION_SECONDS=30`.

## CI and release traps

- `machine-doctor-image.yml` runs automatically on pushes to `master` touching
  that app, and publishes `0.1.9` and `latest`. Treat changes there as a release.
- `e-lite-image.yml` runs automatically on pushes to `master` touching e-lite and
  publishes only AMD64 to GHCR. Do not add ARM64 labels unless e-lite is built
  from source for ARM64. Pin the digest in Compose after the first publish.
- `simplex-chat-cli-image.yml` and `simplex-server-image.yml` are manual;
  `publish` defaults to false. Do not enable publishing unless asked.
- `albercoin-store-onion-rotator/scripts/publish.sh` runs `git push origin master`
  for the entire repo, not just that app.
- `master` is the branch Umbrel consumes. There is no repo-wide lint, format,
  typecheck or test workflow, and no root package manager/lockfile. Run each
  app's tests from its directory.
- Version changes cross-cut several files: manifest, Dockerfile label/ARG,
  workflow tag and Compose digest. A republished tag does not change a digest-pinned
  Compose reference. SimpleX Server keeps `0.1.0`; same-version upgrades may need
  the documented forced CLI upgrade.

## Safety

- Never point tests or dev scripts at production Tor data, the real Docker socket,
  real SimpleX identities, or Umbrel app data. Use Onion Rotator's
  `ONION_ROTATOR_DRY_RUN=true` and ephemeral containers for SimpleX tests.
- Frigate's start-height change deletes/reindexes the database; do not treat it as
  a cosmetic setting.
- Do not claim tests or node deployments were executed when they were not. The
  docs deliberately distinguish available tests, previously documented checks and
  newly executed verification.

## References

- `docs/architecture.md`: directory map, ports, permissions, persistent data.
- `docs/development.md`: per-app test overview and environment limits.
- `docs/releasing.md`: publication, version and pre-release checklist.
