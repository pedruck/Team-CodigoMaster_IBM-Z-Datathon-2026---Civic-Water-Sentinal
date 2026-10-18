from typing import Annotated

from fastapi import APIRouter, Query

from sentinel.api.deps import ExplanationsDep, PipelineDep
from sentinel.narrative.store import ExplanationStatus
from sentinel.schemas.alerts import Alert
from sentinel.schemas.api import AlertPage, ErrorResponse
from sentinel.schemas.common import UtcDatetime
from sentinel.services.errors import NotFoundError

router = APIRouter(prefix="/v1", tags=["alerts"])


@router.get("/alerts", response_model=AlertPage)
def list_alerts(
    pipeline: PipelineDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    since: UtcDatetime | None = None,
    until: UtcDatetime | None = None,
    region: str | None = None,
) -> AlertPage:
    """Alerts newest first; ``since``/``until`` filter on ``window_end`` (inclusive)."""
    items, total = pipeline.alerts.query(
        limit=limit, offset=offset, since=since, until=until, region=region
    )
    return AlertPage(items=tuple(items), total=total, limit=limit, offset=offset)


@router.get(
    "/alerts/{alert_id}",
    response_model=Alert,
    responses={404: {"model": ErrorResponse}},
)
def get_alert(alert_id: str, pipeline: PipelineDep) -> Alert:
    alert = pipeline.alerts.get(alert_id)
    if alert is None:
        raise NotFoundError(f"alert {alert_id!r} not found")
    return alert


@router.get(
    "/alerts/{alert_id}/explanation",
    response_model=ExplanationStatus,
    responses={404: {"model": ErrorResponse}},
)
def get_alert_explanation(alert_id: str, explanations: ExplanationsDep) -> ExplanationStatus:
    """Stored explanation of an alert (Q-AUD-07, audit owner's ``ExplanationService``):
    ``pending`` with the deterministic text until the narrator's text is recorded, then
    ``ready``. 404 for an unknown alert or when explanations are disabled."""
    status = explanations.status(alert_id)
    if status is None:
        raise NotFoundError(f"no explanation for alert {alert_id!r}")
    return status
