"""Dashboard configuration from environment variables."""

import os
from collections.abc import Mapping
from pathlib import Path

from dashboard.source import AlertSource, HttpAlertSource, PlaybackAlertSource

DEFAULT_API_URL = "http://127.0.0.1:8000"


def source_from_env(env: Mapping[str, str] = os.environ) -> AlertSource:
    """``SENTINEL_DASHBOARD_PLAYBACK_DIR`` set → recorded run from that directory; otherwise
    the backend at ``SENTINEL_DASHBOARD_API_URL`` (default local dev server) with
    ``SENTINEL_DASHBOARD_TIMEOUT_S`` (default 5)."""
    playback = env.get("SENTINEL_DASHBOARD_PLAYBACK_DIR")
    if playback:
        return PlaybackAlertSource(Path(playback))
    return HttpAlertSource(
        env.get("SENTINEL_DASHBOARD_API_URL", DEFAULT_API_URL),
        timeout_s=float(env.get("SENTINEL_DASHBOARD_TIMEOUT_S", "5")),
    )
