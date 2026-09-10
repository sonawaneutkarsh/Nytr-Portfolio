"""Content-addressed snapshot store (ADR-013 pillar 1).

Raw bytes are immutable once written; metadata is returned to callers for
provenance. Deduplication is inherent: identical content maps to one file.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4


@dataclass(frozen=True)
class RawPage:
    source_url: str
    method: str
    request_params: dict[str, str]
    body: bytes
    http_status: int
    fetched_at: datetime
    content_type: str = "text/html"


@dataclass(frozen=True)
class SnapshotRef:
    snapshot_id: UUID
    content_sha256: str
    storage_path: str
    source_url: str
    method: str
    request_params: dict[str, str]
    http_status: int
    fetched_at: datetime
    byte_size: int = 0
    run_id: UUID | None = None


def sha256_hex(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


class SnapshotStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def save(self, raw: RawPage, run_id: UUID) -> SnapshotRef:
        digest = sha256_hex(raw.body)
        relative = Path(digest[:2]) / digest[2:4] / f"{digest}.html"
        absolute = self._root / relative
        if not absolute.exists():
            absolute.parent.mkdir(parents=True, exist_ok=True)
            temp = absolute.with_suffix(".tmp")
            temp.write_bytes(raw.body)
            temp.replace(absolute)
        return SnapshotRef(
            snapshot_id=uuid4(),
            content_sha256=digest,
            storage_path=str(relative),
            source_url=raw.source_url,
            method=raw.method,
            request_params=dict(raw.request_params),
            http_status=raw.http_status,
            fetched_at=raw.fetched_at,
            byte_size=len(raw.body),
            run_id=run_id,
        )

    def load(self, ref: SnapshotRef) -> bytes:
        return (self._root / ref.storage_path).read_bytes()
