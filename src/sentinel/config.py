"""Runtime configuration read from environment variables (prefix ``SENTINEL_``)."""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from sentinel.audit.chain import audit_path_for

DEFAULT_REGISTRY_PATH = Path("config/sensors.provisional.json")
FORECASTERS = {"mock", "baseline", "xgboost", "gru"}
# Snap ML ``import_model(tree_format=...)``; "auto" picks the NNPA (zdnn_tensors) on LinuxONE.
TREE_FORMATS = {"auto", "compress_trees", "zdnn_tensors"}
AUDIT_SINKS = {"null", "memory", "chain"}
# Who writes the stored explanations (P5, sentinel.narrative); "none" = deterministic text only.
NARRATORS = {"none", "llamacpp", "local", "watsonx"}
MIN_TOKEN_LENGTH = 16
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ConfigError(ValueError):
    """Invalid configuration; raised at startup, never at request time."""


def _int(env: Mapping[str, str], key: str, default: int, minimum: int = 1) -> int:
    raw = env.get(key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ConfigError(f"{key} must be >= {minimum}, got {value}")
    return value


def _float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number, got {raw!r}") from exc
    if not value > 0:
        raise ConfigError(f"{key} must be > 0, got {value}")
    return value


def _bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None:
        return default
    if raw.lower() in {"1", "true", "yes"}:
        return True
    if raw.lower() in {"0", "false", "no"}:
        return False
    raise ConfigError(f"{key} must be a boolean, got {raw!r}")


def _contributions(env: Mapping[str, str], key: str) -> str:
    """``all | on_alert | off``; the booleans of the original switch still work
    (true = all, false = off). Default ``on_alert``: TreeSHAP only for the sensors of an
    alert."""
    raw = env.get(key, "on_alert").lower()
    if raw in {"all", "on_alert", "off"}:
        return raw
    if raw in {"1", "true", "yes"}:
        return "all"
    if raw in {"0", "false", "no"}:
        return "off"
    raise ConfigError(f"{key} must be all, on_alert or off (or a boolean), got {raw!r}")


def _ingest_token(env: Mapping[str, str]) -> str | None:
    """``SENTINEL_INGEST_TOKEN`` or the first line of ``SENTINEL_INGEST_TOKEN_FILE`` (preferred
    on the shared VM: a value on a command line is visible in ``ps``)."""
    raw, path = env.get("SENTINEL_INGEST_TOKEN"), env.get("SENTINEL_INGEST_TOKEN_FILE")
    if raw is not None and path is not None:
        raise ConfigError("set SENTINEL_INGEST_TOKEN or SENTINEL_INGEST_TOKEN_FILE, not both")
    if path is not None:
        try:
            raw = Path(path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ConfigError(f"SENTINEL_INGEST_TOKEN_FILE: cannot read {path}: {exc}") from exc
    return raw


def _choice(env: Mapping[str, str], key: str, default: str, allowed: set[str]) -> str:
    value = env.get(key, default)
    if value not in allowed:
        raise ConfigError(f"{key} must be one of {sorted(allowed)}, got {value!r}")
    return value


def _optional_path(env: Mapping[str, str], key: str) -> Path | None:
    raw = env.get(key)
    if raw is None:
        return None
    if not raw.strip():  # Path("") would silently mean the current directory
        raise ConfigError(f"{key} must not be empty; unset it instead")
    return Path(raw)


@dataclass(frozen=True, slots=True)
class Settings:
    interval_seconds: int = 300
    history_capacity: int = 288
    min_history_windows: int = 144
    require_aligned_timestamps: bool = True
    # Timestamps without timezone: "UTC" = interpret as UTC, "reject" = 422 (ADR-004).
    naive_timestamp_tz: str = "UTC"
    result_capacity: int = 2016
    alert_capacity: int = 10_000
    sensor_registry_path: Path = DEFAULT_REGISTRY_PATH
    # A window more than this ahead of the last accepted one is refused (0 = no limit): one
    # wrong timestamp would otherwise make every later window "out of order" (ADR-004).
    max_gap_seconds: int = 86_400
    # A window later than the server clock + this is refused (replays are in the past).
    future_tolerance_seconds: int = 3_600
    # Bearer token required on POST /v1/windows; None = open (development only).
    ingest_token: str | None = field(default=None, repr=False)
    max_body_bytes: int = 65_536
    # Real forecasters need their artefact and are loaded at startup; a load failure stops the
    # process, so a mock is never substituted silently (ADR-005).
    forecaster: str = "mock"
    forecaster_path: Path | None = None
    # XGBoost only: TreeSHAP costs ~127 of ~134 ms per window on the VM (docs/performance.md).
    forecaster_contributions: str = "on_alert"
    forecaster_engine: str = "snapml"
    forecaster_tree_format: str = "auto"
    # Expected SHA-256 of the artefact (sentinel.forecasting.integrity); startup fails if it
    # differs. None = not pinned (the digest is still computed, logged and put in the version).
    forecaster_sha256: str | None = None
    # "mock" or "cusum" (ADR-007). The CUSUM detector has no numeric default: every number comes
    # from the calibration file, which has no default path either.
    detector: str = "mock"
    detector_calibration_path: Path | None = None
    detector_regions_path: Path | None = None  # optional sensor -> region mapping (ADR-007 §8)
    detector_network_path: Path | None = None  # optional pipe network, candidate pipe (ADR-011)
    audit_sink: str = "null"
    # "chain" only: the run names the logs <run>.jsonl and <run>.explanations.jsonl, in
    # audit_dir (default ~/shared/civicwater/audit, Q-AUD-02).
    audit_run: str | None = None
    audit_dir: Path | None = None
    narrator: str = "none"
    # Audit events waiting for the sink (fail-closed, ADR-006); beyond this, windows get 503.
    audit_pending_capacity: int = 2016
    mock_detector_sigma_m: float = 1.0
    mock_detector_z_threshold: float = 3.0
    mock_detector_min_sensors: int = 2
    metrics_sample_size: int = 10_000
    log_level: str = "INFO"

    def __post_init__(self) -> None:
        if self.min_history_windows > self.history_capacity:
            raise ConfigError(
                "SENTINEL_MIN_HISTORY_WINDOWS "
                f"({self.min_history_windows}) exceeds SENTINEL_HISTORY_CAPACITY "
                f"({self.history_capacity})"
            )
        if self.forecaster not in FORECASTERS:
            raise ConfigError(
                f"SENTINEL_FORECASTER must be one of {sorted(FORECASTERS)}, got {self.forecaster!r}"
            )
        if self.forecaster != "mock" and self.forecaster_path is None:
            raise ConfigError(
                f"SENTINEL_FORECASTER={self.forecaster} needs SENTINEL_FORECASTER_PATH"
            )
        if self.forecaster_contributions not in {"all", "on_alert", "off"}:
            raise ConfigError(
                "SENTINEL_FORECASTER_CONTRIBUTIONS must be all, on_alert or off, got "
                f"{self.forecaster_contributions!r}"
            )
        if self.forecaster_tree_format not in TREE_FORMATS:
            raise ConfigError(
                "SENTINEL_FORECASTER_TREE_FORMAT must be one of "
                f"{sorted(TREE_FORMATS)}, got {self.forecaster_tree_format!r}"
            )
        self._check_audit()
        self._check_integrity()
        if self.detector == "cusum":
            if self.detector_calibration_path is None:
                raise ConfigError(
                    "SENTINEL_DETECTOR=cusum requires SENTINEL_DETECTOR_CALIBRATION "
                    "(the calibration artefact, ADR-008)"
                )
        else:
            for key, path in (
                ("SENTINEL_DETECTOR_CALIBRATION", self.detector_calibration_path),
                ("SENTINEL_DETECTOR_REGIONS", self.detector_regions_path),
                ("SENTINEL_DETECTOR_NETWORK", self.detector_network_path),
            ):
                if path is not None:
                    raise ConfigError(
                        f"{key} is set but SENTINEL_DETECTOR={self.detector}; "
                        "it only applies to SENTINEL_DETECTOR=cusum"
                    )

    def _check_integrity(self) -> None:
        if self.ingest_token is not None and len(self.ingest_token) < MIN_TOKEN_LENGTH:
            raise ConfigError(f"the ingest token must have at least {MIN_TOKEN_LENGTH} characters")
        if self.forecaster_sha256 is not None:
            if self.forecaster == "mock":
                raise ConfigError("SENTINEL_FORECASTER_SHA256 is set but SENTINEL_FORECASTER=mock")
            if not _SHA256.fullmatch(self.forecaster_sha256):
                raise ConfigError("SENTINEL_FORECASTER_SHA256 must be 64 lowercase hex digits")

    def _check_audit(self) -> None:
        if self.audit_sink not in AUDIT_SINKS:
            raise ConfigError(
                f"SENTINEL_AUDIT_SINK must be one of {sorted(AUDIT_SINKS)}, got {self.audit_sink!r}"
            )
        if self.narrator not in NARRATORS:
            raise ConfigError(
                f"SENTINEL_NARRATOR must be one of {sorted(NARRATORS)}, got {self.narrator!r}"
            )
        if self.audit_sink != "chain":
            for key, value in (
                ("SENTINEL_AUDIT_RUN", self.audit_run),
                ("SENTINEL_AUDIT_DIR", self.audit_dir),
            ):
                if value is not None:
                    raise ConfigError(
                        f"{key} is set but SENTINEL_AUDIT_SINK={self.audit_sink}; "
                        "it only applies to SENTINEL_AUDIT_SINK=chain"
                    )
            if self.narrator != "none":
                raise ConfigError(
                    f"SENTINEL_NARRATOR={self.narrator} needs SENTINEL_AUDIT_SINK=chain: "
                    "explanations are stored next to the audit chain"
                )
            return
        if self.audit_run is None:
            raise ConfigError(
                "SENTINEL_AUDIT_SINK=chain requires SENTINEL_AUDIT_RUN (names the audit log)"
            )
        try:
            audit_path_for(self.audit_run, self.audit_dir)
        except ValueError as exc:
            raise ConfigError(f"SENTINEL_AUDIT_RUN: {exc}") from exc

    @property
    def audit_path(self) -> Path | None:
        """The audit chain file, ``None`` unless ``audit_sink == "chain"``."""
        if self.audit_sink != "chain" or self.audit_run is None:
            return None
        return audit_path_for(self.audit_run, self.audit_dir)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env
        return cls(
            interval_seconds=_int(e, "SENTINEL_INTERVAL_SECONDS", 300),
            history_capacity=_int(e, "SENTINEL_HISTORY_CAPACITY", 288),
            min_history_windows=_int(e, "SENTINEL_MIN_HISTORY_WINDOWS", 144),
            require_aligned_timestamps=_bool(e, "SENTINEL_REQUIRE_ALIGNED_TIMESTAMPS", True),
            naive_timestamp_tz=_choice(e, "SENTINEL_NAIVE_TIMESTAMP_TZ", "UTC", {"UTC", "reject"}),
            result_capacity=_int(e, "SENTINEL_RESULT_CAPACITY", 2016),
            alert_capacity=_int(e, "SENTINEL_ALERT_CAPACITY", 10_000),
            sensor_registry_path=Path(
                e.get("SENTINEL_SENSOR_REGISTRY", str(DEFAULT_REGISTRY_PATH))
            ),
            max_gap_seconds=_int(e, "SENTINEL_MAX_GAP_SECONDS", 86_400, minimum=0),
            future_tolerance_seconds=_int(e, "SENTINEL_FUTURE_TOLERANCE_SECONDS", 3_600, minimum=0),
            ingest_token=_ingest_token(e),
            max_body_bytes=_int(e, "SENTINEL_MAX_BODY_BYTES", 65_536, minimum=1024),
            forecaster=_choice(e, "SENTINEL_FORECASTER", "mock", FORECASTERS),
            forecaster_path=_optional_path(e, "SENTINEL_FORECASTER_PATH"),
            forecaster_contributions=_contributions(e, "SENTINEL_FORECASTER_CONTRIBUTIONS"),
            forecaster_engine=_choice(
                e, "SENTINEL_FORECASTER_ENGINE", "snapml", {"snapml", "xgboost"}
            ),
            forecaster_tree_format=_choice(
                e, "SENTINEL_FORECASTER_TREE_FORMAT", "auto", TREE_FORMATS
            ),
            forecaster_sha256=e.get("SENTINEL_FORECASTER_SHA256"),
            detector=_choice(e, "SENTINEL_DETECTOR", "mock", {"mock", "cusum"}),
            detector_calibration_path=_optional_path(e, "SENTINEL_DETECTOR_CALIBRATION"),
            detector_regions_path=_optional_path(e, "SENTINEL_DETECTOR_REGIONS"),
            detector_network_path=_optional_path(e, "SENTINEL_DETECTOR_NETWORK"),
            audit_sink=_choice(e, "SENTINEL_AUDIT_SINK", "null", AUDIT_SINKS),
            audit_run=e.get("SENTINEL_AUDIT_RUN"),
            audit_dir=_optional_path(e, "SENTINEL_AUDIT_DIR"),
            narrator=_choice(e, "SENTINEL_NARRATOR", "none", NARRATORS),
            audit_pending_capacity=_int(e, "SENTINEL_AUDIT_PENDING_CAPACITY", 2016),
            mock_detector_sigma_m=_float(e, "SENTINEL_MOCK_DETECTOR_SIGMA_M", 1.0),
            mock_detector_z_threshold=_float(e, "SENTINEL_MOCK_DETECTOR_Z_THRESHOLD", 3.0),
            mock_detector_min_sensors=_int(e, "SENTINEL_MOCK_DETECTOR_MIN_SENSORS", 2),
            metrics_sample_size=_int(e, "SENTINEL_METRICS_SAMPLE_SIZE", 10_000),
            log_level=_choice(
                e, "SENTINEL_LOG_LEVEL", "INFO", {"DEBUG", "INFO", "WARNING", "ERROR"}
            ),
        )
