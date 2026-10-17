from fastapi import APIRouter, Response, status

from sentinel.api.deps import PipelineDep
from sentinel.schemas.api import ErrorResponse, WindowResult
from sentinel.schemas.windows import SensorWindow

router = APIRouter(prefix="/v1", tags=["ingestion"])


@router.post(
    "/windows",
    response_model=WindowResult,
    status_code=status.HTTP_201_CREATED,
    responses={
        200: {"model": WindowResult, "description": "Idempotent resend of an accepted window"},
        409: {"model": ErrorResponse, "description": "out_of_order or window_conflict"},
        422: {"model": ErrorResponse, "description": "Invalid payload or sensor set"},
    },
)
def ingest_window(window: SensorWindow, pipeline: PipelineDep, response: Response) -> WindowResult:
    """Accept one window and process it synchronously.

    201: accepted now (``status`` tells whether it was scored). 200: same window resent;
    the stored result is returned with ``duplicate=true`` and nothing is reprocessed.
    """
    outcome = pipeline.ingest(window)
    if not outcome.created:
        response.status_code = status.HTTP_200_OK
    return outcome.result
