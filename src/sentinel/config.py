"""Runtime configuration read from environment variables (prefix ``SENTINEL_``)."""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_REGISTRY_PATH = Path("config/sensors.provisional.json")


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
    # Only "mock" exists for the forecaster until its owner delivers; any other value fails at
    # startup so a mock is never substituted silently (ADR-005).
    forecaster: str = "mock"
    # "mock" or "cusum" (ADR-007). The CUSUM detector has no numeric default: every number comes
    # from the calibration file, which has no default path either.
    detector: str = "mock"
    detector_calibration_path: Path | None = None
    detector_regions_path: Path | None = None  # optional sensor -> region mapping (ADR-007 §8)
    audit_sink: str = "null"
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
            ):
                if path is not None:
                    raise ConfigError(
                        f"{key} is set but SENTINEL_DETECTOR={self.detector}; "
                        "it only applies to SENTINEL_DETECTOR=cusum"
                    )

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
            forecaster=_choice(e, "SENTINEL_FORECASTER", "mock", {"mock"}),
            detector=_choice(e, "SENTINEL_DETECTOR", "mock", {"mock", "cusum"}),
            detector_calibration_path=_optional_path(e, "SENTINEL_DETECTOR_CALIBRATION"),
            detector_regions_path=_optional_path(e, "SENTINEL_DETECTOR_REGIONS"),
            audit_sink=_choice(e, "SENTINEL_AUDIT_SINK", "null", {"null", "memory"}),
            audit_pending_capacity=_int(e, "SENTINEL_AUDIT_PENDING_CAPACITY", 2016),
            mock_detector_sigma_m=_float(e, "SENTINEL_MOCK_DETECTOR_SIGMA_M", 1.0),
            mock_detector_z_threshold=_float(e, "SENTINEL_MOCK_DETECTOR_Z_THRESHOLD", 3.0),
            mock_detector_min_sensors=_int(e, "SENTINEL_MOCK_DETECTOR_MIN_SENSORS", 2),
            metrics_sample_size=_int(e, "SENTINEL_METRICS_SAMPLE_SIZE", 10_000),
            log_level=_choice(
                e, "SENTINEL_LOG_LEVEL", "INFO", {"DEBUG", "INFO", "WARNING", "ERROR"}
            ),
        )
