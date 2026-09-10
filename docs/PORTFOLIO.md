# Nytr Portfolio Notes

## One-line description

Built Nytr, an evidence-driven personal nutrition and training decision system that turns
versioned Stacks, HealthKit, Hevy, and owner-recorded data into deterministic plans,
progress views, and optional AI explanations.

## Résumé bullets

- Designed and shipped a Python/FastAPI + PostgreSQL modular monolith and native SwiftUI
  client that preserves source provenance, Decimal nutrition arithmetic, immutable decision
  artifacts, and an explicit recommendation-versus-consumption boundary.
- Implemented privacy-conscious HealthKit and official Hevy ingestion with idempotent sync,
  tombstones, PostgreSQL Row Level Security, narrow append-only privileges, and deterministic
  body-weight/training analytics that fail closed on stale or partial evidence.
- Added an owner-triggered Gemini review behind a provider-neutral port using a minimized
  allowlisted snapshot, bounded structured output, no AI persistence, and typed degradation;
  deterministic Python remains authoritative for every number and decision.

## Key technologies

Python 3.11+, FastAPI, Pydantic, PostgreSQL/Supabase, psycopg, pytest, mypy, Ruff,
Swift 5.10, SwiftUI, Swift Charts, HealthKit, Keychain, Xcode/XCTest, Docker, Render,
GitHub Actions, Hevy REST API, and Gemini Developer API.

## Engineering highlights

- Immutable raw snapshots, accepted menu-page versions, exact nutrition-profile pins, and
  stable plan hashes make historical recommendations reproducible.
- Four-state nutrient semantics preserve published zero, known value, declared unavailable,
  and absent/unknown instead of silently zero-filling incomplete labels.
- Owner identity comes from verified JWT claims; database RLS and constrained grants provide
  defense in depth for health, nutrition, target, plan, and training history.
- Compute-on-read trends and progress avoid a second mutable analytics truth while preserving
  timezone-correct local-day attribution and explicit denominators.
- External adapters are bounded, fixture-tested, and operationally optional. Provider failure
  cannot erase accepted evidence or bypass deterministic eligibility.

## Interview talking points

1. **Why a modular monolith?** One-user scale benefits from a single deployment and transaction
   boundary; pure domain modules preserve testability without microservice overhead.
2. **Why immutable evidence?** Menus, nutrition, targets, and recommendations change. Pinning
   exact versions makes old decisions explainable instead of reinterpreting them through the
   latest data.
3. **Why separate recommendation and consumption?** Intent is not fact. Only explicit owner
   confirmation can change the recorded ledger.
4. **Why two training lanes?** HealthKit is the high-level workout authority available on
   device; Hevy provides richer set detail. Without a shared identity, automatic fuzzy linking
   would manufacture certainty.
5. **Where does AI fit?** Downstream of deterministic state. Gemini improves explanation, not
   arithmetic, eligibility, target policy, or persistence.

## Architectural tradeoffs

- **Chosen:** strict provenance and conservative unavailable states. **Cost:** fewer confident
  recommendations when source data is incomplete.
- **Chosen:** append-only/versioned history. **Cost:** more storage and more deliberate reads,
  acceptable at personal scale.
- **Chosen:** compute-on-read analytics. **Cost:** repeated bounded calculation, in exchange for
  avoiding cache invalidation and derived-truth drift.
- **Chosen:** no HealthKit↔Hevy fuzzy reconciliation. **Cost:** two visible evidence lanes, in
  exchange for honest identity semantics.
- **Chosen:** no general scheduler or agent framework. **Cost:** narrow operational tooling,
  in exchange for smaller attack surface and lower maintenance.

## Strongest safety and failure-mode decisions

- Stale, partial, unknown, and unavailable evidence remains explicit; missing values never
  become zero.
- Parser or source failure quarantines the attempt and retains the last accepted evidence.
- HealthKit deletions become tombstones; retries are idempotent and cannot resurrect facts.
- User-owned tables use RLS; authenticated roles cannot UPDATE or DELETE immutable history
  except the single constrained tombstone transition where required.
- Gemini receives no PII, raw health/workout history, source identifiers, food/exercise text,
  or tokens; invalid, numeric, refused, timed-out, or unavailable output becomes a typed
  non-blocking failure.
- No claim is made that recorded nutrition is complete intake, that progress is causal, or
  that Nytr provides clinical accuracy.
