# Security and Privacy

Nytr treats health, nutrition, workout, schedule, device, and authentication data
as private by default.

## Boundaries

- User-owned API routes require a verified bearer token; identity comes from its
  subject, not a client-supplied user ID.
- PostgreSQL row-level security and narrow authenticated grants protect user data.
- HealthKit is read-only on device. Server-only provider keys never enter iOS,
  fixtures, logs, or responses.
- Optional AI receives a minimized allowlist of deterministic state. Prompts and
  responses are not persisted or logged.
- External menu and barcode adapters validate provider responses and retain
  provenance; missing data is never guessed.

## Local development

Copy `.env.example` to an ignored `.env` and use synthetic fixtures. Never commit
real health exports, provider pages, tokens, database URLs, or private keys. The
public mirror intentionally has no scheduled live-ingestion workflow, credentials,
or institutional source dumps.

Report suspected vulnerabilities privately to the repository owner rather than
opening an issue containing sensitive data.
