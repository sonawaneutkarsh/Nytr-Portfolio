"""M6 target-policy approval use case: validation, immutability, audit link."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from nutrition_agent.application.target_policy import (
    ApproveTargetPolicyUseCase,
    DuplicateTargetPolicyVersion,
    InvalidTargetPolicy,
    build_domain_target_set,
)
from nutrition_agent.db.in_memory_repos import InMemoryTargetPolicyRepository

USER_A = UUID("00000000-0000-0000-0000-0000000000a1")
NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)

GOALS = [
    {"nutrient": "calories_kcal", "kind": "target", "value": "900", "weight": "1"},
    {"nutrient": "protein_g", "kind": "target", "value": "50", "weight": "1"},
]


class _Clock:
    def now(self) -> datetime:
        return NOW


class _Ids:
    _n = 10

    def new_id(self) -> UUID:
        _Ids._n += 1
        return UUID(int=_Ids._n)


def _use_case() -> tuple[ApproveTargetPolicyUseCase, InMemoryTargetPolicyRepository]:
    repo = InMemoryTargetPolicyRepository()
    return ApproveTargetPolicyUseCase(repository=repo, clock=_Clock(), ids=_Ids()), repo


def test_approval_persists_immutable_version_and_decision_link() -> None:
    use_case, repo = _use_case()
    outcome = use_case.execute(USER_A, "leanbulk-v1", GOALS, "trainer guidance start")
    assert outcome.policy.user_id == USER_A
    assert outcome.policy.policy_version == "leanbulk-v1"
    assert len(outcome.policy.payload_sha256) == 64
    assert repo.rationale_for(outcome.policy.version_id) == "trainer guidance start"


def test_goal_order_does_not_change_payload_hash() -> None:
    # Separate stores: identical payloads are deduped per user, so order
    # equivalence must be observed across independent approvals.
    forward_use_case, _ = _use_case()
    backward_use_case, _ = _use_case()
    forward = forward_use_case.execute(USER_A, "v-a", GOALS, "r")
    backward = backward_use_case.execute(USER_A, "v-b", list(reversed(GOALS)), "r")
    assert forward.policy.payload_sha256 == backward.policy.payload_sha256

    # Within ONE user's history, an identical payload under a new label is a
    # semantic duplicate, rejected rather than silently versioned.
    duplicate_use_case, _ = _use_case()
    duplicate_use_case.execute(USER_A, "v-a", GOALS, "r")
    with pytest.raises(DuplicateTargetPolicyVersion):
        duplicate_use_case.execute(USER_A, "v-b", list(reversed(GOALS)), "again")


def test_duplicate_version_label_maps_to_dedicated_error() -> None:
    use_case, _ = _use_case()
    use_case.execute(USER_A, "v1", GOALS, "first")
    with pytest.raises(DuplicateTargetPolicyVersion):
        use_case.execute(USER_A, "v1", GOALS, "second")


@pytest.mark.parametrize(
    ("bad_goals",),
    [
        (None,),
        ([],),
        ([{"kind": "target", "value": "900", "weight": "1"}],),  # missing nutrient
        ([{"nutrient": "calories_kcal", "kind": "saturate", "value": "900", "weight": "1"}],),
        ([{"nutrient": "not_a_nutrient", "kind": "target", "value": "1", "weight": "1"}],),
        ([{"nutrient": "calories_kcal", "kind": "target", "value": "abc", "weight": "1"}],),
        ([{"nutrient": "calories_kcal", "kind": "target", "value": "0", "weight": "1"}],),
        ([{"nutrient": "calories_kcal", "kind": "target", "value": "-5", "weight": "1"}],),
        ([{"nutrient": "calories_kcal", "kind": "target", "value": "900", "weight": "-1"}],),
        ([GOALS[0], GOALS[0]],),
    ],
)
def test_invalid_payloads_fail_closed_without_persistence(bad_goals) -> None:
    use_case, repo = _use_case()
    with pytest.raises(InvalidTargetPolicy):
        use_case.execute(USER_A, "v-bad", bad_goals, "rationale")
    assert repo.latest_approved(USER_A) is None


def test_rationale_required_and_bounded() -> None:
    use_case, _ = _use_case()
    with pytest.raises(InvalidTargetPolicy):
        use_case.execute(USER_A, "v1", GOALS, "")
    with pytest.raises(InvalidTargetPolicy):
        use_case.execute(USER_A, "v1", GOALS, "   ")
    with pytest.raises(InvalidTargetPolicy):
        use_case.execute(USER_A, "v1", GOALS, "x" * 2001)


def test_stored_goals_reconstruct_to_equivalent_domain_target_set() -> None:
    use_case, _ = _use_case()
    outcome = use_case.execute(USER_A, "v1", GOALS, "r")
    stored = build_domain_target_set(outcome.policy.goals_jsonb)
    assert stored.goals["calories_kcal"].value == Decimal("900")
    assert stored.goals["protein_g"].kind.value == "target"


def test_exponent_notation_is_exact_decimal_not_float() -> None:
    """'1.5e3' parses to Decimal('1500') exactly — accepted, never via float."""
    from decimal import Decimal

    use_case, repo = _use_case()
    outcome = use_case.execute(
        USER_A,
        "v-exp",
        [{**GOALS[0], "value": "1.5e3"}],
        "exact-decimal notation",
    )
    stored = build_domain_target_set(outcome.policy.goals_jsonb)
    assert stored.goals["calories_kcal"].value == Decimal("1500") == Decimal("1.5e3")
    assert repo.latest_approved(USER_A) is not None
