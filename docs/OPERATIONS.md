# Public operations notes

This mirror is safe for local development and CI. It does not contain a
production scheduler, credentials, raw institutional pages, or deployment
identifiers. Configure a private deployment separately using the variable names
in `.env.example` and your platform's secret store.

The public workflow runs tests only. Any live dining integration must be an
explicit, authorized, bounded operation against a provider whose terms permit
it; this repository does not bundle provider content.
