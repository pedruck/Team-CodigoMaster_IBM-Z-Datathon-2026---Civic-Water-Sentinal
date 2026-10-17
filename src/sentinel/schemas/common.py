"""Types shared by every contract."""

from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import AfterValidator, AwareDatetime, BaseModel, BeforeValidator, ConfigDict, Field


def _reject_non_numbers(value: Any) -> Any:
    # bool is a subclass of int and strings are coerced in lax mode: both are rejected so a
    # malformed SCADA export never becomes a plausible pressure value.
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("must be a JSON number")
    return value


def _to_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


FiniteFloat = Annotated[
    float,
    BeforeValidator(_reject_non_numbers),
    Field(allow_inf_nan=False),
]
"""A finite JSON number (int or float). NaN, +/-Infinity, strings and booleans are rejected."""

UtcDatetime = Annotated[AwareDatetime, AfterValidator(_to_utc)]
"""Timezone-aware datetime, normalised to UTC. Naive datetimes are rejected."""


class ContractModel(BaseModel):
    """Base for every contract model: immutable, unknown fields rejected."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class ModelInfo(ContractModel):
    """Identifies the component that produced a forecast or a detection."""

    name: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=32)
    is_mock: bool = Field(
        description="True for test doubles. Results of a mock are never evidence of detection."
    )
