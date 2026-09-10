# Public Product Requirements

Nytr is an evidence-driven nutrition and training companion. This mirror keeps
the implementation and tests runnable with synthetic menu data; an institutional
dining adapter can be configured separately by an operator.

- Published menu and nutrition values retain provenance and never get guessed.
- Deterministic Python code owns arithmetic, targets, feasibility, trends, and coaching.
- Plans are recommendations; only explicit owner actions create consumption records.
- HealthKit owns body mass and high-level workout observations on iOS.
- Hevy, when connected, supplies additive exercise/set detail and cannot alter HealthKit facts.
- Optional AI explains a minimized deterministic snapshot and is never authoritative.
- User-owned records require authenticated access and database row-level security.
- Incomplete, stale, or unsupported evidence fails closed.

Out of scope for this public release are autonomous goal changes, clinical claims,
multi-campus operations, background training classification, and a general agent
or microservice framework.
