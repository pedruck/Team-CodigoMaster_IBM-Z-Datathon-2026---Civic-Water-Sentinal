from pathlib import Path

import pytest

from sentinel.config import ConfigError, Settings


def test_defaults_match_documented_values() -> None:
    s = Settings.from_env({})
    assert (s.interval_seconds, s.history_capacity, s.min_history_windows) == (300, 288, 144)
    assert (s.forecaster, s.detector, s.audit_sink) == ("mock", "mock", "null")


def test_env_overrides_are_parsed() -> None:
    s = Settings.from_env(
        {
            "SENTINEL_HISTORY_CAPACITY": "144",
            "SENTINEL_MIN_HISTORY_WINDOWS": "12",
            "SENTINEL_REQUIRE_ALIGNED_TIMESTAMPS": "false",
        }
    )
    assert (s.history_capacity, s.min_history_windows, s.require_aligned_timestamps) == (
        144,
        12,
        False,
    )


@pytest.mark.parametrize(
    "env",
    [
        {"SENTINEL_HISTORY_CAPACITY": "abc"},
        {"SENTINEL_HISTORY_CAPACITY": "0"},
        {"SENTINEL_MIN_HISTORY_WINDOWS": "500"},
        {"SENTINEL_FORECASTER": "gru"},
        {"SENTINEL_DETECTOR": "kalman"},
        {"SENTINEL_REQUIRE_ALIGNED_TIMESTAMPS": "maybe"},
    ],
)
def test_invalid_configuration_fails_at_startup(env: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        Settings.from_env(env)


def test_cusum_detector_is_selected_with_its_calibration_file() -> None:
    s = Settings.from_env(
        {
            "SENTINEL_DETECTOR": "cusum",
            "SENTINEL_DETECTOR_CALIBRATION": "run/calibration.json",
        }
    )
    assert s.detector == "cusum"
    assert s.detector_calibration_path == Path("run/calibration.json")
    assert s.detector_regions_path is None


def test_cusum_detector_accepts_an_optional_region_mapping() -> None:
    s = Settings.from_env(
        {
            "SENTINEL_DETECTOR": "cusum",
            "SENTINEL_DETECTOR_CALIBRATION": "run/calibration.json",
            "SENTINEL_DETECTOR_REGIONS": "run/regions.json",
        }
    )
    assert s.detector_regions_path == Path("run/regions.json")


def test_detector_files_default_to_none_with_the_mock_detector() -> None:
    s = Settings.from_env({})
    assert (s.detector_calibration_path, s.detector_regions_path) == (None, None)


def test_cusum_detector_without_a_calibration_file_fails_at_startup() -> None:
    with pytest.raises(ConfigError, match="SENTINEL_DETECTOR_CALIBRATION"):
        Settings.from_env({"SENTINEL_DETECTOR": "cusum"})


def test_region_mapping_without_a_calibration_file_fails_at_startup() -> None:
    env = {"SENTINEL_DETECTOR": "cusum", "SENTINEL_DETECTOR_REGIONS": "run/regions.json"}
    with pytest.raises(ConfigError, match="SENTINEL_DETECTOR_CALIBRATION"):
        Settings.from_env(env)


@pytest.mark.parametrize("key", ["SENTINEL_DETECTOR_CALIBRATION", "SENTINEL_DETECTOR_REGIONS"])
@pytest.mark.parametrize("detector", [None, "mock"])
def test_detector_files_are_not_ignored_by_the_mock_detector(
    key: str, detector: str | None
) -> None:
    env = {key: "run/file.json"} | ({} if detector is None else {"SENTINEL_DETECTOR": detector})
    with pytest.raises(ConfigError, match=key):
        Settings.from_env(env)


@pytest.mark.parametrize("key", ["SENTINEL_DETECTOR_CALIBRATION", "SENTINEL_DETECTOR_REGIONS"])
def test_an_empty_detector_file_path_fails_instead_of_meaning_the_current_directory(
    key: str,
) -> None:
    env = {"SENTINEL_DETECTOR": "cusum", "SENTINEL_DETECTOR_CALIBRATION": "run/c.json", key: " "}
    with pytest.raises(ConfigError, match=key):
        Settings.from_env(env)
