# Nutrition Engine Specification

## Responsibilities

The Python domain engine is authoritative for:
- nutrition arithmetic
- meal composition
- daily totals
- remaining targets
- target calculation
- trend calculation
- candidate eligibility
- deterministic scoring

## Nutrition fields

At minimum, support where source data exists:
- calories_kcal
- protein_g
- carbohydrate_g
- total_fat_g
- saturated_fat_g
- trans_fat_g
- fiber_g
- sugars_g
- added_sugars_g
- sodium_mg
- cholesterol_mg
- vitamin_d_mcg
- calcium_mg
- iron_mg
- potassium_mg

Use canonical units and Decimal/integer-safe arithmetic where appropriate.

## Provenance/confidence

Each nutrition profile must point to:
- source snapshot/document
- parser/version
- serving basis
- confidence level

Confidence:
- `official_published`
- `official_component_sum`
- `verified_internal_recipe`
- `partial`
- `estimated`

## Meal composition

A concrete meal is a collection of component lines:

- food/component reference
- exact quantity
- exact unit
- exact nutrition profile version

Do not resolve a component to its "current" nutrition profile after a plan is created. Historical plans retain the versions they used.

## Candidate scoring

Use hard filters first, then a transparent weighted score.

Example score dimensions:
- calorie fit
- protein adequacy
- carbohydrate fit for post-workout context
- fiber/produce coverage
- sodium penalty
- saturated-fat penalty
- added-sugar penalty
- portability/ease metadata
- repetition penalty
- user preference score
- confidence/freshness score

The exact weights belong in a versioned policy and must be testable.

## Target calculation

The first target calculator should be explicit about assumptions and produce a versioned proposed target.

It must not claim clinical precision.

A user-approved target becomes the active target policy.

## Trend calculation

Use robust rolling summaries rather than raw day-to-day differences.

Requirements:
- minimum data coverage
- consistent local-day boundaries
- trend algorithm version
- stale-data detection
- documented target-adjustment cooldown

## No-plan behavior

If no candidate satisfies strict constraints:
1. Explain which hard constraints failed.
2. Offer the least-risk valid alternative if policy allows.
3. Otherwise state that no strict recommendation is available.
4. Never invent missing nutrition.

## Implementation status

Implemented in `backend/src/nutrition_agent/domain/nutrition/` with versioned,
deterministic policies:

| Spec area | Module | Notes |
|---|---|---|
| nutrient representation, four-state semantics | `facts.py` | KNOWN_VALUE / PUBLISHED_ZERO / DECLARED_UNAVAILABLE / UNKNOWN_ABSENT; explicit `published_zero` field |
| ingestion → engine interface | `domain/stacks/facts_bridge.py` | pure `NutritionProfile` → `NutritionFacts`/`MealLine` adapters; version-pinning |
| arithmetic | `arithmetic.py` | Decimal-only; missing-value propagation; derived facts carry empty `published_zero` |
| meal composition | `meal.py` | per-line independent scaling against each line's own published basis; weakest-link confidence |
| daily totals | `totals.py` | ordered fold of composed meals |
| target evaluation | `targets.py` | TARGET/FLOOR/CEILING/ADVISORY vs caller-supplied TargetSet; UNKNOWN preserves missing reason |
| scoring (minimal) | `scoring.py` | weighted-goal relative deviations only; strict-ineligible refusal; no confidence term |
| deterministic serialization | `serialization.py` | byte-stable JSON; stringified Decimals; sorted keys |
| candidate eligibility (planning) | `domain/planning/eligibility.py` | two-stage hard filters with reason codes (ADR-015) |
| candidate generation (planning) | `domain/planning/candidates.py` | singles + unordered distinct-food pairs; deterministic safety cap |
| deterministic ranking (planning) | `domain/planning/planner.py` | unchanged M3 score over per-slot sliced TargetSets; a versioned planner policy may then add one exact-alias soft preference adjustment; strict-only; NO_PLAN fail-closed |
| configurable meal validation/estimation | `domain/configurable_meals.py` | owner-observed structural rules and portions remain distinct from cited nutrition; missing values remain unknown; strict planner gate unchanged |
| demo meal configuration | `domain/owner_meal_config.py` | synthetic structural preferences; not source availability |
| generic external references | `domain/external_nutrition.py` | small committed USDA SR Legacy set; `estimated` only, with recipe-difference caveats |

The public release keeps incomplete evidence explicit and leaves clinical
interpretation, autonomous goal changes, and live-provider operations outside the
portfolio demo. See `docs/DECISIONS.md` for the safety decisions.
