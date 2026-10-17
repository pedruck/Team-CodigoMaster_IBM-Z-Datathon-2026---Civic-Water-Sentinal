"""Application factory. Run with: ``uvicorn sentinel.main:create_app --factory``."""

import logging
import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response

from sentinel import __version__
from sentinel.api.errors import install_error_handlers
from sentinel.api.routes import alerts, health, status, windows
from sentinel.audit.sinks import AuditSink, InMemoryAuditSink, NullAuditSink
from sentinel.config import ConfigError, Settings
from sentinel.detection.interface import Detector
from sentinel.detection.mock import ThresholdMockDetector
from sentinel.forecasting.interface import Forecaster
from sentinel.forecasting.mock import PersistenceMockForecaster
from sentinel.forecasting.registry import load_forecaster
from sentinel.observability.logging import configure_logging
from sentinel.observability.metrics import Metrics
from sentinel.sensors import SensorRegistry
from sentinel.services.pipeline import Pipeline

log = logging.getLogger("sentinel")


def build_pipeline(
    settings: Settings,
    *,
    registry: SensorRegistry | None = None,
    forecaster: Forecaster | None = None,
    detector: Detector | None = None,
    audit_sink: AuditSink | None = None,
) -> Pipeline:
    """Wire the pipeline. Injected components win over settings (used by tests and by the
    real modules once delivered)."""
    registry = registry or SensorRegistry.load(settings.sensor_registry_path)
    if forecaster is None:
        forecaster = _configured_forecaster(settings)
    if detector is None:
        detector = ThresholdMockDetector(
            sigma_m=settings.mock_detector_sigma_m,
            z_threshold=settings.mock_detector_z_threshold,
            min_sensors=settings.mock_detector_min_sensors,
        )
    if audit_sink is None:
        audit_sink = InMemoryAuditSink() if settings.audit_sink == "memory" else NullAuditSink()
    return Pipeline(
        settings=settings,
        registry=registry,
        forecaster=forecaster,
        detector=detector,
        audit_sink=audit_sink,
        metrics=Metrics(settings.metrics_sample_size),
    )


def _configured_forecaster(settings: Settings) -> Forecaster:
    """Loads the forecaster named in the settings once, at startup (ADR-005).

    Any load error (missing artefact, ML library absent, feature drift) propagates and stops
    the process: falling back to the mock would hide that the real model is not running.
    """
    if settings.forecaster == "mock":
        return PersistenceMockForecaster()
    if settings.forecaster_path is None:  # Settings enforces it; kept for the type checker
        raise ConfigError(
            f"SENTINEL_FORECASTER={settings.forecaster} needs SENTINEL_FORECASTER_PATH"
        )
    forecaster = load_forecaster(
        settings.forecaster,
        settings.forecaster_path,
        engine=settings.forecaster_engine,
        tree_format=settings.forecaster_tree_format,
        with_contributions=settings.forecaster_contributions,
    )
    log.info(
        "forecaster loaded",
        extra={
            "fields": {
                "kind": settings.forecaster,
                "path": str(settings.forecaster_path),
                "model": forecaster.info.model_dump(),
                "contributions": settings.forecaster_contributions,
                "tree_format_used": getattr(forecaster, "tree_format_used", None),
            }
        },
    )
    return forecaster


def create_app(settings: Settings | None = None, pipeline: Pipeline | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    configure_logging(settings.log_level)
    pipeline = pipeline or build_pipeline(settings)

    app = FastAPI(
        title="CivicWater Sentinel - Backend & Ingestion",
        version=__version__,
        description="Ingestion of 5-minute SCADA windows (simulated BattLeDIM L-Town data).",
    )
    app.state.pipeline = pipeline
    install_error_handlers(app)
    for module in (health, windows, alerts, status):
        app.include_router(module.router)

    @app.middleware("http")
    async def _count_window_requests(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.method != "POST" or request.url.path != "/v1/windows":
            return await call_next(request)
        t = time.perf_counter()
        response = await call_next(request)
        pipeline.metrics.observe("http_post_windows", time.perf_counter() - t)
        pipeline.metrics.inc("http_windows_requests")
        if response.status_code >= 400:
            pipeline.metrics.inc("http_windows_errors")
            pipeline.metrics.inc(f"http_windows_{response.status_code}")
        return response

    log.info(
        "sentinel started",
        extra={
            "fields": {
                "forecaster": pipeline.forecaster.info.name,
                "detector": pipeline.detector.info.name,
                "audit_sink": pipeline.audit_sink.name,
                "mocks_active": pipeline.mocks_active,
                "registry_provisional": pipeline.registry.provisional,
            }
        },
    )
    if pipeline.mocks_active:
        log.warning("MOCK forecaster/detector active: results are not leak detections")
    if pipeline.registry.provisional:
        log.warning("PROVISIONAL sensor registry: IDs are placeholders, not BattLeDIM IDs")
    return app
