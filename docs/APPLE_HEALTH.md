# Apple Health / HealthKit Boundary

The native SwiftUI companion is Nytr's only HealthKit client. It requests the
smallest read set needed for body mass and workout observations; it does not write
health data or request unrelated metrics.

Sync is incremental and idempotent. A transient paging anchor lives only during a
run; the durable anchor advances only after the backend commits the corresponding
batch. Deletions are retained as tombstones, so a deleted sample cannot silently
reappear. Missing observations remain unknown rather than zero.

HealthKit owns high-level workout observations and body weight. Hevy, when enabled,
adds richer set detail but cannot alter HealthKit facts or nutrition decisions. The
backend receives authenticated, minimized payloads and stores user-owned records
behind row-level security.

The public mirror contains synthetic tests only. Physical-device authorization,
real samples, and background delivery are deployment validation tasks, not claims
made by this repository.
