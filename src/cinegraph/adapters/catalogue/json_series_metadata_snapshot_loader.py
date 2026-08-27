"""Compatibility-named entry point for the JSON series metadata loader."""

from cinegraph.adapters.catalogue.series_metadata_snapshot_loader import (
    JsonSeriesMetadataSnapshotLoader,
    parse_series_metadata_snapshot,
)

__all__ = ["JsonSeriesMetadataSnapshotLoader", "parse_series_metadata_snapshot"]
