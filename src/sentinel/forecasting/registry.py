"""Builds a forecaster from its kind and artefact path, for the backend wiring.

Artefacts live outside git, under ``shared/civicwater/models/`` on the VM:

- ``baseline``: ``baseline-hourofweek@<version>.json``
- ``xgboost``: ``xgboost-33@<version>/`` (``meta.json`` + one JSON booster per sensor)
- ``gru``: ``gru-33@<version>/`` (``meta.json`` + ``model.weights.h5`` + ``model.onnx``)
"""

from pathlib import Path

from sentinel.forecasting.baseline import HourOfWeekForecaster
from sentinel.forecasting.gru import GRUForecaster
from sentinel.forecasting.integrity import artefact_sha256
from sentinel.forecasting.interface import Forecaster
from sentinel.forecasting.xgb import XGBoostForecaster

KINDS = ("baseline", "xgboost", "gru")


class ArtefactMismatchError(ValueError):
    """The artefact on disk is not the one pinned by ``SENTINEL_FORECASTER_SHA256``."""


def load_forecaster(
    kind: str,
    path: str | Path,
    *,
    engine: str = "snapml",
    tree_format: str = "auto",
    with_contributions: bool | str = True,
    expected_sha256: str | None = None,
) -> Forecaster:
    """``engine``, ``tree_format`` and ``with_contributions`` apply to ``xgboost`` only.

    ``with_contributions``: ``"all"`` (or ``True``), ``"on_alert"`` or ``"off"`` (or
    ``False``). On the LinuxONE VM, TreeSHAP takes ~127 of the ~134 ms per window and Snap
    ML's ``compress_trees`` was the fastest format (``docs/performance.md``); ``"on_alert"``
    pays TreeSHAP only for the deviating sensors of an alert.

    The artefact's SHA-256 (``sentinel.forecasting.integrity``) is computed **before** any file
    is parsed: with ``expected_sha256`` a different artefact raises ``ArtefactMismatchError``
    and nothing is loaded; either way the digest goes into the served version.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown forecaster kind {kind!r}; expected one of {KINDS}")
    digest = artefact_sha256(path)
    if expected_sha256 is not None and digest != expected_sha256:
        raise ArtefactMismatchError(
            f"forecaster artefact {path} has sha256 {digest}, expected {expected_sha256}"
        )
    if kind == "baseline":
        return HourOfWeekForecaster.load(path, artefact_sha256=digest)
    if kind == "xgboost":
        return XGBoostForecaster(
            path,
            engine=engine,
            tree_format=tree_format,
            with_contributions=with_contributions,
            artefact_sha256=digest,
        )
    if kind == "gru":
        return GRUForecaster(path, artefact_sha256=digest)
    raise ValueError(f"unknown forecaster kind {kind!r}; expected one of {KINDS}")
