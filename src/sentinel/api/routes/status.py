from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Request

from sentinel import __version__
from sentinel.api.deps import PipelineDep
from sentinel.schemas.api import StatusResponse

router = APIRouter(prefix="/v1", tags=["operations"])


@router.get("/status", response_model=StatusResponse)
def get_status(pipeline: PipelineDep, request: Request) -> StatusResponse:
    m = pipeline.metrics
    counters = m.counters()
    uptime = m.uptime_s()
    requests = counters.get("http_windows_requests", 0)
    errors = counters.get("http_windows_errors", 0)
    reg = pipeline.registry
    return StatusResponse(
        service="civicwater-sentinel-backend",
        version=__version__,
        started_at=datetime.now(UTC) - timedelta(seconds=uptime),
        uptime_s=round(uptime, 3),
        interval_seconds=pipeline.settings.interval_seconds,
        last_timestamp=pipeline.last_timestamp,
        history=pipeline.history_info(),
        sensor_registry={
            "provisional": reg.provisional,
            "source": reg.source,
            "units": reg.units,
            **reg.counts,
        },
        forecaster=pipeline.forecaster.info,
        detector=pipeline.detector.info,
        audit_sink=pipeline.audit_sink.name,
        mocks_active=pipeline.mocks_active,
        counters={**counters, "alerts_stored": len(pipeline.alerts)},
        latency=m.latency(),
        throughput_windows_per_s=round(counters.get("windows_accepted", 0) / uptime, 4)
        if uptime > 0
        else 0.0,
        error_rate=round(errors / requests, 6) if requests else 0.0,
    )
