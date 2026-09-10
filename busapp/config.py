"""Configuration. Everything tunable lives here, read from the environment."""
from __future__ import annotations

import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(HERE, "data")
PUBLIC_DIR = os.path.join(HERE, "public")
NETWORK_PATH = os.path.join(DATA_DIR, "network.json")
DB_PATH = os.environ.get("BUSAPP_DB", os.path.join(DATA_DIR, "busapp.sqlite3"))

# LTA DataMall. Without a key the app runs on synthetic data and says so loudly.
DATAMALL_KEY = os.environ.get("DATAMALL_API_KEY", "").strip()
DATAMALL_URL = "https://datamall2.mytransport.sg/ltaodataservice/v3/BusArrival"

HOST = os.environ.get("BUSAPP_HOST", "127.0.0.1")
PORT = int(os.environ.get("BUSAPP_PORT", "8080"))

# Singapore has no DST, so a fixed offset is correct and avoids a tzdata dependency.
SGT_OFFSET_HOURS = 8

# --- Collector -------------------------------------------------------------
POLL_INTERVAL_S = int(os.environ.get("BUSAPP_POLL_INTERVAL", "60"))

# A forward jump in the leading bus's ETA this large means the previous bus left.
DEPARTURE_JUMP_S = 120

# Gaps longer than this are service breaks (overnight, disruption), not headways.
MAX_HEADWAY_S = 5400
MIN_HEADWAY_S = 30

# Raw observations are pruned after this long; derived arrivals are kept forever.
RAW_RETENTION_DAYS = int(os.environ.get("BUSAPP_RAW_RETENTION_DAYS", "30"))

# --- Confidence ------------------------------------------------------------
# How many observed arrivals a time bucket needs before we make a claim.
# Below MIN_FOR_CLAIM the app refuses to quote a buffer. No fake confidence.
MIN_FOR_CLAIM = 10
CONFIDENCE_TIERS = ((30, "good"), (10, "fair"), (1, "thin"), (0, "none"))

# Quantile we plan to. 0.9 = "right nine mornings in ten".
PLANNING_QUANTILE = 0.9

# Margin used before any history exists, stated plainly in the UI.
DEFAULT_MARGIN_S = 120

# --- Getting out of the building -------------------------------------------
# CT Hub 2 is a tower: the lift wait happens before the walk to the stop even
# starts. Unlike the margin, this is not measured -- it is a stated assumption,
# so it is configurable and always shown to the user as its own line.
EXIT_BUFFER_MIN = int(os.environ.get("BUSAPP_EXIT_BUFFER_MIN", "5"))
EXIT_BUFFER_S = EXIT_BUFFER_MIN * 60
