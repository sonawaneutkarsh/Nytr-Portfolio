# Security and privacy

Treat health, nutrition, workout, authentication, and device data as sensitive.
Never commit API keys, JWTs, database credentials, private keys, personal
exports, or real fixtures. Keep deployment secrets outside Git and outside the
iOS bundle.

The backend authenticates owner requests and uses PostgreSQL row-level security.
HealthKit is accessed only by the native iOS companion. Gemini receives only the
bounded aggregate snapshot described in `docs/LLM_ARCHITECTURE.md`; it is
optional, non-authoritative, and not persisted. Public fixtures are synthetic.

To report a vulnerability, do not include secrets or personal data in an issue.
Contact the repository owner through the private project channel instead.
