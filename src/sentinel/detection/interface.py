"""Detector port. The real residual + CUSUM + region ranking belongs to the detection owner
and must satisfy this protocol; see docs/integration-contracts.md §3."""

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol, runtime_checkable

from sentinel.schemas.common import ModelInfo
from sentinel.schemas.detections import DetectionResult


class DetectionError(Exception):
    """Expected, recoverable detection failure. The detector state must be left unchanged."""


@runtime_checkable
class Detector(Protocol):
    """Stateful across windows (CUSUM). The backend keeps ONE instance per process and calls
    ``evaluate`` strictly in timestamp order, never concurrently.

    Atomicity: ``evaluate`` either updates its state and returns, or raises without having
    changed its state.
    """

    @property
    def info(self) -> ModelInfo: ...

    def evaluate(
        self,
        observed: Mapping[str, float],
        expected: Mapping[str, float],
        timestamp: datetime,
    ) -> DetectionResult: ...
