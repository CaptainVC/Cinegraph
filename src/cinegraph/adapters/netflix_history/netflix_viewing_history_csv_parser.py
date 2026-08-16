import csv
import hashlib
import io
from datetime import datetime
from pathlib import PurePath

from cinegraph.application.models.netflix_history import (
    NetflixHistoryUpload,
    NetflixViewingHistoryRow,
    ParsedNetflixViewingHistory,
)
from cinegraph.common.error_messages import NetflixHistoryErrorMessages
from cinegraph.config import (
    DEFAULT_NETFLIX_HISTORY_IMPORT_CONFIGURATION,
    NetflixHistoryImportConfiguration,
)
from cinegraph.config.netflix_history import (
    NETFLIX_HISTORY_DATE_COLUMN,
    NETFLIX_HISTORY_DATE_FORMATS,
    NETFLIX_HISTORY_FORMULA_PREFIXES,
    NETFLIX_HISTORY_REQUIRED_COLUMNS,
    NETFLIX_HISTORY_TITLE_COLUMN,
)


class NetflixViewingHistoryCsvParser:
    def __init__(
        self,
        configuration: NetflixHistoryImportConfiguration = (
            DEFAULT_NETFLIX_HISTORY_IMPORT_CONFIGURATION
        ),
    ) -> None:
        self._configuration = configuration

    def parse(self, upload: NetflixHistoryUpload) -> ParsedNetflixViewingHistory:
        self._validate_upload(upload)
        try:
            text = upload.content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValueError(NetflixHistoryErrorMessages.ENCODING_INVALID) from error
        if "\x00" in text:
            raise ValueError(NetflixHistoryErrorMessages.ENCODING_INVALID)
        content_sha256 = hashlib.sha256(upload.content).hexdigest()
        try:
            reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
            if tuple(reader.fieldnames or ()) != NETFLIX_HISTORY_REQUIRED_COLUMNS:
                raise ValueError(NetflixHistoryErrorMessages.CSV_SCHEMA_INVALID)
            parsed_rows = []
            for row_number, row in enumerate(reader, start=2):
                if len(parsed_rows) >= self._configuration.maximum_rows:
                    raise ValueError(
                        NetflixHistoryErrorMessages.CSV_ROW_LIMIT_EXCEEDED
                    )
                parsed_rows.append(
                    self._parse_row(row, row_number, content_sha256)
                )
            rows = tuple(parsed_rows)
        except csv.Error as error:
            raise ValueError(NetflixHistoryErrorMessages.CSV_ROW_INVALID) from error
        if not rows:
            raise ValueError(NetflixHistoryErrorMessages.CSV_ROW_INVALID)
        return ParsedNetflixViewingHistory(content_sha256, rows)

    def _validate_upload(self, upload: NetflixHistoryUpload) -> None:
        content_type = upload.content_type.split(";", 1)[0].strip().casefold()
        if (
            not upload.filename
            or PurePath(upload.filename).name != upload.filename
            or not upload.filename.casefold().endswith(".csv")
        ):
            raise ValueError(NetflixHistoryErrorMessages.FILENAME_INVALID)
        if content_type not in self._configuration.accepted_content_types:
            raise ValueError(NetflixHistoryErrorMessages.CONTENT_TYPE_INVALID)
        if not 0 < len(upload.content) <= self._configuration.maximum_upload_bytes:
            raise ValueError(NetflixHistoryErrorMessages.FILE_SIZE_INVALID)

    def _parse_row(
        self,
        row: dict[str, str | None],
        row_number: int,
        content_sha256: str,
    ) -> NetflixViewingHistoryRow:
        if set(row) != set(NETFLIX_HISTORY_REQUIRED_COLUMNS):
            raise ValueError(NetflixHistoryErrorMessages.CSV_ROW_INVALID)
        title = row.get(NETFLIX_HISTORY_TITLE_COLUMN)
        viewed_text = row.get(NETFLIX_HISTORY_DATE_COLUMN)
        if (
            not isinstance(title, str)
            or not title
            or title.strip() != title
            or len(title) > self._configuration.maximum_title_characters
            or not isinstance(viewed_text, str)
            or not viewed_text
            or viewed_text.strip() != viewed_text
            or any(ord(character) < 32 for character in title)
            or any(ord(character) < 32 for character in viewed_text)
        ):
            raise ValueError(NetflixHistoryErrorMessages.CSV_ROW_INVALID)
        if any(
            value.lstrip().startswith(NETFLIX_HISTORY_FORMULA_PREFIXES)
            for value in (title, viewed_text)
        ):
            raise ValueError(
                NetflixHistoryErrorMessages.FORMULA_INJECTION_DETECTED
            )
        viewed_on = self._parse_date(viewed_text)
        row_id = hashlib.sha256(
            f"{content_sha256}:{row_number}:{title}:{viewed_on.isoformat()}".encode(
                "utf-8"
            )
        ).hexdigest()
        return NetflixViewingHistoryRow(row_id, row_number, title, viewed_on)

    @staticmethod
    def _parse_date(value: str):
        for date_format in NETFLIX_HISTORY_DATE_FORMATS:
            try:
                return datetime.strptime(value, date_format).date()
            except ValueError:
                continue
        raise ValueError(NetflixHistoryErrorMessages.CSV_ROW_INVALID)
