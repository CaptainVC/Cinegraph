from types import SimpleNamespace
from typing import Any

import pytest
from qdrant_client.http import models

from cinegraph.adapters.qdrant.qdrant_collection_provisioner import (
    QdrantTranscriptCollectionProvisioner,
)
from cinegraph.common.error_messages import QdrantErrorMessages
from cinegraph.config import DEFAULT_QDRANT_TRANSCRIPT_COLLECTION_SCHEMA
from cinegraph.domain.exceptions.errors import InvalidModelError

SCHEMA = DEFAULT_QDRANT_TRANSCRIPT_COLLECTION_SCHEMA


class FakeQdrantCollectionClient:
    def __init__(
        self,
        *,
        exists: bool = False,
        dense_size: int | None = None,
        include_sparse: bool = True,
        payload_schema: dict[str, models.PayloadIndexInfo] | None = None,
        status: models.CollectionStatus = models.CollectionStatus.GREEN,
    ) -> None:
        self.exists = exists
        self.dense_size = dense_size or SCHEMA.dense_vector_size
        self.include_sparse = include_sparse
        self.payload_schema = payload_schema or {}
        self.status = status
        self.create_calls: list[dict[str, Any]] = []
        self.index_calls: list[dict[str, Any]] = []
        self.get_calls: list[str] = []

    def collection_exists(self, collection_name: str) -> bool:
        return self.exists

    def create_collection(self, collection_name: str, **kwargs: Any) -> bool:
        self.create_calls.append({"collection_name": collection_name, **kwargs})
        self.exists = True
        vectors = kwargs["vectors_config"]
        self.dense_size = vectors[SCHEMA.dense_vector_name].size
        self.include_sparse = SCHEMA.sparse_vector_name in kwargs["sparse_vectors_config"]
        return True

    def create_payload_index(
        self,
        collection_name: str,
        field_name: str,
        **kwargs: Any,
    ) -> object:
        self.index_calls.append(
            {
                "collection_name": collection_name,
                "field_name": field_name,
                **kwargs,
            }
        )
        self.payload_schema[field_name] = models.PayloadIndexInfo(
            data_type=kwargs["field_schema"],
            points=0,
        )
        return object()

    def get_collection(self, collection_name: str) -> Any:
        self.get_calls.append(collection_name)
        dense = models.VectorParams(
            size=self.dense_size,
            distance=SCHEMA.distance,
        )
        sparse = (
            {
                SCHEMA.sparse_vector_name: models.SparseVectorParams(
                    index=models.SparseIndexParams(on_disk=True)
                )
            }
            if self.include_sparse
            else {}
        )
        return SimpleNamespace(
            status=self.status,
            config=SimpleNamespace(
                params=SimpleNamespace(
                    vectors={SCHEMA.dense_vector_name: dense},
                    sparse_vectors=sparse,
                )
            ),
            payload_schema=self.payload_schema,
        )


def complete_payload_schema() -> dict[str, models.PayloadIndexInfo]:
    return {
        definition.field_name: models.PayloadIndexInfo(
            data_type=definition.field_schema,
            points=0,
        )
        for definition in SCHEMA.payload_indexes
    }


def test_missing_collection_and_indexes_are_created_then_verified() -> None:
    client = FakeQdrantCollectionClient()

    result = QdrantTranscriptCollectionProvisioner(client).provision()

    assert result.collection_name == SCHEMA.collection_name
    assert result.collection_created is True
    assert result.payload_indexes_created == tuple(
        definition.field_name for definition in SCHEMA.payload_indexes
    )
    assert len(client.create_calls) == 1
    create = client.create_calls[0]
    dense = create["vectors_config"][SCHEMA.dense_vector_name]
    sparse = create["sparse_vectors_config"][SCHEMA.sparse_vector_name]
    assert dense.size == 384
    assert dense.distance is models.Distance.COSINE
    assert sparse.index.on_disk is True
    assert create["on_disk_payload"] is True
    assert [call["field_name"] for call in client.index_calls] == list(
        result.payload_indexes_created
    )
    assert all(call["wait"] is True for call in client.index_calls)
    assert client.get_calls == [SCHEMA.collection_name, SCHEMA.collection_name]


def test_existing_ready_collection_is_idempotent() -> None:
    client = FakeQdrantCollectionClient(
        exists=True,
        payload_schema=complete_payload_schema(),
    )
    provisioner = QdrantTranscriptCollectionProvisioner(client)

    first = provisioner.provision()
    second = provisioner.provision()

    assert first.collection_created is False
    assert first.payload_indexes_created == ()
    assert second == first
    assert client.create_calls == []
    assert client.index_calls == []


@pytest.mark.parametrize(
    ("client", "message"),
    [
        (
            FakeQdrantCollectionClient(exists=True, dense_size=999),
            QdrantErrorMessages.DENSE_VECTOR_CONFIGURATION_MUST_MATCH,
        ),
        (
            FakeQdrantCollectionClient(exists=True, include_sparse=False),
            QdrantErrorMessages.SPARSE_VECTOR_CONFIGURATION_MUST_MATCH,
        ),
    ],
)
def test_incompatible_vector_schema_fails_without_mutation(
    client: FakeQdrantCollectionClient,
    message: str,
) -> None:
    with pytest.raises(InvalidModelError, match=message):
        QdrantTranscriptCollectionProvisioner(client).provision()

    assert client.create_calls == []
    assert client.index_calls == []


def test_incompatible_existing_payload_index_fails_without_replacing_it() -> None:
    first = SCHEMA.payload_indexes[0]
    client = FakeQdrantCollectionClient(
        exists=True,
        payload_schema={
            first.field_name: models.PayloadIndexInfo(
                data_type=models.PayloadSchemaType.INTEGER,
                points=0,
            )
        },
    )

    with pytest.raises(
        InvalidModelError,
        match=QdrantErrorMessages.PAYLOAD_INDEX_CONFIGURATION_MUST_MATCH,
    ):
        QdrantTranscriptCollectionProvisioner(client).provision()

    assert client.index_calls == []


def test_non_green_collection_fails_readiness_after_schema_verification() -> None:
    client = FakeQdrantCollectionClient(
        exists=True,
        payload_schema=complete_payload_schema(),
        status=models.CollectionStatus.RED,
    )

    with pytest.raises(
        InvalidModelError,
        match=QdrantErrorMessages.COLLECTION_STATUS_MUST_BE_GREEN,
    ):
        QdrantTranscriptCollectionProvisioner(client).provision()
