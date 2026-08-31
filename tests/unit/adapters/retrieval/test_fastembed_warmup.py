from dataclasses import dataclass
from pathlib import Path

import pytest

from cinegraph.adapters.retrieval.fastembed_warmup import (
    prepare_model_cache_directories,
    warmup_fastembed_models,
)
from cinegraph.config import EmbeddingConfiguration


@dataclass
class FakeSparseResult:
    indices: tuple[object, ...] = (1, 4)
    values: tuple[object, ...] = (0.5, 0.25)


class FakeDenseModel:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs

    def embed(self, documents: tuple[str, ...]):
        return iter(((0.0, 1.0, 2.0),))


class FakeSparseModel:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs

    def embed(self, documents: tuple[str, ...]):
        return iter((FakeSparseResult(),))


def configuration() -> EmbeddingConfiguration:
    return EmbeddingConfiguration("public-dense", "public-sparse", 2_147_483_647, 1e-12, 3)


def test_warmup_materializes_both_models_and_returns_safe_aggregates(tmp_path: Path) -> None:
    result = warmup_fastembed_models(
        configuration(),
        tmp_path / "fastembed",
        dense_factory=FakeDenseModel,
        sparse_factory=FakeSparseModel,
    )

    assert result.dense_model == "public-dense"
    assert result.sparse_model == "public-sparse"
    assert result.dense_dimension == 3
    assert result.sparse_nonzero_count == 2
    assert (tmp_path / "fastembed").is_dir()


def test_warmup_passes_one_shared_cache_to_both_model_factories(tmp_path: Path) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def dense_factory(**kwargs: object) -> FakeDenseModel:
        calls.append(("dense", kwargs))
        return FakeDenseModel(**kwargs)

    def sparse_factory(**kwargs: object) -> FakeSparseModel:
        calls.append(("sparse", kwargs))
        return FakeSparseModel(**kwargs)

    cache_dir = tmp_path / "cache" / "fastembed"
    warmup_fastembed_models(
        configuration(),
        cache_dir,
        dense_factory=dense_factory,
        sparse_factory=sparse_factory,
    )

    assert calls == [
        ("dense", {"model_name": "public-dense", "cache_dir": str(cache_dir)}),
        ("sparse", {"model_name": "public-sparse", "cache_dir": str(cache_dir)}),
    ]
    assert (cache_dir.parent / "model-download-work").is_dir()


def test_prepare_model_cache_directories_creates_persistent_temp_dir(tmp_path: Path) -> None:
    cache_dir = tmp_path / "cache" / "fastembed"
    temporary_dir = tmp_path / "cache" / "tmp"

    prepare_model_cache_directories(cache_dir, temporary_dir)

    assert temporary_dir.is_dir()
    assert cache_dir.is_dir()
    if temporary_dir.stat().st_mode & 0o777 != 0o700:
        pytest.skip("POSIX directory mode bits are unavailable")


@pytest.mark.parametrize("model_kind", ["dense", "sparse"])
def test_warmup_rejects_wrong_result_cardinality(tmp_path: Path, model_kind: str) -> None:
    class WrongCardinalityDense(FakeDenseModel):
        def embed(self, documents: tuple[str, ...]):
            return iter(((0.0, 1.0, 2.0), (0.0, 1.0, 2.0)))

    class WrongCardinalitySparse(FakeSparseModel):
        def embed(self, documents: tuple[str, ...]):
            return iter((FakeSparseResult(), FakeSparseResult()))

    dense_factory = WrongCardinalityDense if model_kind == "dense" else FakeDenseModel
    sparse_factory = WrongCardinalitySparse if model_kind == "sparse" else FakeSparseModel
    with pytest.raises(ValueError, match="cardinality"):
        warmup_fastembed_models(
            configuration(),
            tmp_path,
            dense_factory=dense_factory,
            sparse_factory=sparse_factory,
        )


def test_warmup_rejects_dense_dimension_mismatch(tmp_path: Path) -> None:
    class WrongDimensionDense(FakeDenseModel):
        def embed(self, documents: tuple[str, ...]):
            return iter(((0.0, 1.0),))

    with pytest.raises(ValueError, match="dimension"):
        warmup_fastembed_models(
            configuration(),
            tmp_path,
            dense_factory=WrongDimensionDense,
            sparse_factory=FakeSparseModel,
        )


def test_warmup_rejects_sparse_index_value_mismatch(tmp_path: Path) -> None:
    class WrongSparse(FakeSparseModel):
        def embed(self, documents: tuple[str, ...]):
            return iter((FakeSparseResult(indices=(1,), values=(0.5, 0.25)),))

    with pytest.raises(ValueError, match="cardinality"):
        warmup_fastembed_models(
            configuration(),
            tmp_path,
            dense_factory=FakeDenseModel,
            sparse_factory=WrongSparse,
        )


@pytest.mark.parametrize("bad_dense", [(float("nan"),) * 3, (float("inf"),) * 3, (True,) * 3])
def test_warmup_rejects_nonfinite_or_boolean_dense_values(
    tmp_path: Path, bad_dense: tuple[object, ...]
) -> None:
    class BadDense(FakeDenseModel):
        def embed(self, documents: tuple[str, ...]):
            return iter((bad_dense,))

    with pytest.raises(ValueError, match="dense value"):
        warmup_fastembed_models(
            configuration(),
            tmp_path,
            dense_factory=BadDense,
            sparse_factory=FakeSparseModel,
        )


def test_warmup_rejects_empty_sparse_output(tmp_path: Path) -> None:
    class EmptySparse(FakeSparseModel):
        def embed(self, documents: tuple[str, ...]):
            return iter((FakeSparseResult(indices=(), values=()),))

    with pytest.raises(ValueError, match="cardinality"):
        warmup_fastembed_models(
            configuration(),
            tmp_path,
            dense_factory=FakeDenseModel,
            sparse_factory=EmptySparse,
        )


def test_warmup_rejects_nonfinite_sparse_values(tmp_path: Path) -> None:
    class BadSparse(FakeSparseModel):
        def embed(self, documents: tuple[str, ...]):
            return iter((FakeSparseResult(values=(float("inf"), 0.25)),))

    with pytest.raises(ValueError, match="sparse value"):
        warmup_fastembed_models(
            configuration(),
            tmp_path,
            dense_factory=FakeDenseModel,
            sparse_factory=BadSparse,
        )


@pytest.mark.parametrize(
    ("indices", "values"),
    [((1, 1), (0.5, 0.25)), ((1, 4), (0.0, 0.25)), ((1, 4), (True, 0.25))],
)
def test_warmup_rejects_duplicate_indices_or_invalid_sparse_values(
    tmp_path: Path,
    indices: tuple[object, ...],
    values: tuple[object, ...],
) -> None:
    class BadSparse(FakeSparseModel):
        def embed(self, documents: tuple[str, ...]):
            return iter((FakeSparseResult(indices=indices, values=values),))

    with pytest.raises(ValueError, match="sparse (index|value)"):
        warmup_fastembed_models(
            configuration(),
            tmp_path,
            dense_factory=FakeDenseModel,
            sparse_factory=BadSparse,
        )
