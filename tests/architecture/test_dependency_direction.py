"""Keep the hexagonal dependency direction enforceable and reviewable."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "cinegraph"


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_domain_does_not_depend_on_outer_layers() -> None:
    forbidden = ("cinegraph.application", "cinegraph.adapters", "cinegraph.infrastructure")
    for path in (SRC / "domain").rglob("*.py"):
        assert not any(
            imported == prefix or imported.startswith(f"{prefix}.")
            for imported in _imports(path)
            for prefix in forbidden
        ), path


def test_application_depends_on_ports_not_concrete_adapters() -> None:
    forbidden = ("cinegraph.adapters", "cinegraph.infrastructure")
    for path in (SRC / "application").rglob("*.py"):
        assert not any(
            imported == prefix or imported.startswith(f"{prefix}.")
            for imported in _imports(path)
            for prefix in forbidden
        ), path
