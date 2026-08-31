from pathlib import Path

from scripts import warmup_embeddings


def test_warmup_cli_success_prints_only_public_models_and_dimension(monkeypatch, capsys) -> None:
    class Result:
        dense_model = "public-dense"
        dense_dimension = 384
        sparse_model = "public-sparse"

    monkeypatch.setattr(warmup_embeddings, "warmup_fastembed_models", lambda *_args: Result())

    assert warmup_embeddings.main() == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert "public-dense" in output.out
    assert "public-sparse" in output.out
    assert "dense_dimension=384" in output.out
    assert "[0.1, 0.2]" not in output.out
    assert str(Path("private-corpus")) not in output.out


def test_warmup_cli_failure_is_sanitized(monkeypatch, capsys) -> None:
    def fail(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("private-corpus/vector payload /home/secret")

    monkeypatch.setattr(warmup_embeddings, "warmup_fastembed_models", fail)

    assert warmup_embeddings.main() == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert "Embedding model warmup failed" in output.err
    assert "private-corpus" not in output.err
    assert "/home/secret" not in output.err
    assert "vector payload" not in output.err
