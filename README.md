# Nytr

Nytr is an evidence-driven nutrition and training companion that turns messy
health, food, dining, and workout inputs into transparent daily decisions.

## The problem

Nutrition apps often blur estimates, recommendations, and facts. Nytr keeps them
separate: external evidence is captured with provenance, deterministic Python
performs the arithmetic and trend analysis, and the owner explicitly approves
targets or records consumption.

## Key features

- Deterministic calorie/protein targets, target review, and body-weight trends.
- Body & Goals setup for HealthKit weight, height, optional target weight, and
  timestamped owner-measured waist evidence.
- Native SwiftUI HealthKit sync for body mass and workouts.
- Official Hevy workout detail with source-authority-preserving coaching.
- Exact barcode food provenance with explicit review before import.
- Provider-bounded dining menu/nutrition ingestion with fail-closed parsing.
- Optional Nytr Review: structured, privacy-minimized Gemini explanation only.

## Architecture and authority model

```text
HealthKit / Hevy / dining / owner facts
              -> authenticated adapters
              -> PostgreSQL + row-level security
              -> deterministic Python domain
              -> SwiftUI companion
              -> optional non-authoritative AI prose
```

HealthKit is authoritative for factual body weight and workout observations.
Hevy is authoritative for imported exercise/set detail. Dining providers are
authoritative only for published menu/nutrition evidence. Owner-entered profile,
waist evidence, targets, goal direction, and consumption decisions remain
explicit and separate. AI cannot calculate, correct, or write authoritative
facts.

## Tech stack

Python 3.11+, FastAPI, PostgreSQL/Supabase, psycopg, Pydantic, pytest, Ruff,
strict mypy, SwiftUI, Swift Charts, HealthKit, and a provider-neutral AI port.

## Safety model

All numeric nutrition, target, trend, eligibility, and coaching decisions are
deterministic and testable. Missing or partial evidence fails closed. Starting
calories are clearly labeled estimates until the owner approves an immutable
target. Waist is supplementary progress evidence: it never infers body fat,
changes calories, or switches a gain/maintain/lose phase automatically.

## Screenshots

Screenshots can be added here after a privacy-reviewed device capture:

- `docs/screenshots/today.png`
- `docs/screenshots/body-and-goals.png`
- `docs/screenshots/training.png`

No personal screenshots or production exports are included in this mirror.

## Local setup

```bash
cp .env.example .env
cd backend
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest
```

For the iOS target, open the project generated from
`ios/NutritionHealthCompanion/Project/project.yml` and provide local placeholder
configuration from `ios/NutritionHealthCompanion/Project/Config.xcconfig`.
Never commit local credentials.

## Synthetic data and privacy

The public mirror contains fabricated menu/label fixtures and no real health,
body, routine, class, gym, meal, device, or production records. It does not
bundle institutional dining pages or credentials. Any real integration must be
configured privately and must satisfy the provider's terms and data-use rules.
Open Food Facts data remains community data; attribution obligations are listed
in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Project status

Nytr is production-released and engineering-complete. Feature development is
frozen; physical-device demo validation, privacy-reviewed screenshots, demo
capture, and résumé/portfolio presentation remain deferred. This sanitized
mirror intentionally excludes production data and deployment configuration.

See [the public architecture notes](docs/ARCHITECTURE.md),
[security guidance](SECURITY.md), and [the roadmap](docs/ROADMAP.md).
