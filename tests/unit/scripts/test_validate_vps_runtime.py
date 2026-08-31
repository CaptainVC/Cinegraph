import errno
import socket
from pathlib import Path

import pytest
from scripts.validate_vps_runtime import (
    parse_env_file,
    validate_env_file,
    validate_runtime,
)


def _write_env(path: Path, *, environment: str = "production") -> None:
    path.write_text(
        "\n".join(
            [
                f"CINEGRAPH_ENVIRONMENT={environment}",
                f"CINEGRAPH_ENV_FILE={path}",
                f"CINEGRAPH_STACK_NAME={'cinegraph-prod' if environment == 'production' else 'cinegraph-dev'}",
                "CINEGRAPH_QDRANT_MODE=remote",
                f"CINEGRAPH_QDRANT_COLLECTION_NAME={'transcript_segments_production' if environment == 'production' else 'transcript_segments_development'}",
                f"CINEGRAPH_PUBLISHED_PORT={'18001' if environment == 'production' else '18000'}",
                f"CINEGRAPH_IDENTITY_DATABASE_PATH=/app/knowledge/cinegraph-{'production' if environment == 'production' else 'development'}.sqlite3",
                "CINEGRAPH_IMAGE=ghcr.io/captainvc/cinegraph",
                "CINEGRAPH_IMAGE_DIGEST=sha256:0000000000000000000000000000000000000000000000000000000000000000",
                "CINEGRAPH_RELEASE_SHA=0000000000000000000000000000000000000000",
                "CINEGRAPH_API_HOST=0.0.0.0",
                "CINEGRAPH_API_PORT=8000",
                "OPENAI_API_KEY=real-key",
                f"POSTGRES_USER={'cinegraph_prod' if environment == 'production' else 'cinegraph_dev'}",
                "POSTGRES_PASSWORD=long-random-password",
                f"POSTGRES_DB={'cinegraph_prod' if environment == 'production' else 'cinegraph_dev'}",
                f"CINEGRAPH_DATABASE_URL=postgresql+psycopg://{'cinegraph_prod' if environment == 'production' else 'cinegraph_dev'}:long-random-password@postgres:5432/{'cinegraph_prod' if environment == 'production' else 'cinegraph_dev'}",
                "CINEGRAPH_QDRANT_URL=http://qdrant:6333",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


def test_parse_env_file_ignores_comments_and_preserves_values(tmp_path: Path) -> None:
    path = tmp_path / "runtime.env"
    path.write_text("# comment\nA=value=with=equals\n", encoding="utf-8")

    assert parse_env_file(path) == {"A": "value=with=equals"}


def test_production_env_contract_passes_without_running_external_tools(tmp_path: Path) -> None:
    path = tmp_path / "prod.env"
    _write_env(path)

    assert validate_env_file(path, "production") == []
    assert (
        validate_runtime(
            path,
            tmp_path / "compose.yaml",
            check_tools=False,
            check_compose=False,
            check_host=False,
        )
        == []
    )


def test_production_rejects_placeholders_and_non_postgres_database(tmp_path: Path) -> None:
    path = tmp_path / "prod.env"
    _write_env(path)
    content = path.read_text(encoding="utf-8")
    path.write_text(
        content.replace("real-key", "REPLACE_WITH_OPENAI_KEY").replace(
            "@postgres:5432", "@other:5432"
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)

    codes = {issue.code for issue in validate_env_file(path, "production")}
    assert "secret-placeholder" in codes
    assert "database-credentials" in codes


def test_development_still_requires_remote_qdrant_for_compose(tmp_path: Path) -> None:
    path = tmp_path / "dev.env"
    _write_env(path, environment="development")
    content = path.read_text(encoding="utf-8").replace("CINEGRAPH_QDRANT_MODE=remote", "CINEGRAPH_QDRANT_MODE=local")
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)

    assert any(issue.code == "qdrant-mode" for issue in validate_env_file(path, "development"))


def test_malformed_service_urls_fail_closed_without_echoing_values(tmp_path: Path) -> None:
    path = tmp_path / "prod.env"
    _write_env(path)
    content = path.read_text(encoding="utf-8")
    path.write_text(
        content.replace(
            "postgresql+psycopg://cinegraph_prod:long-random-password@postgres:5432/cinegraph_prod",
            "postgresql+psycopg://[",
        ).replace("http://qdrant:6333", "http://["),
        encoding="utf-8",
    )
    path.chmod(0o600)

    issues = validate_env_file(path, "production")

    assert {issue.code for issue in issues} >= {"database-credentials", "qdrant-url"}
    assert all("postgresql+psycopg://[" not in issue.message for issue in issues)
    assert all("http://[" not in issue.message for issue in issues)


def test_active_port_can_only_be_allowed_explicitly(tmp_path: Path) -> None:
    from scripts.validate_vps_runtime import validate_host

    path = tmp_path / "dev.env"
    _write_env(path, environment="development")
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        content = path.read_text(encoding="utf-8").replace(
            "CINEGRAPH_PUBLISHED_PORT=18000", f"CINEGRAPH_PUBLISHED_PORT={port}"
        )
        path.write_text(content, encoding="utf-8")
        path.chmod(0o600)
        assert any(issue.code == "port-in-use" for issue in validate_host(path))
        assert not any(
            issue.code == "port-in-use" for issue in validate_host(path, allow_active_port=True)
        )


def test_allow_active_port_does_not_suppress_unexpected_bind_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.validate_vps_runtime import validate_host

    class FailingSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def bind(self, _address):
            raise OSError(errno.EACCES, "denied")

    path = tmp_path / "dev.env"
    _write_env(path, environment="development")
    monkeypatch.setattr(socket, "socket", lambda *_args, **_kwargs: FailingSocket())

    issues = validate_host(path, allow_active_port=True)

    assert any(issue.code == "port-check" for issue in issues)
    assert not any("denied" in issue.message for issue in issues)


def test_container_and_image_contracts_fail_closed(tmp_path: Path) -> None:
    path = tmp_path / "prod.env"
    _write_env(path)
    content = path.read_text(encoding="utf-8").replace(
        "CINEGRAPH_API_HOST=0.0.0.0", "CINEGRAPH_API_HOST=127.0.0.1"
    ).replace(
        "CINEGRAPH_IMAGE_DIGEST=sha256:0000000000000000000000000000000000000000000000000000000000000000",
        "CINEGRAPH_IMAGE_DIGEST=latest",
    )
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)

    codes = {issue.code for issue in validate_env_file(path, "production")}
    assert {"api-host", "image-digest"}.issubset(codes)


def test_image_reference_must_match_approved_registry_and_name(tmp_path: Path) -> None:
    path = tmp_path / "prod.env"
    _write_env(path)
    content = path.read_text(encoding="utf-8").replace(
        "CINEGRAPH_IMAGE=ghcr.io/captainvc/cinegraph",
        "CINEGRAPH_IMAGE=docker.io/captainvc/cinegraph:sha-0000000000000000000000000000000000000000",
    )
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)

    assert any(issue.code == "image-name" for issue in validate_env_file(path, "production"))


def test_digest_and_release_sha_are_strict_lowercase_immutable_values(tmp_path: Path) -> None:
    path = tmp_path / "prod.env"
    _write_env(path)
    content = path.read_text(encoding="utf-8").replace(
        "sha256:0000000000000000000000000000000000000000000000000000000000000000",
        "sha256:ABC",
    ).replace(
        "CINEGRAPH_RELEASE_SHA=0000000000000000000000000000000000000000",
        "CINEGRAPH_RELEASE_SHA=ABC",
    )
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)
    codes = {issue.code for issue in validate_env_file(path, "production")}

    assert {"image-digest", "release-sha"}.issubset(codes)


def test_environment_contract_prevents_dev_prod_reuse(tmp_path: Path) -> None:
    path = tmp_path / "prod.env"
    _write_env(path, environment="production")
    path.write_text(
        path.read_text(encoding="utf-8")
        .replace("CINEGRAPH_STACK_NAME=cinegraph-prod", "CINEGRAPH_STACK_NAME=cinegraph-dev")
        .replace(
            "CINEGRAPH_QDRANT_COLLECTION_NAME=transcript_segments_production",
            "CINEGRAPH_QDRANT_COLLECTION_NAME=transcript_segments_development",
        )
        .replace("CINEGRAPH_PUBLISHED_PORT=18001", "CINEGRAPH_PUBLISHED_PORT=18000")
        .replace("cinegraph-production.sqlite3", "cinegraph-development.sqlite3")
        .replace("POSTGRES_USER=cinegraph_prod", "POSTGRES_USER=cinegraph_dev")
        .replace("POSTGRES_DB=cinegraph_prod", "POSTGRES_DB=cinegraph_dev"),
        encoding="utf-8",
    )
    path.chmod(0o600)

    issues = validate_env_file(path, "production")

    assert sum(issue.code == "environment-isolation" for issue in issues) == 6
