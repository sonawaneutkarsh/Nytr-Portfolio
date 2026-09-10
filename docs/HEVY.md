# Hevy Training Integration

Hevy is an optional source of detailed workout, exercise, and set evidence. A
backend-only adapter can import bounded pages using an operator-provided API key;
the key is never accepted from iOS, stored in the database, or logged. The public
mirror ships no key, live workout history, or scheduled sync.

HealthKit remains the high-level workout authority. Hevy records are additive and
never qualify a HealthKit training day, alter calories/targets/plans, or write back
to Hevy. The two source identities are never fuzzy-matched.

Analytics and coaching are read-only deterministic projections over immutable,
owner-scoped evidence. Missing, stale, incomparable, or unsupported sets hold or
fail closed. Any future AI explanation remains downstream of those projections.

Use the synthetic JSON fixtures under `backend/tests/fixtures/hevy/` when running
the public test suite. Consult the official [Hevy API documentation](https://api.hevyapp.com/docs/)
before configuring a private deployment.
