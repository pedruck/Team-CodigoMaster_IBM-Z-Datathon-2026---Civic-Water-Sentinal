"""Builds a forecaster from its kind and artefact path, for the backend wiring.

Artefacts live outside git, under ``shared/civicwater/models/`` on the VM:

- ``baseline``: ``baseline-hourofweek@<version>.json``
- ``xgboost``: ``xgboost-33@<version>/`` (``meta.json`` + one JSON booster per sensor)
- ``gru``: ``gru-33@<version>/`` (``meta.json`` + ``model.weights.h5`` + ``model.onnx``)
"""

from pathlib import Path

from sentinel.forecasting.baseline import HourOfWeekForecaster
from sentinel.forecasting.gru import GRUForecaster
from sentinel.forecasting.interface import Forecaster
from sentinel.forecasting.xgb import XGBoostForecaster

KINDS = ("baseline", "xgboost", "gru")


def load_forecaster(
    kind: str,
    path: str | Path,
    *,
    engine: str = "snapml",
    tree_format: str = "auto",
    with_contributions: bool = True,
) -> Forecaster:
    """``engine``, ``tree_format`` and ``with_contributions`` apply to ``xgboost`` only.

    On the LinuxONE VM, TreeSHAP takes ~127 of the ~134 ms per window and Snap ML's
    ``compress_trees`` was the fastest format (``docs/performance.md``), so a long replay can
    load the model with ``with_contributions=False`` and the live demo with the defaults.
    """
    if kind == "baseline":
        return HourOfWeekForecaster.load(path)
    if kind == "xgboost":
        return XGBoostForecaster(
            path, engine=engine, tree_format=tree_format, with_contributions=with_contributions
        )
    if kind == "gru":
        return GRUForecaster(path)
    raise ValueError(f"unknown forecaster kind {kind!r}; expected one of {KINDS}")
