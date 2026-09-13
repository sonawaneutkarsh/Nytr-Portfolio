"""M15 immutable custom-food schema, repository, and ledger integration."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from nutrition_agent.application.manual_foods import (
    AdjustManualFoodUseCase,
    RecordManualFoodUseCase,
)
from nutrition_agent.db.sql_repos import SqlConsumptionRepository, SqlCustomFoodRepository
from nutrition_agent.domain.nutrition.custom_foods import (
    CustomFoodAuthority,
    CustomFoodProvenance,
    CustomFoodVersion,
    ManualFoodConsumptionEntry,
    ManualMealPeriod,
    ManualNutritionFacts,
)
from nutrition_agent.domain.nutrition.ledger import NutritionAuthority
from tests.migration_helpers import apply_migrations

DATABASE_URL = os.environ.get("STACKS_TEST_DATABASE_URL")
MIGRATION = Path(__file__).resolve().parents[2] / "migrations" / "0013_custom_food.sql"
ADJUSTMENT_MIGRATION = (
    Path(__file__).resolve().parents[2] / "migrations" / "0019_manual_food_adjustments.sql"
)
pytestmark = pytest.mark.skipif(DATABASE_URL is None, reason="scratch PostgreSQL required")


def _repo() -> SqlCustomFoodRepository:
    assert DATABASE_URL is not None
    return SqlCustomFoodRepository(DATABASE_URL)


def _version(user_id, *, calories: str = "400") -> CustomFoodVersion:
    return CustomFoodVersion(
        food_id=uuid4(),
        version_id=uuid4(),
        user_id=user_id,
        name="Oats",
        brand=None,
        serving_description="one bowl",
        serving_amount=Decimal("1"),
        serving_unit="serving",
        nutrition=ManualNutritionFacts(calories_kcal=Decimal(calories), protein_g=None),
        created_at=datetime(2026, 9, 4, 12, tzinfo=UTC),
    )


def test_migration_reapplies_and_privileges_are_append_only() -> None:
    psycopg = pytest.importorskip("psycopg")
    assert DATABASE_URL is not None
    apply_migrations(DATABASE_URL)
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute(MIGRATION.read_text())
        for table in ("custom_food", "custom_food_version", "manual_food_consumption"):
            cur.execute(
                """
                SELECT has_table_privilege('authenticated', %s, 'SELECT'),
                       has_table_privilege('authenticated', %s, 'INSERT'),
                       has_table_privilege('authenticated', %s, 'UPDATE'),
                       has_table_privilege('authenticated', %s, 'DELETE')
                """,
                (table, table, table, table),
            )
            assert cur.fetchone() == (True, True, False, False)
        cur.execute(ADJUSTMENT_MIGRATION.read_text())
        cur.execute(
            """
            SELECT has_table_privilege('authenticated', %s, 'SELECT'),
                   has_table_privilege('authenticated', %s, 'INSERT'),
                   has_table_privilege('authenticated', %s, 'UPDATE'),
                   has_table_privilege('authenticated', %s, 'DELETE')
            """,
            ("manual_food_consumption_adjustment",) * 4,
        )
        assert cur.fetchone() == (True, True, False, False)


def test_sql_correction_replaces_active_totals_then_void_removes_without_deleting() -> None:
    psycopg = pytest.importorskip("psycopg")
    apply_migrations(DATABASE_URL)  # type: ignore[arg-type]
    owner = uuid4()
    repo = _repo()
    version = CustomFoodVersion(
        **{
            **_version(owner).__dict__,
            "nutrition": ManualNutritionFacts(
                calories_kcal=Decimal("400"), protein_g=Decimal("20")
            ),
        }
    )
    repo.save_version(version, create_identity=True)

    class IDs:
        def new_id(self):
            return uuid4()

    class FixedClock:
        def now(self):
            return datetime(2026, 9, 4, 13, tzinfo=UTC)

    original = (
        RecordManualFoodUseCase(repo, FixedClock(), IDs())
        .execute(
            user_id=owner,
            food_id=version.food_id,
            food_version_id=version.version_id,
            consumed_amount=Decimal("1"),
            consumed_unit="serving",
            meal_period=ManualMealPeriod.LUNCH,
            client_event_id=uuid4(),
        )
        .entry
    )
    adjusted = AdjustManualFoodUseCase(repo, FixedClock(), IDs())
    corrected = adjusted.correct(
        user_id=owner,
        entry_id=original.entry_id,
        amount=Decimal("1.5"),
        unit="serving",
        client_event_id=uuid4(),
    )
    assert corrected.replacement is not None
    assert DATABASE_URL is not None
    ledger = SqlConsumptionRepository(DATABASE_URL).list_eaten_evidence(
        owner, datetime(2026, 9, 4, tzinfo=UTC), datetime(2026, 9, 5, tzinfo=UTC)
    )
    assert [item.entry_id for item in ledger] == [corrected.replacement.entry_id]
    assert ledger[0].calories_kcal == Decimal("600.0")
    assert ledger[0].serving_description == "one bowl"
    assert ledger[0].serving_amount == Decimal("1")
    assert ledger[0].serving_unit == "serving"
    assert repo.find_active_consumption(uuid4(), corrected.replacement.entry_id) is None

    adjusted.void(user_id=owner, entry_id=corrected.replacement.entry_id, client_event_id=uuid4())
    assert (
        SqlConsumptionRepository(DATABASE_URL).list_eaten_evidence(
            owner, datetime(2026, 9, 4, tzinfo=UTC), datetime(2026, 9, 5, tzinfo=UTC)
        )
        == ()
    )
    with __import__("psycopg").connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM manual_food_consumption WHERE user_id=%s", (str(owner),))
        assert cur.fetchone()[0] == 2
        cur.execute(
            "SELECT COUNT(*) FROM manual_food_consumption_adjustment WHERE user_id=%s",
            (str(owner),),
        )
        assert cur.fetchone()[0] == 2
    with psycopg.connect(DATABASE_URL) as conn, conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE authenticated")
        cur.execute("SELECT set_config('request.jwt.claim.sub', %s, true)", (str(uuid4()),))
        cur.execute("SELECT COUNT(*) FROM manual_food_consumption_adjustment")
        assert cur.fetchone()[0] == 0


def test_sql_version_snapshot_replay_owner_isolation_and_mixed_ledger() -> None:
    apply_migrations(DATABASE_URL)  # type: ignore[arg-type]
    owner, other = uuid4(), uuid4()
    repo = _repo()
    first = _version(owner)
    repo.save_version(first, create_identity=True)
    second = CustomFoodVersion(
        **{
            **first.__dict__,
            "version_id": uuid4(),
            "nutrition": ManualNutritionFacts(calories_kcal=Decimal("500")),
            "created_at": datetime(2026, 9, 5, 12, tzinfo=UTC),
        }
    )
    repo.save_version(second, create_identity=False)
    assert repo.list_latest(owner) == (second,)
    assert repo.list_latest(other) == ()

    event = ManualFoodConsumptionEntry(
        entry_id=uuid4(),
        user_id=owner,
        food_id=first.food_id,
        food_version_id=first.version_id,
        client_event_id=uuid4(),
        meal_period=ManualMealPeriod.BREAKFAST,
        consumed_amount=Decimal("0.5"),
        consumed_unit="serving",
        portion_factor=Decimal("0.5"),
        food_name=first.name,
        brand=None,
        serving_description=first.serving_description,
        serving_amount=first.serving_amount,
        serving_unit=first.serving_unit,
        nutrition=first.nutrition.scaled(Decimal("0.5")),
        recorded_at=datetime(2026, 9, 4, 13, tzinfo=UTC),
    )
    assert repo.save_consumption(event).created
    replay = ManualFoodConsumptionEntry(
        **{
            **event.__dict__,
            "entry_id": uuid4(),
            "recorded_at": datetime(2026, 9, 4, 14, tzinfo=UTC),
        }
    )
    assert repo.save_consumption(replay).entry == event

    assert DATABASE_URL is not None
    evidence = SqlConsumptionRepository(DATABASE_URL).list_eaten_evidence(
        owner, datetime(2026, 9, 4, tzinfo=UTC), datetime(2026, 9, 5, tzinfo=UTC)
    )
    assert len(evidence) == 1
    assert evidence[0].authority is NutritionAuthority.USER_ENTERED
    assert evidence[0].calories_kcal == Decimal("200.0")
    assert evidence[0].protein_g is None
    assert "protein_g" in evidence[0].unknown_nutrients
    assert evidence[0].plan_item_id is None


def test_barcode_source_identity_provenance_and_ledger_authority_roundtrip() -> None:
    apply_migrations(DATABASE_URL)  # type: ignore[arg-type]
    owner = uuid4()
    digest = "a" * 64
    version = CustomFoodVersion(
        food_id=uuid4(),
        version_id=uuid4(),
        user_id=owner,
        name="Yogurt",
        brand="Dairy",
        serving_description="170 g",
        serving_amount=Decimal("1"),
        serving_unit="serving",
        nutrition=ManualNutritionFacts(calories_kcal=Decimal("100"), protein_g=Decimal("17")),
        created_at=datetime(2026, 9, 9, 12, tzinfo=UTC),
        provenance=CustomFoodProvenance(
            authority=CustomFoodAuthority.OPEN_FOOD_FACTS,
            provider="open_food_facts",
            scanned_barcode="012345678905",
            provider_code="0012345678905",
            product_url="https://world.openfoodfacts.org/product/0012345678905",
            fetched_at=datetime(2026, 9, 9, 12, tzinfo=UTC),
            payload_sha256=digest,
            data_license="ODbL-1.0/DbCL-1.0",
            nutrition_basis="per_serving",
        ),
    )
    repo = _repo()
    repo.save_version(version, create_identity=True)
    assert repo.find_latest_by_source(owner, "open_food_facts", "0012345678905") == version
    event = ManualFoodConsumptionEntry(
        entry_id=uuid4(),
        user_id=owner,
        food_id=version.food_id,
        food_version_id=version.version_id,
        client_event_id=uuid4(),
        meal_period=ManualMealPeriod.BREAKFAST,
        consumed_amount=Decimal("1"),
        consumed_unit="serving",
        portion_factor=Decimal("1"),
        food_name=version.name,
        brand=version.brand,
        serving_description=version.serving_description,
        serving_amount=version.serving_amount,
        serving_unit=version.serving_unit,
        nutrition=version.nutrition,
        recorded_at=datetime(2026, 9, 9, 13, tzinfo=UTC),
        source_system="barcode_open_food_facts",
        nutrition_authority="external_reference",
        nutrition_confidence="community_database",
        provenance_summary=f"Open Food Facts 0012345678905 snapshot {digest}",
    )
    repo.save_consumption(event)
    assert DATABASE_URL is not None
    evidence = SqlConsumptionRepository(DATABASE_URL).list_eaten_evidence(
        owner, datetime(2026, 9, 9, tzinfo=UTC), datetime(2026, 9, 10, tzinfo=UTC)
    )
    assert evidence[0].authority is NutritionAuthority.EXTERNAL_REFERENCE
    assert evidence[0].source_system == "barcode_open_food_facts"
    assert digest in evidence[0].provenance_summary
