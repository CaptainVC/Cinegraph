"""Provider-free FastEmbed model materialization and local sanity checks."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from math import isfinite
from os import PathLike
from pathlib import Path
from stat import S_IMODE
from typing import Any, Protocol, cast

from cinegraph.config import (
    APP_CACHE_ROOT,
    DEFAULT_EMBEDDING_CONFIGURATION,
    FASTEMBED_CACHE_DIR,
    HUGGINGFACE_HOME_DIR,
    HUGGINGFACE_HUB_CACHE_DIR,
    HUGGINGFACE_XET_CACHE_DIR,
    MODEL_DOWNLOAD_TMPDIR,
    EmbeddingConfiguration,
)


class DenseEmbeddingModel(Protocol):
    def embed(self, documents: Iterable[str]) -> Iterable[Iterable[object]]: ...


class SparseEmbeddingResult(Protocol):
    indices: Iterable[object]
    values: Iterable[object]


class SparseEmbeddingModel(Protocol):
    def embed(self, documents: Iterable[str]) -> Iterable[SparseEmbeddingResult]: ...


ModelFactory = Callable[..., object]


def _ensure_cache_directory(path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_dir()):
        raise ValueError("embedding cache path is not a directory")
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)
    if os.name != "nt" and S_IMODE(path.stat().st_mode) != 0o700:
        raise ValueError("embedding cache directory permissions are invalid")


def prepare_model_cache_directories(
    cache_dir: str | PathLike[str] = FASTEMBED_CACHE_DIR,
    temporary_dir: str | PathLike[str] = MODEL_DOWNLOAD_TMPDIR,
) -> None:
    """Create persistent model/cache temp directories before HF imports/downloads."""

    cache_path = Path(cache_dir)
    temporary_path = Path(temporary_dir)
    cache_root = (
        Path(APP_CACHE_ROOT) if cache_path == Path(FASTEMBED_CACHE_DIR) else cache_path.parent
    )
    huggingface_home = (
        Path(HUGGINGFACE_HOME_DIR)
        if cache_root == Path(APP_CACHE_ROOT)
        else cache_root / "huggingface"
    )
    for path in (
        cache_path,
        huggingface_home,
        Path(HUGGINGFACE_HUB_CACHE_DIR)
        if cache_root == Path(APP_CACHE_ROOT)
        else huggingface_home / "hub",
        Path(HUGGINGFACE_XET_CACHE_DIR)
        if cache_root == Path(APP_CACHE_ROOT)
        else huggingface_home / "xet",
        temporary_path,
    ):
        _ensure_cache_directory(path)


@dataclass(frozen=True, slots=True)
class EmbeddingWarmupResult:
    """Safe aggregate facts from a completed warmup; no vectors are retained."""

    dense_model: str
    sparse_model: str
    dense_dimension: int
    sparse_nonzero_count: int


def _default_model_factories() -> tuple[ModelFactory, ModelFactory]:
    from fastembed import SparseTextEmbedding, TextEmbedding

    return TextEmbedding, SparseTextEmbedding


def _one_result(results: Iterable[object]) -> object:
    iterator = iter(results)
    first = next(iterator, None)
    if first is None or next(iterator, None) is not None:
        raise ValueError("embedding warmup result cardinality is invalid")
    return first


def warmup_fastembed_models(
    configuration: EmbeddingConfiguration = DEFAULT_EMBEDDING_CONFIGURATION,
    cache_dir: str | PathLike[str] = FASTEMBED_CACHE_DIR,
    *,
    dense_factory: ModelFactory | None = None,
    sparse_factory: ModelFactory | None = None,
) -> EmbeddingWarmupResult:
    """Download both local models into ``cache_dir`` and validate one local encode.

    Factories are injectable so tests exercise cardinality and dimensions without
    downloading a model.  The probe text is fixed and contains no corpus content;
    only aggregate dimensions are returned to callers.
    """

    cache_path = Path(cache_dir)
    temporary_path = (
        Path(MODEL_DOWNLOAD_TMPDIR)
        if cache_path == Path(FASTEMBED_CACHE_DIR)
        else cache_path.parent / "model-download-work"
    )
    prepare_model_cache_directories(cache_path, temporary_path)
    if dense_factory is None or sparse_factory is None:
        default_dense_factory, default_sparse_factory = _default_model_factories()
        dense_factory = dense_factory or default_dense_factory
        sparse_factory = sparse_factory or default_sparse_factory

    dense_model = cast(
        DenseEmbeddingModel,
        dense_factory(model_name=configuration.dense_model, cache_dir=str(cache_path)),
    )
    sparse_model = cast(
        SparseEmbeddingModel,
        sparse_factory(model_name=configuration.sparse_model, cache_dir=str(cache_path)),
    )

    probe_documents = ("cinegraph local embedding warmup",)
    dense_result = _one_result(dense_model.embed(probe_documents))
    dense_values = tuple(cast(Iterable[object], dense_result))
    for value in dense_values:
        if isinstance(value, bool):
            raise ValueError("embedding warmup dense value is invalid")
        try:
            if not isfinite(float(cast(Any, value))):
                raise ValueError("embedding warmup dense value is invalid")
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("embedding warmup dense value is invalid") from error
    dense_dimension = len(dense_values)
    if dense_dimension != configuration.dense_vector_size:
        raise ValueError("embedding warmup dense dimension is invalid")

    sparse_result = cast(SparseEmbeddingResult, _one_result(sparse_model.embed(probe_documents)))
    sparse_indices = tuple(sparse_result.indices)
    sparse_values = tuple(sparse_result.values)
    if not sparse_indices or len(sparse_indices) != len(sparse_values):
        raise ValueError("embedding warmup sparse cardinality is invalid")
    normalized_indices: set[int] = set()
    for index in sparse_indices:
        if isinstance(index, bool) or not isinstance(index, (int, float)) or int(index) != index:
            raise ValueError("embedding warmup sparse index is invalid")
        normalized_index = int(index)
        if normalized_index < 0 or normalized_index in normalized_indices:
            raise ValueError("embedding warmup sparse index is invalid")
        normalized_indices.add(normalized_index)
    for value in sparse_values:
        if isinstance(value, bool):
            raise ValueError("embedding warmup sparse value is invalid")
        try:
            normalized_value = float(cast(Any, value))
            if not isfinite(normalized_value) or normalized_value == 0:
                raise ValueError("embedding warmup sparse value is invalid")
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("embedding warmup sparse value is invalid") from error

    return EmbeddingWarmupResult(
        dense_model=configuration.dense_model,
        sparse_model=configuration.sparse_model,
        dense_dimension=dense_dimension,
        sparse_nonzero_count=len(sparse_indices),
    )
