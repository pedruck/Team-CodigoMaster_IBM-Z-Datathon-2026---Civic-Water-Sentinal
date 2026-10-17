"""Region ranking of the CUSUM detector (BE-012e, ADR-007 §8).

The sensor -> region mapping and every region name here are SYNTHETIC: invented for the tests,
written to ``tmp_path``; the real L-Town mapping is BE-012g. Sensor IDs are the placeholders
``P01..P33`` and the calibration is invented (see ``artefacts.py``). This exercises the
mechanics only and is not evidence of leak detection.
"""

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from sentinel.audit.sinks import NullAuditSink
from sentinel.config import ConfigError, Settings
from sentinel.detection.cusum import CusumDetector
from sentinel.detection.regions import RegionMapping
from sentinel.main import build_pipeline, create_app
from sentinel.schemas.common import ModelInfo
from sentinel.schemas.detections import DetectionResult
from sentinel.synthetic import window_payload
from tests.conftest import INTERVAL, ts
from tests.detection.artefacts import (
    FORECASTER_NAME,
    FORECASTER_VERSION,
    artefact_fields,
    make_artefact,
)
from tests.detection.test_end_to_end import (
    CALIBRATION,
    GROUP_A,
    GROUP_B,
    REGISTRY,
    STEP_AT,
    STEP_M,
    ProfileForecaster,
)

# Synthetic regions over the placeholder IDs; the file order is the tie-break order.
NORTH, SOUTH, REST = "R-north", "R-south", "R-rest"
SYNTHETIC_REGIONS: dict[str, list[str]] = {
    NORTH: list(GROUP_A),
    SOUTH: list(GROUP_B),
    REST: [s for s in REGISTRY.pressure if s not in GROUP_A + GROUP_B],
}


def write_regions(tmp_path: Path, regions: Mapping[str, Sequence[str]]) -> Path:
    path = tmp_path / "regions.synthetic.json"
    path.write_text(json.dumps({"regions": regions}), encoding="utf-8")
    return path


class Replayed(NamedTuple):
    alerts: list[dict[str, Any]]  # GET /v1/alerts, oldest first
    by_region: dict[str, list[dict[str, Any]]]  # GET /v1/alerts?region=<r> per synthetic region


def replay(tmp_path: Path, stepped: Callable[[int], tuple[str, ...]]) -> Replayed:
    """Replay one noise-free synthetic day through ``POST /v1/windows``; ``stepped(i)`` names
    the sensors reading ``STEP_M`` low at window i."""
    detector = CusumDetector(
        make_artefact(dict.fromkeys(REGISTRY.pressure, 0.1), **CALIBRATION),
        interval=INTERVAL,
        regions=RegionMapping.from_file(write_regions(tmp_path, SYNTHETIC_REGIONS)),
    )
    settings = Settings()
    pipeline = build_pipeline(
        settings,
        registry=REGISTRY,
        forecaster=ProfileForecaster(REGISTRY),
        detector=detector,
        audit_sink=NullAuditSink(),
    )
    with TestClient(create_app(settings, pipeline)) as client:
        for i in range(288):
            window = window_payload(ts(i), REGISTRY, drop_m=STEP_M, drop_sensors=stepped(i))
            assert client.post("/v1/windows", json=window).status_code == 201
        return Replayed(
            alerts=client.get("/v1/alerts").json()["items"],
            by_region={
                r: client.get("/v1/alerts", params={"region": r}).json()["items"]
                for r in SYNTHETIC_REGIONS
            },
        )


def test_step_on_the_sensors_of_a_region_opens_an_alert_in_that_region(tmp_path: Path) -> None:
    day = replay(tmp_path, lambda i: GROUP_B if i >= STEP_AT else ())

    (alert,) = day.alerts
    assert alert["region"] == SOUTH


def test_alerts_endpoint_returns_the_alert_only_for_its_region(tmp_path: Path) -> None:
    day = replay(tmp_path, lambda i: GROUP_B if i >= STEP_AT else ())

    assert [a["region"] for a in day.by_region[SOUTH]] == [SOUTH]
    assert day.by_region[NORTH] == []
    assert day.by_region[REST] == []


# Seam A: the detector alone, four invented sensors, sigma 0.1 m, k = 0.5, h = 8, N = 1, c = 2,
# h_close = 4. A 0.5 m drop gives d = 5, so S grows by 4.5 per window (4.5, 9.0, 13.5, cap 16).
SENSORS = ("P1", "P2", "P3", "P4")


def detector_over(regions: Mapping[str, Sequence[str]] | None) -> CusumDetector:
    return CusumDetector(
        make_artefact(dict.fromkeys(SENSORS, 0.1)),
        interval=INTERVAL,
        regions=None
        if regions is None
        else RegionMapping(regions={r: tuple(s) for r, s in regions.items()}),
    )


def feed(detector: CusumDetector, i: int, low: Sequence[str]) -> DetectionResult:
    """Window i with the sensors ``low`` reading 0.5 m below the expected 50 m."""
    observed = {s: 49.5 if s in low else 50.0 for s in SENSORS}
    return detector.evaluate(observed, dict.fromkeys(SENSORS, 50.0), ts(i))


HALVES = {"R1": ("P1", "P2"), "R2": ("P3", "P4")}


def test_region_scores_leave_out_the_sensors_of_an_older_episode() -> None:
    detector = detector_over(HALVES)

    feed(detector, 0, ["P1"])
    first = feed(detector, 1, ["P1"])  # S1 = 9.0 > h: the alert opens in R1
    during = feed(detector, 2, ["P1", "P3"])  # P1 is claimed, S3 = 4.5 is below h
    second = feed(detector, 3, ["P1", "P3"])  # S3 = 9.0 > h: a second alert, in R2

    assert first.region_scores == {"R1": 9.0, "R2": 0.0}
    assert during.region_scores == {"R1": 0.0, "R2": 0.0}
    assert second.region_scores == {"R1": 0.0, "R2": 9.0}
    assert second.anomaly_score == 9.0  # the same unclaimed set as the score
    assert [a.region for a in first.alerts + second.alerts] == ["R1", "R2"]


def test_tied_regions_resolve_to_the_first_region_of_the_file() -> None:
    # P1 and P3 reach the same S in the same window: both regions sum to 9.0
    names_not_alphabetical = {"R-b": ("P1", "P2"), "R-a": ("P3", "P4")}
    swapped = {"R-a": ("P3", "P4"), "R-b": ("P1", "P2")}

    regions = []
    for mapping in (names_not_alphabetical, swapped):
        detector = detector_over(mapping)
        feed(detector, 0, ["P1", "P3"])
        (alert,) = feed(detector, 1, ["P1", "P3"]).alerts
        regions.append(alert.region)

    assert regions == ["R-b", "R-a"]


def test_the_region_with_the_largest_sum_wins_even_if_it_comes_later_in_the_file() -> None:
    detector = detector_over(HALVES)

    feed(detector, 0, ["P1", "P3", "P4"])
    (alert,) = feed(detector, 1, ["P1", "P3", "P4"]).alerts  # R1: 9.0, R2: 9.0 + 9.0

    assert alert.region == "R2"


def test_regions_id_is_the_sha256_of_the_mapping_file(tmp_path: Path) -> None:
    path = write_regions(tmp_path, HALVES)
    detector = CusumDetector(
        make_artefact(dict.fromkeys(SENSORS, 0.1)),
        interval=INTERVAL,
        regions=RegionMapping.from_file(path),
    )

    result = feed(detector, 0, [])

    assert result.diagnostics["regions_id"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_regions_id_of_a_mapping_in_memory_follows_its_content_and_region_order() -> None:
    a = RegionMapping(regions={"R1": ("P1", "P2"), "R2": ("P3", "P4")})

    same = RegionMapping(regions={"R1": ("P1", "P2"), "R2": ("P3", "P4")})
    reordered = RegionMapping(regions={"R2": ("P3", "P4"), "R1": ("P1", "P2")})

    assert a.regions_id == same.regions_id
    assert a.regions_id != reordered.regions_id  # the file order is the tie-break order
    assert len(a.regions_id) == 64


def test_without_a_mapping_region_and_scores_stay_null_and_nothing_else_changes() -> None:
    plain, mapped = detector_over(None), detector_over(HALVES)
    pairs = [(feed(plain, i, low), feed(mapped, i, low)) for i, low in enumerate(WALK)]

    assert all(p.region_scores is None for p, _ in pairs)
    assert all(a.region is None for p, _ in pairs for a in p.alerts)
    assert all("regions_id" not in p.diagnostics for p, _ in pairs)
    for p, m in pairs:  # the mapping adds the region fields and nothing else
        assert (
            p.model_dump()
            == m.model_copy(
                update={
                    "region_scores": None,
                    "alerts": tuple(a.model_copy(update={"region": None}) for a in m.alerts),
                    "diagnostics": {k: v for k, v in m.diagnostics.items() if k != "regions_id"},
                }
            ).model_dump()
        )
    assert any(p.alerts for p, _ in pairs)  # the walk does open an alert


WALK: tuple[list[str], ...] = (["P1"], ["P1"], ["P1", "P3"], ["P1", "P3"], [], [], [], [])


@pytest.mark.parametrize(
    "regions",
    [
        pytest.param({"R1": ("P1", "P2", "P3")}, id="sensor-missing"),
        pytest.param({"R1": ("P1", "P2"), "R2": ("P3", "P4", "P5")}, id="sensor-unknown"),
    ],
)
def test_detector_rejects_a_mapping_that_does_not_cover_exactly_its_sensors(
    regions: Mapping[str, Sequence[str]],
) -> None:
    with pytest.raises(ValueError, match="calibration"):
        detector_over(regions)


@pytest.mark.parametrize(
    "content",
    [
        pytest.param({"regions": {}}, id="no-regions"),
        pytest.param({"regions": {"R1": [], "R2": ["P1"]}}, id="region-without-sensors"),
        pytest.param({"regions": {"R1": ["P1", "P1"]}}, id="sensor-repeated-in-a-region"),
        pytest.param({"regions": {"R1": ["P1"], "R2": ["P1"]}}, id="sensor-in-two-regions"),
        pytest.param({"regions": {"R1": ["P1"]}, "note": "x"}, id="unknown-key"),
    ],
)
def test_mapping_file_with_an_invalid_shape_is_rejected(
    tmp_path: Path, content: dict[str, Any]
) -> None:
    path = tmp_path / "regions.synthetic.json"
    path.write_text(json.dumps(content), encoding="utf-8")

    with pytest.raises(ValidationError):
        RegionMapping.from_file(path)


# Seam E: the factory the server uses at startup.
FORECASTER = ModelInfo(name=FORECASTER_NAME, version=FORECASTER_VERSION, is_mock=True)


def for_pipeline(
    tmp_path: Path,
    regions: Mapping[str, Sequence[str]],
    pressure_sensors: Sequence[str] = SENSORS,
) -> CusumDetector:
    calibration = tmp_path / "calibration.synthetic.json"
    calibration.write_text(json.dumps(artefact_fields(dict.fromkeys(SENSORS, 0.1))))
    return CusumDetector.for_pipeline(
        calibration,
        interval=INTERVAL,
        pressure_sensors=pressure_sensors,
        forecaster=FORECASTER,
        regions=write_regions(tmp_path, regions),
    )


def test_pipeline_detector_loads_the_mapping_that_covers_the_registry(tmp_path: Path) -> None:
    detector = for_pipeline(tmp_path, HALVES)

    result = feed(detector, 0, ["P3"])

    assert result.region_scores == {"R1": 0.0, "R2": 0.0}
    assert (
        result.diagnostics["regions_id"]
        == hashlib.sha256((tmp_path / "regions.synthetic.json").read_bytes()).hexdigest()
    )


@pytest.mark.parametrize(
    "regions",
    [
        pytest.param({"R1": ("P1", "P2"), "R2": ("P3",)}, id="registry-sensor-missing"),
        pytest.param({"R1": ("P1", "P2"), "R2": ("P3", "P4", "P5")}, id="sensor-not-in-registry"),
    ],
)
def test_startup_fails_when_the_mapping_does_not_cover_exactly_the_pressure_sensors(
    tmp_path: Path, regions: Mapping[str, Sequence[str]]
) -> None:
    with pytest.raises(ConfigError, match="regions"):
        for_pipeline(tmp_path, regions)
