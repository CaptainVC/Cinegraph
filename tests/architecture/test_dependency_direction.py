"""Keep the hexagonal dependency direction enforceable and reviewable."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "cinegraph"
KNOWN_PORT_OUTER_LAYER_DEPENDENCIES = frozenset(
    {
        "catalogue/reviewed_subtitle_batch_loader.py",
        "conversation/conversation_thread_binding_repository.py",
        "conversation/conversational_agent.py",
        "conversation/series_conversational_agent.py",
        "evaluation/retrieval_evaluation_dataset_loader.py",
        "llm/chat_model_gateway.py",
        "media/media_provider.py",
        "netflix_history/netflix_history_import_repository.py",
        "netflix_history/netflix_viewing_history_parser.py",
        "observability/audit_sink.py",
        "observability/media_action_audit_sink.py",
        "recommendation/episode_recommendation_ranker.py",
        "subtitle_processing/finalized_subtitle_canonicalizer.py",
    }
)


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


def test_ports_add_no_new_outer_layer_dependencies() -> None:
    forbidden = (
        "cinegraph.application",
        "cinegraph.adapters",
        "cinegraph.bootstrap",
        "cinegraph.infrastructure",
        "cinegraph.ingestion",
    )
    ports_root = SRC / "ports"
    violations = {
        path.relative_to(ports_root).as_posix()
        for path in ports_root.rglob("*.py")
        if any(
            imported == prefix or imported.startswith(f"{prefix}.")
            for imported in _imports(path)
            for prefix in forbidden
        )
    }
    assert violations == KNOWN_PORT_OUTER_LAYER_DEPENDENCIES
