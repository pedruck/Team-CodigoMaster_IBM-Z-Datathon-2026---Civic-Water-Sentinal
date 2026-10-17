"""Domain errors mapped to HTTP responses by ``sentinel.api.errors``."""

from typing import Any


class SentinelError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, details: dict[str, Any] | list[Any] | None = None):
        super().__init__(message)
        self.message = message
        self.details = details


class WindowRejectedError(SentinelError):
    """The window is valid JSON but violates the sensor registry or timestamp grid."""

    status_code = 422

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message, details)
        self.code = code


class TemporalConflictError(SentinelError):
    """The window conflicts with what was already accepted (out of order / different data)."""

    status_code = 409

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None):
        super().__init__(message, details)
        self.code = code


class NotFoundError(SentinelError):
    status_code = 404
    code = "not_found"
