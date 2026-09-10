# Database Schema

System of record: Supabase PostgreSQL (ADR-002). Migrations live in
`backend/migrations/` and are idempotent (`IF NOT EXISTS`), reversible via
commented `DROP` statements, and contain **no DELETE anywhere** (ADR-013).
Replacement semantics = insert-new-version + supersede pointers, or explicit
append-only state machines (tombstones, plan runs).

Conventions shared by all migrations:

- `uuid` primary keys; `timestamptz` timestamps.
- CHECK-constrained enum-like text columns.
- Every user-owned table carries `user_id uuid NOT NULL`, has
  `ENABLE ROW LEVEL SECURITY`, and an owner policy comparing `user_id` to the
  Supabase JWT subject (`current_setting('request.jwt.claim.sub', true)`),
  executed server-side under `SET LOCAL ROLE authenticated`.
- A scratch-DB bootstrap creates the standard `authenticated` role and grants
  membership to `postgres` so the RLS proof tests run anywhere.

The sections below group global and user-owned surfaces for readability;
filenames `0001` through `0010` remain the authoritative application order.

## Migration 0001 — Stacks ingestion (M2, ADR-013) — GLOBAL/shared data

| Table | Purpose | Key identity |
|---|---|---|
| `stacks_ingestion_run` | ingestion run spine + quarantine stats context | `run_id` |
| `source_snapshot` | immutable fetched pages, SHA-256-addressed | `UNIQUE(source_system, content_sha256)` |
| `stacks_food` | food IDENTITY only | `UNIQUE(campus_id, name_normalized)` |
| `nutrition_profile` | immutable nutrition VERSION per snapshot | `UNIQUE(food_id, snapshot_id)`; supersede chain via `superseded_by`/`valid_to`; partial index on unsuperseded rows |
| `menu_offering` | MENU AVAILABILITY instance (date/period/food/ordinal); pins exact `profile_id` + `snapshot_id` | `UNIQUE(service_date, meal_period, campus_id, food_id, occurrence_ordinal)` |
| `quarantine_record` | fail-closed parse/validation rejects, never engine input | indexed by run |

These tables are deliberately SHARED (not user-owned): Stacks is the source of
truth for everyone in this single-user system; no RLS applies here by design.
Backend menu reads use the trusted backend database connection. They do not run
as the per-user `authenticated` role, and that role is not granted direct access
merely to support planning.

## Migration 0005 — Accepted menu-page versions (M7 Step 5A, ADR-020) — GLOBAL/shared data

| Table | Purpose | Key identity |
|---|---|---|
| `menu_page_version` | immutable affirmative acceptance observation of one validated date/period/campus source page | `page_version_id`; after 0006: `UNIQUE(ingestion_run_id, service_date, meal_period, campus_id)` |
| `menu_page_offering` | immutable accepted offering facts: stable correlation plus exact food/name/order/source/tag, nutrition-response snapshot/state, and optional exact profile pins | `PRIMARY KEY(page_version_id, offering_id)`; non-null food/snapshot FKs; state/profile CHECK |

`menu_page_version.validation_state` is exactly `validated_nonempty` or
`validated_empty`; CHECK constraints bind non-empty to a positive
`offering_count` and empty to zero. The membership foreign key is deferred so
the trusted persistence adapter can insert all membership rows and insert the
acceptance marker last in one transaction. The marker and normalized page writes
either commit together or all roll back.

Every non-empty membership row pins `food_id`, `name_normalized`,
`occurrence_ordinal`, `category_name`, `category_position`, `item_position`,
`source_mid`, and `dietary_tags`. Migration 0007 adds exact
`nutrition_snapshot_id` and `nutrition_source_state`. `profile_available`
requires `profile_id`; `source_placeholder`, `source_incomplete`, and
`source_unavailable` require it to be null. The live `menu_offering` row may
continue to mutate under M2 upsert behavior but is not accepted-page authority.

`source_snapshot` content deduplication now returns its canonical persisted UUID
to callers. Accepted versions use that identity. No acceptance history is
backfilled for pre-0005 data because the old schema cannot prove page-level
validation or complete membership.

These tables have no RLS because they are shared source data. Migration 0005
explicitly revokes all direct privileges from `authenticated`; only trusted
backend infrastructure accesses them.

## Migration 0006 — Accepted menu-page observation ordering (M8, ADR-024) — GLOBAL/shared data

Migration 0006 drops 0005's permanent
`UNIQUE(service_date, meal_period, campus_id, snapshot_id)` constraint. That
constraint incorrectly erased a later observation when canonical content
repeated (A→B→A). It adds run/page uniqueness instead, allowing separate runs to
accept the same canonical `snapshot_id`, and adds the authoritative lookup index
`(service_date, meal_period, campus_id, accepted_at DESC, page_version_id DESC)`.

The reader uses that accepted-observation order. The UUID is the deterministic
tie-break. No history is fabricated for pre-0005 data, and 0006 adds no RLS or
`authenticated` privileges because these remain trusted global source tables.

## Migration 0007 — Offering nutrition source state (M8, ADR-025) — GLOBAL/shared data

Migration 0007 makes `menu_page_offering.profile_id` nullable and adds non-null
`nutrition_source_state` plus `nutrition_snapshot_id REFERENCES
source_snapshot(snapshot_id)`. Existing accepted rows are backfilled from their
immutable profile to `profile_available` and the profile's exact snapshot. A
CHECK enforces profile presence only for `profile_available` and profile absence
for all three recognized non-profile states. Offering count still means total
displayed occurrences. No RLS or `authenticated` privileges are added.

## Migration 0009 — Resumable label observations (M11C, ADR-031) — GLOBAL/shared data

`menu_label_observation` is immutable evidence for one classified label result,
not a menu-availability record. Its exact same-page key is `service_date`,
`meal_period`, `campus_id`, `name_normalized`, `source_mid`, and
`occurrence_ordinal`. It additionally pins the originating `menu_snapshot_id`,
canonical `nutrition_snapshot_id`, optional exact `profile_id`, parser version,
ingestion run, and `recorded_at`. A CHECK requires a profile exactly for
`profile_available`; the three approved non-profile states require no profile.

Uniqueness across the logical occurrence, parser, and nutrition snapshot makes
repeated persistence idempotent while permitting a later immutable label
response. The lookup index follows the exact page/occurrence key and newest
observation order. No `menu_page_version` foreign key exists deliberately: the
observation must survive a cap-truncated page, while the planner continues to
read only fully accepted page versions and memberships. The table has no RLS and
no direct `authenticated` privileges because it is trusted global source data.

## Migration 0002 — HealthKit body mass (M5, ADR-016) — user-owned

`health_body_mass_sample`: idempotent history keyed `UNIQUE(user_id,
hk_sample_uuid)`; tombstone-only rows represent deletions seen before their
sample (measurement fields jointly NULL, enforced by CHECKs; active rows always
carry a value). CHECK constraints cap values 20–400 kg. Partial indexes support
per-user recent/active queries. Policy `hbm_owner_all`.

M9 adds no migration and no trend table. Its owner-scoped repository read uses
the existing `ix_hbm_user_active` path over a half-open absolute timestamp
range, excludes `tombstoned_at IS NOT NULL`, and returns raw active timestamps
for IANA local-date grouping in pure Python. Derived summaries are not database
truth or mutable cache state.

## Migration 0010 — HealthKit training sessions (M12A, ADR-036) — user-owned

`training_session` stores complete immutable workout observations keyed by
`UNIQUE(user_id, source_system, source_record_id)`. Required start/end timestamps
are ordered by CHECK; optional duration seconds and workout-attached active-energy
kcal are nonnegative `numeric` values. Activity type, timezone, source name,
bundle identifier, and source revision remain nullable rather than inferred.
Only the first `tombstoned_at: NULL -> non-NULL` transition is legal; a trigger
rejects every observation-field rewrite and repeated tombstone mutation.

`training_session_tombstone` is a narrow immutable identity ledger for deletion
events received before an add. It deliberately has no foreign key to
`training_session`: requiring one would force fake timestamps into the approved
complete-observation table. Add and delete triggers take the same transaction-
scoped advisory lock for one owner/source identity, and session insertion checks
the tombstone ledger after acquiring it. Thus a tombstone wins deterministically
and no concurrent or later add can resurrect the source identity.

Both tables enable owner RLS using the established JWT-subject expression.
`authenticated` receives `SELECT`/`INSERT`; `training_session` additionally
grants only column-level `UPDATE(tombstoned_at)`. Neither table grants `DELETE`,
and the tombstone ledger grants no update. The recent active-session index is
`(user_id, started_at DESC) WHERE tombstoned_at IS NULL`.

## Migration 0003 — Daily plans & target policies (M6, ADR-017/018)

All five tables below are user-owned with RLS enabled AT CREATION and per-table
owner policies mirroring 0002 (`plan_version`/`plan_item` enforce ownership
through join paths back to `plan_run.user_id`; `decision_log` enforces
ownership through the referenced `target_policy_version`).

All five M6 tables grant only `SELECT` and `INSERT` to `authenticated`.
Migration 0003 explicitly revokes `UPDATE` and `DELETE`, including when reapplied
over an earlier scratch-database draft, so the immutable/append-only contract is
enforced by PostgreSQL privileges as well as repository APIs.

### `target_policy_version`
Immutable approved goal sets. Columns: `version_id PK`, `user_id`,
`policy_version` label, `goals jsonb` (normalized `{nutrient, kind, value(str),
weight(str)}` list validated through domain reconstruction), `payload_sha256`,
`created_at`. Uniqueness: `(user_id, policy_version)` AND
`(user_id, payload_sha256)` — neither a reused label nor an identical payload
can fabricate a new "decision".

### `decision_log`
Minimal human-approval audit (ADR-007/ADR-018). One row PER approved version,
written atomically with it: `subject='target_policy'`, `decision='approved'`,
non-empty `rationale`, FK `policy_version_id`, `decided_at`. Not a generic
event-sourcing system.

### `plan_run`
Execution/audit spine. `(user_id, requested_for_date, inputs_fingerprint)`
UNIQUE ⇒ identical resolved inputs yield exactly one logical run
(idempotency; ADR-017 §2). The fingerprint binds schedule/planner/target
versions + target payload sha256 + menu snapshot sha256, EXCLUDING all clock
reads. Status ∈ {completed, no_plan}; union of planner reason codes stored as
array. started/finished timestamps are execution metadata only. Migration 0004
adds nullable `target_policy_version_id` referencing
`target_policy_version(version_id)`; NULL preserves legacy M6 rows while new
generation pins the policy used by that run.

### `plan_version`
The IMMUTABLE plan artifact: `plan_canonical text` holds the byte-stable M3
serialization verbatim (hash basis), `plan_jsonb` is a structurally identical
projection for query/debug use, `plan_sha256 = SHA-256(canonical bytes)`,
UNIQUE per run. Historical plans are never re-derived from "latest" anything.

### `plan_item`
Queryable candidate metadata: slot index/context/rank/candidate id/menu period,
pinned arrays `food_ids[]`, `offering_ids[]`, `profile_ids[]` (exact immutable
nutrition_profile row ids captured at plan time — survives offering upserts),
`score_total`/`calories_kcal` decimal strings. UNIQUE(version, slot, rank).
The row's `item_id` is exposed only through a validated completed-response
projection and is the authoritative consumption target; it is not part of the
canonical plan artifact or SHA.

## Migration 0004 — Consumption + historical target-policy reference (M7) — user-owned

Migration 0004 adds the nullable `plan_run.target_policy_version_id` foreign key
described above and creates `plan_consumption`:

| Column/constraint | Contract |
|---|---|
| `entry_id uuid PRIMARY KEY` | immutable persisted event identity |
| `user_id`, `plan_run_id`, `plan_version_id`, `item_id` | owner plus exact plan hierarchy FKs |
| `state` | CHECK-constrained to `eaten`, `skipped`, `unavailable`, `alternative` |
| `client_event_id` | client retry identity |
| `recorded_at` | server persistence timestamp, retained on replay |
| unique event key | `(user_id, plan_version_id, item_id, client_event_id)` |

The owner RLS policy verifies both the row's `user_id` and one joined
`plan_run → plan_version → plan_item` hierarchy owned by the JWT subject. This
prevents cross-parent UUID combinations even when each FK exists separately.
The authenticated role receives only `SELECT` and `INSERT`; `UPDATE` and
`DELETE` are explicitly revoked. Exact retries/conflicts are interpreted by the
repository without an upsert or overwrite.

## Migration 0008 — Goal policy and target-review lifecycle (M10) — user-owned

### `goal_policy_version`

Immutable per-user goal intent: UUID/version label, `maintain`/`gain`/`lose`,
exact numeric `desired_rate_kg_per_week`, semantic payload SHA-256, and
`created_at`. Database checks enforce direction/rate sign. Unique owner/label
and owner/payload keys prevent fabricated duplicate versions.

### `target_review`

One immutable deterministic review. It references the exact goal policy and
prior approved target policy, stores date/timezone, M9 algorithm/input digest,
review-policy version, typed state/reasons, exact current/proposed calories and
delta, the complete canonical evaluation JSON, and recommendation SHA-256.
`UNIQUE(user_id, recommendation_digest)` is the logical idempotency key. Only
`recommendation_ready` rows may contain proposal columns.

### `target_review_decision`

Append-only terminal `approved`/`rejected` lifecycle. Approved rows require a
resulting target-policy FK; rejected rows require none. Owner/review and
owner/client-event uniqueness prevent multiple terminal decisions or retry
duplication. Exact replay is reconstructed from the original decision and, for
approval, its joined target-policy and decision-log rows.

The M10 approval repository takes the same per-owner PostgreSQL advisory
transaction lock used by ordinary goal/target-policy writers, rechecks latest
goal/target UUIDs, and inserts the resulting target policy, mandatory
`decision_log`, and review decision in one serializable transaction. Any insert
failure rolls back all three. Rejection inserts only the review decision.

All three tables enable RLS at creation. Policies verify the JWT subject owns
the row and all referenced goal/target/review parents. `authenticated` receives
SELECT/INSERT only; UPDATE and DELETE are explicitly revoked.

## Design boundaries preserved

FOOD IDENTITY (`stacks_food`) ≠ NUTRITION VERSION (`nutrition_profile`) ≠
LIVE MENU AVAILABILITY (`menu_offering`) ≠ ACCEPTED PAGE VERSION/MEMBERSHIP
(`menu_page_version` + `menu_page_offering`) ≠ MEAL CONFIGURATION (future
ComponentPortionPolicy + pinned plan items; ADR-011 gate). Plans reference
versions, never catalogs or "latest" pointers (ADR-017 §4).

## M14A detailed training revisions (migration 0011)

Detailed structure is normalized separately from high-level
`training_session`: `training_detail_session_revision` appends one immutable
source revision, `training_detail_exercise` and `training_detail_set` preserve
ordered membership, and `training_detail_session_tombstone` permanently marks
one deleted source session identity. Composite `(parent_id, user_id)` foreign
keys prevent cross-owner attachment. All four tables use JWT-subject RLS and
grant authenticated users only `SELECT` and `INSERT`; no detail row contains a
source credential. Historical revisions remain stored after update/deletion.

## M14B Hevy sync checkpoints (migration 0012)

`training_detail_sync_checkpoint` appends one owner/source synchronization fact
only after a complete detailed-training batch is durably stored. It records the
bootstrap/incremental mode, source event watermark, parser/provider versions,
bounded page/request/attempt/retry counts, and completion time. It contains no
API credential or raw response payload.

The SQL repository inserts detail revisions/exercises/sets, deletion tombstones,
and the checkpoint in one transaction. Any failure rolls back all of them. A
trigger serializes each owner/source checkpoint stream, requires bootstrap
first and incremental mode afterward, and rejects a regressing source
watermark. The table is owner-RLS data under the JWT-subject convention;
`authenticated` receives `SELECT` and `INSERT`, never `UPDATE` or `DELETE`.

## Manual foods (migration 0013)

`custom_food` is the owner identity, `custom_food_version` stores immutable
serving and nullable nutrition facts, and `manual_food_consumption` freezes one
scaled factual event. Composite foreign keys bind versions and events to the
same owner. All three use JWT-subject RLS and authenticated SELECT/INSERT only.
Nullable nutrients mean unknown, not zero. Migration 0013 was later applied and
physically validated during the M15 release.

## M16A reviewed targets and next-meal artifacts (migration 0014)

`protein_target_proposal` pins the exact owned body-mass sample and prior
target-policy version, calculation inputs/result, rationale, provenance,
generation time, and evidence digest. Append-only
`protein_target_proposal_decision` permits one terminal approve/reject decision;
approval references the ordinary `target_policy_version` written atomically.

`next_meal_recommendation` stores one immutable owner/client-request artifact:
decision date/timezone/time, typed status/reasons, optional target-policy pin,
inputs digest, canonical artifact JSON, and artifact SHA-256. Successful and
typed-failure artifacts retain the factual ledger snapshot and all available
menu/schedule/target evidence; successful artifacts additionally pin selected
and bounded alternative candidates. Numeric proposal checks reject PostgreSQL
NaN/Infinity and digest checks require lowercase SHA-256 hex. It has no
consumption state or update path. All three tables use JWT-subject RLS and grant
`authenticated` only `SELECT`/`INSERT`.

## M16B explicit next-meal consumption (migration 0015)

`next_meal_consumption` is a dedicated immutable owner event; it is not a
`plan_consumption` row and has no fabricated plan hierarchy. Its composite
foreign key binds `(user_id, recommendation_id)` to the same-owned immutable
`next_meal_recommendation`. The only allowed state is `eaten`.

The row freezes the recommendation artifact/policy identifiers, local
date/timezone, server-recorded instant, meal period/context, item and serving
or configuration identifiers, selected-candidate canonical JSON and SHA-256,
nutrition authority/confidence, nullable finite nonnegative calories/protein,
and explicit unknown-nutrient names. A future menu, policy, or recommendation
implementation cannot rewrite this evidence.

`UNIQUE(user_id, client_event_id)` provides owner-global retry identity and
`UNIQUE(user_id, recommendation_id)` permits only one consumption event per
recommendation. RLS checks the JWT subject and recommendation ownership.
`authenticated` receives `SELECT` and `INSERT` only; all column updates plus
row `UPDATE`/`DELETE` remain unavailable.

## M17 body-mass evidence integrity (migration 0016)

Migration 0016 hardens the existing `health_body_mass_sample` table without
adding a table, column, backfill, or data transformation. Every factual column
is immutable after insert. A `BEFORE UPDATE` trigger permits only the first
`tombstoned_at` transition from `NULL` to non-`NULL`; it rejects factual
rewrites, repeated tombstones, tombstone timestamp replacement, and tombstone
reversal, including for privileged writers.

The `authenticated` role retains owner-RLS `SELECT` and `INSERT`, loses the
original table-wide `UPDATE`, and receives only
`UPDATE(tombstoned_at)`. `DELETE` remains unavailable. Existing idempotent
sample insertion, deletion-before-add tombstones, retained observations, and
latest-active body-mass queries keep their established semantics.

## B1 barcode food provenance (migration 0017)

Migration 0017 extends the existing M15 lane rather than creating another
consumption system. Nullable `custom_food.source_system/source_key` identify an
owner's exact provider product and have a partial unique index. Manual foods
keep both columns null. `custom_food_version.provenance_jsonb` freezes authority,
provider, scanned/provider code, product URL, fetch time, allowlisted payload
SHA-256, data license, and nutrition basis; legacy rows default explicitly to
`owner_entered`.

`manual_food_consumption` additionally freezes nutrition authority, confidence,
and provenance summary. Its source check permits only `manual_custom` or
`barcode_open_food_facts`. The existing owner RLS and authenticated
SELECT/INSERT-only grants are unchanged; no mutation or deletion path is added.

## M21 evidence-grounded AI review (no migration)

M21 is compute-and-explain on demand. It reads existing owner-scoped ledger,
progress, goal/target, body-trend, and latest-next-meal evidence and persists
neither the minimized snapshot nor provider output. It adds no table, column,
index, RLS policy, grant, or migration. There is intentionally no AI review or
chat-history system of record.

## Body & Goals profile (migration 0018)

Migration 0018 adds the owner-scoped `owner_body_profile` table for explicit
height and optional target weight, plus append-only `waist_measurement` evidence.
HealthKit body mass remains the authoritative weight source; these tables do not
rewrite HealthKit observations, targets, or goal policy. Both tables use JWT-
subject RLS. Authenticated users may select/insert profile rows and select/insert
waist evidence; waist rows cannot be updated or deleted. The public mirror ships
the additive migration for local demonstration only and contains no owner data.
