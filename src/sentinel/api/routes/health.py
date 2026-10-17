from fastapi import APIRouter

from sentinel import __version__
from sentinel.schemas.api import HealthResponse

router = APIRouter(tags=["operations"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness only: the process is up. Use /v1/status for readiness details."""
    return HealthResponse(status="ok", version=__version__)
