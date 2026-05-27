# Onion Rotator — Umbrel App

Rotate Tor onion addresses for installed Umbrel apps.

## What it does

Onion Rotator lets you view all `.onion` addresses of your installed Umbrel applications
and selectively regenerate them. When you rotate an onion address:

1. The old `.onion` address stops working
2. Tor generates a new random `.onion` address
3. The app is automatically restarted
4. Your app remains functional with the new address

## ⚠️ Security Warning

Rotating an onion address will make the **previous address permanently stop working**
after the app restarts. Make sure you have updated any external services that rely on
the old address before rotating.

## How to install

### From Community App Store (recommended)

1. Open your Umbrel dashboard
2. Go to **App Store**
3. Click on the gear icon (⚙️) to manage stores
4. Add the Albercoin Store:
   ```
   https://github.com/AlbercoinDev/Umbrel-AppStore-Albercoin
   ```
5. Find **Onion Rotator** in the store and install it

### Via CLI

```bash
sudo ~/umbrel/scripts/repo add https://github.com/AlbercoinDev/Umbrel-AppStore-Albercoin
sudo ~/umbrel/scripts/repo update
```

Then install from the Umbrel UI.

## How to use

1. Open Onion Rotator from your Umbrel dashboard (port 2180)
2. The main page shows all detected apps with Tor onion addresses
3. Check the apps you want to rotate
4. Click **"Rotate selected onion addresses"**
5. Confirm the action in the dialog
6. Wait for the operation to complete
7. The new onion addresses will be displayed

### Copy an address

Click the **Copy** button next to any onion address to copy it to your clipboard.

### Refresh

Click **Refresh** to rescan and update the list of apps.

## How it works

### Tor data detection

The app scans the Umbrel Tor data directory (typically `/home/umbrel/umbrel/tor/data/`)
for subdirectories matching `app-<id>` and reads the `hostname` file inside each one.

### App restart

The app uses the Docker socket (`/var/run/docker.sock`) to find containers by
the `com.docker.compose.project` label matching the app ID, and restarts them.

### Regeneration flow

For each selected app:
1. Validates the app ID
2. Deletes the `hostname` file from the Tor data directory
3. Restarts the app's Docker containers
4. Waits up to 120 seconds for Tor to regenerate a new hostname
5. Returns the old and new onion addresses

## Dry run mode

Set the environment variable `ONION_ROTATOR_DRY_RUN=true` to enable dry-run mode.
In this mode, no files are deleted and no apps are restarted — the app only simulates
the operations.

```yaml
environment:
  ONION_ROTATOR_DRY_RUN: "true"
```

## Debug mode

Set `ONION_ROTATOR_DEBUG=true` for verbose logging.

## Local development

```bash
# Prerequisites
python 3.12+
pip

# Setup
cd albercoin-store-onion-rotator
pip install -r requirements.txt

# Run with mock data
ONION_ROTATOR_DRY_RUN=true \
ONION_ROTATOR_DEBUG=true \
TOR_DATA_DIR=/tmp/onion-rotator-dev-tor \
uvicorn src.main:app --host 0.0.0.0 --port 8900 --reload

# Or use the dev script
./scripts/dev.sh
```

### Mock Tor data

The dev script automatically creates mock Tor data at `/tmp/onion-rotator-dev-tor`
with fake apps (`bitcoin`, `electrs`, `lnd`, `cln`) for testing.

## Running tests

```bash
./scripts/test.sh
# Or directly:
pip install pytest
python -m pytest tests/ -v
```

## Permissions required

- **Tor data directory** (`/home/umbrel/umbrel/tor/data`): Read/write access to
  read and delete `hostname` files
- **Docker socket** (`/var/run/docker.sock`): Required to restart app containers
  after rotating their onion addresses

## Limitations

- Only detects apps managed by Umbrel (with `app-` prefix in Tor data directory)
- Requires Tor hidden services to be enabled for apps
- Maximum wait time for new hostname generation is 120 seconds
- Onion v3 only (v2 onions are deprecated and not supported)

## Privacy

- No telemetry, analytics, or external calls
- Onion addresses are never sent outside the device
- All operations are local

## Files structure

```
albercoin-store-onion-rotator/
├── umbrel-app.yml          # Umbrel app manifest
├── docker-compose.yml      # Docker services
├── Dockerfile              # Container build
├── requirements.txt        # Python dependencies
├── icon.svg                # App icon
├── README.md               # This file
├── src/
│   ├── main.py             # FastAPI app + API endpoints
│   ├── config.py           # Configuration
│   ├── detector.py         # Tor hostname scanning
│   ├── rotator.py          # Rotation logic
│   ├── restarter.py        # Docker-based app restart
│   ├── models.py           # Pydantic models
│   ├── i18n.py             # Translations (EN/ES)
│   └── static/
│       └── index.html      # Web interface
├── tests/                  # Unit tests
└── scripts/
    ├── dev.sh              # Development server
    ├── test.sh             # Run tests
    └── publish.sh          # Publish to GitHub
```

## Report issues

https://github.com/AlbercoinDev/Umbrel-AppStore-Albercoin/issues
