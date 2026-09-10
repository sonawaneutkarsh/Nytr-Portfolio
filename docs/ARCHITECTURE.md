# Nytr architecture

Nytr is a modular monolith:

```text
authoritative sources -> bounded adapters -> PostgreSQL/RLS
                                  -> deterministic Python projections
                                  -> SwiftUI presentation
                                  -> optional, non-authoritative AI prose
```

HealthKit is accessed only by the native iOS companion. Hevy workout detail is
kept in a separate official-source lane. Dining data is provider evidence, not
consumption evidence. Owner profile and waist measurements are separate from
HealthKit weight and approved nutrition targets.

All user-owned records are authenticated and owner-scoped. Historical evidence
is append-only where correction semantics matter. Missing, partial, estimated,
or unsupported inputs fail closed rather than becoming fabricated facts.
