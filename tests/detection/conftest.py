import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture
def work() -> Iterator[Path]:
    """A fresh temporary directory whose path cannot name 2019.

    The offline calibration tools refuse any path with ``2019`` (ADR-008 §A). pytest's
    ``tmp_path`` lives under a numbered ``pytest-<N>`` directory that will one day be
    ``pytest-2019``; ``mkdtemp`` with a fixed prefix does not depend on that counter.
    """
    path = Path(tempfile.mkdtemp(prefix="p4-calibration-"))
    if "2019" in str(path):  # a random suffix that happens to contain it: draw again
        shutil.rmtree(path)
        path = Path(tempfile.mkdtemp(prefix="p4-calibration-"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)
