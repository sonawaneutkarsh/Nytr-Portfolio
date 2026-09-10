# AI Review Boundary

Nytr's optional AI review explains a deterministic snapshot; it does not make
nutrition, target, eligibility, training, or health decisions. The backend sends
only a minimized, allowlisted summary and never sends tokens, identifiers, raw
history, or source-provider text.

The provider is behind a small typed port. Missing configuration, timeout,
quota, malformed output, safety refusal, authentication failure, and outages
become coarse unavailable states. The app keeps working from deterministic data.

AI output is bounded structured prose, is not persisted, and is never treated as
evidence. Python/domain policies remain authoritative for arithmetic, nutrition,
targets, progress, and coaching. See the root README for the high-level model.
