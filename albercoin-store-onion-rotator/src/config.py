import os
import re

UMBREL_ROOT = os.environ.get("UMBREL_ROOT", "/home/umbrel/umbrel")
TOR_DATA_DIR = os.environ.get("TOR_DATA_DIR", os.path.join(UMBREL_ROOT, "tor", "data"))
UMBREL_APP_DATA_DIR = os.environ.get("UMBREL_APP_DATA_DIR", os.path.join(UMBREL_ROOT, "app-data"))
DRY_RUN = os.environ.get("ONION_ROTATOR_DRY_RUN", "false").lower() == "true"
DEBUG = os.environ.get("ONION_ROTATOR_DEBUG", "false").lower() == "true"
LOG_MAX_LINES = 500
POLL_INTERVAL = 3
POLL_TIMEOUT = 120
APP_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]+$")
ONION_V3_REGEX = re.compile(r"^[a-z2-7]{56}\.onion$")
