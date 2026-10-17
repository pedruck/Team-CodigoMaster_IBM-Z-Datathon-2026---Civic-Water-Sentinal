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
        {"SENTINEL_DETECTOR": "cusum"},
        {"SENTINEL_REQUIRE_ALIGNED_TIMESTAMPS": "maybe"},
    ],
)
def test_invalid_configuration_fails_at_startup(env: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        Settings.from_env(env)
