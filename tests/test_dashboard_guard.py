"""Guards for the dashboard boundary (spec: labels never reach the operator view; the
dashboard depends only on the contract models)."""

import ast
import re
from pathlib import Path

DASHBOARD = Path(__file__).resolve().parents[1] / "dashboard"
LABEL_REFERENCE = re.compile(r"leaks?_(?:20\d\d|label|start)|eval/v\d|ground[ _-]?truth", re.I)


def _sources() -> list[Path]:
    files = sorted(DASHBOARD.rglob("*.py"))
    assert files, "dashboard sources not found"
    return files


def test_dashboard_never_references_leak_labels() -> None:
    hits = [
        f"{path.name}:{n}: {line.strip()}"
        for path in _sources()
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if LABEL_REFERENCE.search(line)
    ]
    assert hits == []


def test_dashboard_imports_only_contract_models_from_backend() -> None:
    imported: set[str] = set()
    for path in _sources():
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    backend = {m for m in imported if m == "sentinel" or m.startswith("sentinel.")}
    assert backend, "expected the dashboard to use sentinel.schemas"
    assert {m for m in backend if not m.startswith("sentinel.schemas")} == set()
