# Security and privacy

Treat health, nutrition, workout, authentication, and device data as sensitive.
Never commit API keys, JWTs, database credentials, private keys, personal
exports, or real fixtures. Keep deployment secrets outside Git and outside the
iOS bundle.

The backend authenticates owner requests and uses PostgreSQL row-level security
so each owner reads only their own rows. HealthKit is accessed only by the native
iOS companion. Institutional dining ingestion is disabled by default
(`STACKS_INGESTION_MODE=blocked`) and no institutional pages or credentials are
included here.

## AI paths

Nytr's AI explanation layer is optional and never authoritative.

- **On-device (default path).** Apple Foundation Models runs locally through
  `Support/OnDeviceReview.swift`, gated on `SystemLanguageModel` availability. No
  evidence leaves the device and no third-party API key is required. Debug output
  records only a fixed failure category, never prose, prompts, or identity.
- **Cloud (opt-in, disabled by default).** A provider-neutral port can send the
  bounded aggregate snapshot described in `docs/LLM_ARCHITECTURE.md`. It is off
  unless `GEMINI_AI_REVIEW_ENABLED` is explicitly set, is non-authoritative, and
  its output is not persisted as fact.

Either way, deterministic Nytr analysis remains the source of every number.

## Reporting

To report a vulnerability, do not include secrets or personal data in an issue.
Contact the repository owner through the private project channel instead.
