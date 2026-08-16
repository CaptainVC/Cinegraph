from dataclasses import dataclass
from typing import Protocol

from qdrant_client.http import models

from cinegraph.common.error_messages import QdrantErrorMessages
from cinegraph.config import (
    DEFAULT_QDRANT_TRANSCRIPT_COLLECTION_SCHEMA,
    QdrantTranscriptCollectionSchema,
)
from cinegraph.domain.exceptions.errors import InvalidModelError


class QdrantCollectionClient(Protocol):
    def collection_exists(self, collection_name: str) -> bool: ...

    def create_collection(
        self,
        collection_name: str,
        *,
        vectors_config: dict[str, models.VectorParams],
        sparse_vectors_config: dict[str, models.SparseVectorParams],
        on_disk_payload: bool,
    ) -> bool: ...

    def create_payload_index(
        self,
        collection_name: str,
        field_name: str,
        *,
        field_schema: models.PayloadSchemaType,
        wait: bool,
    ) -> object: ...

    def get_collection(self, collection_name: str) -> models.CollectionInfo: ...


@dataclass(frozen=True, slots=True)
class QdrantCollectionProvisioningResult:
    collection_name: str
    collection_created: bool
    payload_indexes_created: tuple[str, ...]


class QdrantTranscriptCollectionProvisioner:
    # Store an immutable expected schema used for both provisioning and readiness.
    def __init__(
        self,
        client: QdrantCollectionClient,
        schema: QdrantTranscriptCollectionSchema = (
            DEFAULT_QDRANT_TRANSCRIPT_COLLECTION_SCHEMA
        ),
    ) -> None:
        self._client = client
        self._schema = schema

    # Create missing structures, then fail closed unless the resulting schema is ready.
    def provision(self) -> QdrantCollectionProvisioningResult:
        collection_created = not self._client.collection_exists(
            self._schema.collection_name
        )
        if collection_created:
            self._client.create_collection(
                self._schema.collection_name,
                vectors_config={
                    self._schema.dense_vector_name: models.VectorParams(
                        size=self._schema.dense_vector_size,
                        distance=self._schema.distance,
                    )
                },
                sparse_vectors_config={
                    self._schema.sparse_vector_name: models.SparseVectorParams(
                        index=models.SparseIndexParams(on_disk=True)
                    )
                },
                on_disk_payload=self._schema.on_disk_payload,
            )

        collection = self._client.get_collection(self._schema.collection_name)
        self._validate_vectors(collection)
        created_indexes = self._create_missing_payload_indexes(collection)
        ready_collection = self._client.get_collection(self._schema.collection_name)
        self._validate_ready(ready_collection)
        return QdrantCollectionProvisioningResult(
            collection_name=self._schema.collection_name,
            collection_created=collection_created,
            payload_indexes_created=created_indexes,
        )

    def _create_missing_payload_indexes(
        self,
        collection: models.CollectionInfo,
    ) -> tuple[str, ...]:
        created = []
        for definition in self._schema.payload_indexes:
            existing = collection.payload_schema.get(definition.field_name)
            if existing is not None:
                if existing.data_type is not definition.field_schema:
                    raise InvalidModelError(
                        QdrantErrorMessages.PAYLOAD_INDEX_CONFIGURATION_MUST_MATCH
                    )
                continue
            self._client.create_payload_index(
                self._schema.collection_name,
                definition.field_name,
                field_schema=definition.field_schema,
                wait=True,
            )
            created.append(definition.field_name)
        return tuple(created)

    def _validate_vectors(self, collection: models.CollectionInfo) -> None:
        vectors = collection.config.params.vectors
        dense = vectors.get(self._schema.dense_vector_name) if isinstance(vectors, dict) else None
        if (
            dense is None
            or dense.size != self._schema.dense_vector_size
            or dense.distance is not self._schema.distance
        ):
            raise InvalidModelError(
                QdrantErrorMessages.DENSE_VECTOR_CONFIGURATION_MUST_MATCH
            )
        sparse_vectors = collection.config.params.sparse_vectors or {}
        if self._schema.sparse_vector_name not in sparse_vectors:
            raise InvalidModelError(
                QdrantErrorMessages.SPARSE_VECTOR_CONFIGURATION_MUST_MATCH
            )

    def _validate_ready(self, collection: models.CollectionInfo) -> None:
        self._validate_vectors(collection)
        if collection.status is not models.CollectionStatus.GREEN:
            raise InvalidModelError(
                QdrantErrorMessages.COLLECTION_STATUS_MUST_BE_GREEN
            )
        for definition in self._schema.payload_indexes:
            existing = collection.payload_schema.get(definition.field_name)
            if existing is None or existing.data_type is not definition.field_schema:
                raise InvalidModelError(
                    QdrantErrorMessages.PAYLOAD_INDEX_CONFIGURATION_MUST_MATCH
                )
