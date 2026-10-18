"""2019 ground truth: reading and the check against the official list (ADR-010 §10).

Only the evaluation reads the 2019 labels (``eval/v1/leaks_2019.csv``, docs/data.md §6). Before
a run they must match the official BattLeDIM 2019 list, copied here from the competition's
scoring code (KIOS-Research/BattLeDIM, ``Scoring Algorithm/functions/readLeakageFile.m``, header
comment, revision August 2020): pipe, start, end (repair, or the end of the year), type. Times
are the competition's simulated clock, read as UTC like every window (docs/data.md §3).
"""

from pathlib import Path

import pandas as pd

OFFICIAL_2019_LEAKS: tuple[tuple[str, str, str, str], ...] = (
    ("p257", "2018-01-08 13:30", "2019-12-31 23:55", "incipient"),
    ("p427", "2018-02-13 08:25", "2019-12-31 23:55", "incipient"),
    ("p810", "2018-07-28 03:05", "2019-12-31 23:55", "incipient"),
    ("p654", "2018-07-05 03:40", "2019-12-31 23:55", "incipient"),
    ("p523", "2019-01-15 23:00", "2019-02-01 09:50", "abrupt"),
    ("p827", "2019-01-24 18:30", "2019-02-07 09:05", "abrupt"),
    ("p280", "2019-02-10 13:05", "2019-12-31 23:55", "abrupt"),
    ("p653", "2019-03-03 13:10", "2019-05-05 12:10", "incipient"),
    ("p710", "2019-03-24 14:15", "2019-12-31 23:55", "abrupt"),
    ("p514", "2019-04-02 20:40", "2019-05-23 14:55", "abrupt"),
    ("p331", "2019-04-20 10:10", "2019-12-31 23:55", "abrupt"),
    ("p193", "2019-05-19 10:40", "2019-12-31 23:55", "incipient"),
    ("p277", "2019-05-30 21:55", "2019-12-31 23:55", "incipient"),
    ("p142", "2019-06-12 19:55", "2019-07-17 09:25", "abrupt"),
    ("p680", "2019-07-10 08:45", "2019-12-31 23:55", "abrupt"),
    ("p586", "2019-07-26 14:40", "2019-09-16 03:20", "incipient"),
    ("p721", "2019-08-02 03:00", "2019-12-31 23:55", "incipient"),
    ("p800", "2019-08-16 14:00", "2019-10-01 16:35", "incipient"),
    ("p123", "2019-09-13 20:05", "2019-12-31 23:55", "incipient"),
    ("p455", "2019-10-03 14:00", "2019-12-31 23:55", "incipient"),
    ("p762", "2019-10-09 10:15", "2019-12-31 23:55", "incipient"),
    ("p426", "2019-10-25 13:25", "2019-12-31 23:55", "abrupt"),
    ("p879", "2019-11-20 11:55", "2019-12-31 23:55", "incipient"),
)


def read_labels(path: Path) -> pd.DataFrame:
    """The label CSV (``pipe, start, end, type, peak``, ...) with tz-aware UTC dates."""
    frame = pd.read_csv(path)
    for column in ("start", "end", "peak"):
        frame[column] = pd.to_datetime(frame[column]).dt.tz_localize("UTC")
    return frame


def check_official_labels(labels: pd.DataFrame) -> None:
    """``ValueError`` naming the pipes that differ from the official list (ADR-010 §10)."""
    official = {
        pipe: (pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"), kind)
        for pipe, start, end, kind in OFFICIAL_2019_LEAKS
    }
    given = {
        str(pipe): (start, end, kind)
        for pipe, start, end, kind in labels[["pipe", "start", "end", "type"]].itertuples(
            index=False, name=None
        )
    }
    differing = sorted(
        pipe for pipe in official.keys() | given.keys() if official.get(pipe) != given.get(pipe)
    )
    if differing:
        raise ValueError(
            f"labels differ from the official BattLeDIM 2019 list for {differing} (ADR-010 §10)"
        )
