from __future__ import annotations

import uuid as uuid_module
from datetime import datetime
from pathlib import Path

from nutrition_agent.infrastructure.snapshot_store import RawPage, SnapshotStore, sha256_hex


def test_snapshot_store_is_content_addressed_and_dedupes(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path)
    raw = RawPage(
        source_url="fixture://x",
        method="GET",
        request_params={},
        body=b"<html>same</html>",
        http_status=200,
        fetched_at=datetime(2026, 8, 21),
    )
    ref1 = store.save(raw, run_id=uuid_module.uuid4())
    ref2 = store.save(raw, run_id=uuid_module.uuid4())

    assert ref1.content_sha256 == ref2.content_sha256 == sha256_hex(raw.body)
    assert ref1.storage_path == ref2.storage_path
    assert ref1.byte_size == len(raw.body)
    assert store.load(ref1) == raw.body

    files = [p for p in tmp_path.rglob("*.html")]
    assert len(files) == 1  # deduplicated on disk
