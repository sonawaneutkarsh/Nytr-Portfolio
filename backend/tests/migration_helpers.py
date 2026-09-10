"""Shared scratch-PostgreSQL migration application for integration tests."""

from __future__ import annotations

from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[2] / "backend" / "migrations"


def apply_migrations(database_url: str) -> None:
    """Apply a fresh chain, or reapply only the superseding latest migration.

    Migration 0007 intentionally makes ``menu_page_offering.profile_id``
    nullable. Re-running superseded 0005 after accepted non-profile rows exist
    would transiently restore its obsolete NOT NULL invariant before 0007 can
    repair it. Production migration runners apply versions once; repeat tests
    therefore reapply 0007 plus every later additive migration after an
    upgraded schema is detected.
    """
    import psycopg

    with psycopg.connect(database_url) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema='public'
                  AND table_name='menu_page_offering'
                  AND column_name='nutrition_source_state'
            )
            """
        )
        upgraded = bool(cur.fetchone()[0])
        sql_files = (
            [
                path
                for path in sorted(MIGRATIONS.glob("*.sql"))
                if path.name >= "0007_menu_offering_nutrition_state.sql"
            ]
            if upgraded
            else sorted(MIGRATIONS.glob("*.sql"))
        )
        for sql_file in sql_files:
            cur.execute(sql_file.read_text(encoding="utf-8"))


__all__ = ["MIGRATIONS", "apply_migrations"]
