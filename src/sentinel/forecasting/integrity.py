"""SHA-256 of a forecaster artefact, so the audit names the exact weights that ran.

The artefact is one file (baseline) or a directory (``meta.json`` + one file per sensor, or
weights + ONNX). For a directory the digest covers every regular file under it, sorted by
relative POSIX path: ``SHA-256( for each file: path "\\0" sha256(file) "\\n" )``. Hidden entries
(``.ipynb_checkpoints``, ``.DS_Store``) are skipped: no loader reads them.

The backend puts the first 8 hex digits in the served version (``0.1.0+1a2b3c4d``, like the
detector's ``0.1.0+fad6ec0b``), logs the full digest, and refuses to start when
``SENTINEL_FORECASTER_SHA256`` is set and differs.
"""

import hashlib
from pathlib import Path

SHORT = 8


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def artefact_sha256(path: str | Path) -> str:
    root = Path(path)
    if root.is_file():
        return _file_sha256(root)
    if not root.is_dir():
        raise FileNotFoundError(f"forecaster artefact not found: {root}")
    files = sorted(
        p
        for p in root.rglob("*")
        if p.is_file() and not any(part.startswith(".") for part in p.relative_to(root).parts)
    )
    if not files:
        raise FileNotFoundError(f"forecaster artefact directory is empty: {root}")
    digest = hashlib.sha256()
    for p in files:
        digest.update(f"{p.relative_to(root).as_posix()}\0{_file_sha256(p)}\n".encode())
    return digest.hexdigest()


def versioned(version: str, sha256: str | None) -> str:
    """``0.1.0`` + digest → ``0.1.0+1a2b3c4d``; unchanged without a digest."""
    return f"{version}+{sha256[:SHORT]}" if sha256 else version


def release(version: str) -> str:
    """``0.1.0+1a2b3c4d`` → ``0.1.0``: the part a calibration names (ADR-008)."""
    return version.split("+", 1)[0]
