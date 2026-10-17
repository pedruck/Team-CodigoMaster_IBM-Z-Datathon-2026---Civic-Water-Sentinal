"""What the header shows, derived only from the backend's own status (no wall clock)."""

from dataclasses import dataclass

from sentinel.schemas.api import StatusResponse
from sentinel.schemas.common import ModelInfo


@dataclass(frozen=True)
class Header:
    online: bool
    simulated_time: str
    forecaster: str
    detector: str
    simulated: bool
    provisional_sensors: bool


def _model(info: ModelInfo) -> str:
    return f"{info.name} {info.version}" + (" (mock)" if info.is_mock else "")


def build_header(status: StatusResponse | None, online: bool) -> Header:
    if status is None:
        return Header(
            online=online,
            simulated_time="unknown",
            forecaster="unknown",
            detector="unknown",
            simulated=False,
            provisional_sensors=False,
        )
    last = status.last_timestamp
    return Header(
        online=online,
        simulated_time=last.strftime("%Y-%m-%d %H:%M UTC") if last else "no window yet",
        forecaster=_model(status.forecaster),
        detector=_model(status.detector),
        simulated=status.mocks_active,
        provisional_sensors=bool(status.sensor_registry.get("provisional", True)),
    )
