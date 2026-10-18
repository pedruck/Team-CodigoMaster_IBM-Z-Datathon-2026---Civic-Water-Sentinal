"""Dashboard configuration from environment variables."""

import os
from collections.abc import Mapping
from pathlib import Path

from dashboard.source import (
    AlertSource,
    FallbackAlertSource,
    HttpAlertSource,
    PlaybackAlertSource,
    SnapshotAlertSource,
)

DEFAULT_API_URL = "http://127.0.0.1:8000"


def source_from_env(env: Mapping[str, str] = os.environ) -> AlertSource:
    """Where the dashboard reads from, first match wins:

    - ``SENTINEL_DASHBOARD_PLAYBACK_DIR``: a recorded run in that directory;
    - ``SENTINEL_DASHBOARD_SNAPSHOT_URL``: the snapshot the VM pushes to external storage (an
      https URL, e.g. a pre-signed GET URL, or a local file), stale after
      ``SENTINEL_DASHBOARD_SNAPSHOT_MAX_AGE_S`` seconds (default 300);
    - otherwise the backend at ``SENTINEL_DASHBOARD_API_URL`` (default local dev server), with
      ``SENTINEL_DASHBOARD_API_TOKEN`` as bearer token when it is the read-only gateway.

    ``SENTINEL_DASHBOARD_TIMEOUT_S`` (default 5) bounds every request. With
    ``SENTINEL_DASHBOARD_FALLBACK_DIR`` set, a live source that does not answer is replaced by
    that recorded run until it answers again. On Streamlit Community Cloud, root-level secrets
    reach the app as these environment variables (BE-026)."""
    playback = env.get("SENTINEL_DASHBOARD_PLAYBACK_DIR")
    if playback:
        return PlaybackAlertSource(Path(playback))
    timeout_s = float(env.get("SENTINEL_DASHBOARD_TIMEOUT_S", "5"))
    live: AlertSource
    snapshot = env.get("SENTINEL_DASHBOARD_SNAPSHOT_URL")
    if snapshot:
        live = SnapshotAlertSource(
            snapshot,
            timeout_s=timeout_s,
            max_age_s=float(env.get("SENTINEL_DASHBOARD_SNAPSHOT_MAX_AGE_S", "300")),
        )
    else:
        live = HttpAlertSource(
            env.get("SENTINEL_DASHBOARD_API_URL", DEFAULT_API_URL),
            timeout_s=timeout_s,
            token=env.get("SENTINEL_DASHBOARD_API_TOKEN") or None,
        )
    fallback = env.get("SENTINEL_DASHBOARD_FALLBACK_DIR")
    if fallback:
        return FallbackAlertSource(live, PlaybackAlertSource(Path(fallback)))
    return live


DEFAULT_REFRESH_S = 5.0


def refresh_interval_s(env: Mapping[str, str] = os.environ) -> float:
    """Seconds between refreshes of the alert list: ``SENTINEL_DASHBOARD_REFRESH_S``
    (default 5). Also the first back-off step after a failure."""
    raw = env.get("SENTINEL_DASHBOARD_REFRESH_S", str(DEFAULT_REFRESH_S))
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if not value > 0:
        raise ValueError(f"SENTINEL_DASHBOARD_REFRESH_S must be a positive number, got {raw!r}")
    return value
