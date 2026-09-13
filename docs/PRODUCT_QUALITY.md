# Nytr final product pass — 2026-09-12

The real-device follow-up changes build on released `cc1e6e6b9b6016c45b1f67c63b0f034fe4b2f5e5`. Nothing is pushed or
released by this pass. The user's Apple Foundation Models replacement supersedes the
original Part 6 OpenAI architecture, credentials, billing, and provider-error checklist.

## Product composition

The previous pass improved spacing and labels within the existing navigation. This pass
adds a native Today / Food / Training / Progress tab structure, a compact calorie/protein
hero, an ordered choose / portion / log flow, adaptive teal accents, rounded surfaces,
metric hierarchy, and common informational states. Food logging has its own primary tab;
Body & Goals is directly accessible through Today and Progress.

Body & Goals separates factual body evidence, highlighted approved targets, profile editing,
and an amber estimate surface explicitly requiring approval. Progress treats a single day
as a starting point rather than a trend chart; multi-day charts preserve gaps. Training
separates recorded Hevy sessions from tinted advisory coaching. Review separates deterministic
findings from optional local prose. Existing authentication, settings, sync, and disclosure
controls retain their native behavior and the earlier polish pass's accessibility support.

## Reproduced barcode and Today defects

A read-only inspection of the user-specified public Dymatize ISO100 product record found a
packaging 100 g input set containing calories but no protein and a separate packaging serving
set with protein. The old “100 g first” policy selected the incomplete set. The new synthetic
whey fixture preserves that shape: 30 g manufacturer serving, 120 kcal, 25 g protein, plus a
partial 100 g set. No private intake record or actual barcode is committed in the fixture.

A separate read-only inspection of the owner-reported chocolate-milk record found exactly one
packaging/as-sold input set at `per: "100g"` (`per_quantity: 100`, `per_unit: "g"`) and no
`serving_size`, `serving_quantity`, `serving_quantity_unit`, `product_quantity`,
`product_quantity_unit`, or `quantity`, with an empty `packagings` list. `100 g` is therefore the
only authoritative automatic basis for that source and no mL option can be derived from it. The
synthetic `mass_only_no_quantity_evidence` fixture preserves that exact shape with synthetic values;
no real barcode or provider payload is committed. Owners can state the label serving explicitly, and
a volume against mass-based nutrition fails closed instead of assuming 1 g/mL.

Policy `barcode-food-import.v3` prefers a serving set only when explicit source quantity and
unit agree with the structured product serving metadata. A conflicting source serving cannot
inherit a different physical mass. Compatible 100 g/ml evidence is scaled to a known physical
serving. If no reliable physical serving is available, the standardized 100 g/ml basis stays
intact; a per-serving-only source can remain an explicitly unconverted serving. No scoop
mass is inferred and no source sets are merged to manufacture completeness.

For legacy OFF records without a v3 nutrition object, `proteins_100g` / `proteins_serving`
and the other supported normalized suffix values use documented canonical units and an
explicit `nutrition_data_per`. A corresponding contributed `_value` is required. The raw
contributor `_unit` does not reinterpret normalized suffixes. Estimated/computed values and
nonexact modifiers are excluded. Sodium converts source grams to milligrams deterministically.
References: [OFF product schema](https://openfoodfacts.github.io/documentation/docs/Product-Opener/schemas/schemas/product/)
and [OFF v3 product API](https://openfoodfacts.github.io/documentation/docs/Product-Opener/v3/products/get-api-v3-product-code/).

The source version freezes nutrition per physical serving. The UI defaults to “1 serving”
when that verified representation exists and permits g/ml or fractional serving counts.
Changing quantity immediately hides the stale preview and clears the consumption confirmation;
the matching Python preview replaces it when the authenticated response arrives. This needs
a backend connection. A late 30 g response cannot overwrite an 80 g preview. The log action
uses exactly the preview's physical amount and the same immutable version. Preview/import
never writes consumption. Missing nutrients display as unknown, not zero.

The Today defect reproduced from the wire contract is independent of HTTP availability:
barcode consumption adds `external_reference` to `nutrition_authorities`, but the Swift enum
omitted that valid case. Decoding failed for the entire otherwise healthy ledger response.
The enum is repaired. “Try Again” independently refetches the authenticated ledger using the
same local-date/IANA-timezone path. Request tokens and owner checks reject superseded results
and stale authorization failures. Missing and unavailable days retain their existing semantics.
The exact production HTTP response at the user's reported time was not observed; no production
logs or owner health records were accessed to infer it.

Existing food versions and consumption records are immutable. Re-scan/re-import to obtain the
corrected version after backend deployment. Historical partial records are not backfilled or
silently assigned protein.

## Training display

`nytr.training.weightUnit` is a local AppStorage preference, default `lb`, selectable as lb/kg
in Training and history. Every factual load, volume, and coaching target is formatted from the
original canonical value using exact Decimal `1 lb = 0.45359237 kg`, then rounded to one decimal
with trailing zero removed. It never chains conversions or writes source history. Rep-only
bodyweight instructions stay rep-only; converted assistance still says assistance. Unknown
source units are preserved rather than guessed. No migration is needed.

## Deterministic nutrient context

Policy: `nytr.fda-label-context.v1`. For the selected candidate's actual quantity, calculate
percent Daily Value with Decimal arithmetic. Low is <=5%; high is >=20%; moderate is between.
The rounded one-decimal percentage is presentation only; classification uses the unrounded
value. These are general adult/age-four-and-older label references, not personal prescriptions.

| Nutrient | Daily reference | Source coverage in current contracts |
| --- | ---: | --- |
| Saturated fat | 20 g | Stacks when published; absent from manual/barcode DTO |
| Sodium | 2300 mg | Stacks and manual/barcode when present |
| Fiber | 28 g | Stacks and manual/barcode when present |
| Added sugar | 50 g | Stacks when published; absent from manual/barcode DTO |
| Cholesterol | 300 mg | Stacks when published; absent from manual/barcode DTO |

Manual/barcode also support calories, protein, carbohydrate, and total fat. Total fat is never
substituted for saturated fat; total sugar is never substituted for added sugar. Potassium is
not added to this policy. Missing, invalid, and nonfinite inputs remain missing; published
zero remains zero. The read projection preserves candidate confidence, missing fields, and
its frozen source artifact. High reference contributions are visible with detail disclosures.
A low fiber contribution is a fact about the selected quantity, not a diagnosis or a food grade.

Sources: [FDA label interpretation](https://www.fda.gov/food/nutrition-facts-label/how-understand-and-use-nutrition-facts-label)
and [FDA Daily Values](https://www.fda.gov/files/food/published/DV-Percent-DV-Nutrition-Facts-Label_09072023.pdf).

Ranking is deliberately unchanged: incomplete cross-source micronutrient coverage and unknown
unlogged daily intake do not establish a defensible penalty weight or tradeoff between nutrients.
The UI distinguishes macro fit from a comprehensive quality assessment. Current daily ledgers
have calories/protein, not complete daily totals for these five fields, so v1 reports a selected
quantity's contribution to the daily reference and explicitly says unlogged intake is unknown.
It does not silently treat a reference as a personalized remaining daily allowance.

## Apple on-device Review

`AppleOnDeviceReview` uses `SystemLanguageModel.default` on iOS 26+ when FoundationModels can
be imported and the model reports `.available`. `LanguageModelSession.respond` generates an
`@Generable` structure with `@Guide` descriptions, greedy sampling, and at most 500 response
tokens. Each review uses a fresh session, with no tools or agent loop. Four required strings
(summary, key findings, considerations, limitations) must be nonempty; each is capped at 520
characters and their aggregate at 2,080 characters. URLs are rejected. A numeric claim is accepted
only when its normalized value is present in the exact deterministic input allowlist. The view model
offers cancellation and a 30-second timeout; late results cannot cross owner sessions or replace
newer state.

References: [SystemLanguageModel](https://developer.apple.com/documentation/foundationmodels/systemlanguagemodel),
[guided generation](https://developer.apple.com/documentation/foundationmodels/generating-swift-data-structures-with-guided-generation),
and [Apple's Foundation Models session](https://developer.apple.com/videos/play/wwdc2025/301/).
The implementation was also compiled against the installed Xcode 26.5 FoundationModels SDK.

An explicit “Review current evidence” action reads `GET /v1/review/snapshot` using the existing
authenticated backend. That endpoint reads deterministic data already stored by Nytr, never
calls `AIReviewProvider`, and sets `Cache-Control: no-store`. A second explicit action runs
local generation. `model_input` is a dedicated on-device allowlist:

| Field | Meaning |
| --- | --- |
| `goal` | Goal direction, or unavailable |
| `weight_evidence` | Deterministic trend availability/status category |
| `nutrition_evidence` | Today's recorded-nutrition completeness category |
| target and recorded macro fields | Exact available calories/protein plus goal kinds |
| recent nutrition averages | Exact independently computed seven-day recorded-day facts/counts |
| body trend fields | Exact available deterministic average/rate and category |
| `next_meal` | Deterministic recommendation status |
| `includes_estimates` | Boolean for recorded evidence in the seven-day window |
| `quality_flags` | Deterministic selected-quantity nutrient/band codes |
| limitation codes | Known/missing evidence and fixed recorded-intake caveats |

No names, IDs, email, location, schedule, device identity, timestamps, raw HealthKit/Hevy/food rows,
or barcodes are included. Exact values are limited to deterministic summary facts already shown in
the authenticated Nytr UI. Typical input remains small plus short fixed instructions; the output
cap is 500 tokens, with no tokenization/count guarantee across Apple model versions.
Nothing is uploaded for AI inference, no model prompt/result is logged or persisted, and no
external AI request or cloud fallback exists in this flow. Existing Nytr backend sync/storage
continues as before; this is not a claim that the whole app is an offline local database.

Apple Intelligence unavailable, disabled, or model-not-ready states show exactly “AI Review
requires Apple Intelligence.” Cancellation, refusal, validation failure, and other generation
errors preserve the deterministic findings. Generated text is non-authoritative and has no
mutation interface for consumption, targets, goals, body data, HealthKit, Hevy, plans, or coaching.

AI API cost: **$0 per review and $0 for 30 reviews/month**. There is no API account, server API
key, provider environment variable, prepaid funding, or automatic recharge to configure. Model
readiness is managed by Apple Intelligence in device Settings. Legacy server Gemini code remains
for compatibility, with its existing disabled configuration; the new iOS Review never calls it.

## Real-device UX follow-up

Food now owns detailed Lunch and Dinner browsing while Today retains its concise Next Meal action.
Both meal sections project the existing immutable daily-plan artifact and item references; they do
not introduce a second engine or state store. Primary cards show meal name and whole calories/protein,
with remaining nutrient evidence behind disclosure. The four consumption decisions are secondary
menu actions and still require an explicit backend write.

Daily and history presentation uses `NytrNumberFormat`: POSIX decimal parsing, locale-aware grouping,
and decimal half-up rounding to whole calories/protein. Detailed per-food values use at most one decimal where appropriate.
Canonical Decimal strings, arithmetic, storage, and API contracts remain unchanged. Swift request
dates now encode the device's local calendar day at UTC midnight before the existing wire formatter;
this prevents an Eastern-time Sep 12 view after 20:00 from requesting Sep 13.

Review nutrition evidence is now explicitly categorized by recorded item count and completeness:
`none`, `recorded_partial`, or `recorded_complete`. Imported/scanned products remain outside the
ledger until separately consumed. A successful consumption write invalidates any prior Review
snapshot and local explanation, requiring another explicit evidence refresh and explicit Apple
generation.

## Daily-use performance, food log, and appearance patch

The request audit counts authenticated HTTP operations at the client boundary. A cold Today surface
previously issued the plan request first, then started trend, ledger, and latest Next Meal, creating
two top-level network waves (plus one plan-consumption and/or Next Meal consumption-status read when
applicable). It now starts the four independent reads together: **4 base requests, 4–6 including
conditional consumption reads, one top-level wave**. The already-safe cached plan remains visibly
stale while revalidation runs. Same-owner activation is a no-op, so entering Food after Today no
longer repeats the Today bundle.

Initial/revisit request counts are: Food saved metadata **1/0** unless explicitly refreshed;
Progress **1/0**; Training recent analytics **1/0**; Body & Goals aggregate **1/0** and its goal,
target, and proposal reads **3 in one wave/0** when that detail is first opened; Nutrition History
**1/0**; Review snapshot **1 per explicit refresh** and Apple generation **0 network requests**.
Next Meal retrieval is included in Today; generation remains one explicit authoritative POST and an
optional consumption-status GET. HealthKit/Hevy refresh is not coupled to any ordinary screen load.
Concurrent requests with an expiring session reuse one in-flight Supabase token refresh instead of
issuing one refresh per endpoint.

Debug builds emit response-type-only `[NytrTiming]` lines with separate authentication/token,
transport, decode, and total milliseconds. They contain no URL parameters, IDs, nutrition, body,
schedule, or other owner values. This supports physical/backend cold-start comparison without
shipping telemetry. Local deterministic client tests prove same-wave start and exact request counts;
production transport/cold-start values still require the owner's device and deployed service.

Food is ordered as Quick actions, What I ate today, Lunch, Dinner, and Nutrition History. The daily
section shows all active ledger sources. Custom-food rows can be tapped or swiped to edit/remove;
other immutable plan/Next Meal entries open read-only details. Serving-count and physical-unit edits
require a fresh Python Decimal preview, then send its exact physical result. Removal is a durable void,
never client hiding. Successful edit/remove invalidates Review immediately and refreshes Today
nutrition/Next Meal plus History concurrently, without touching Training, Hevy, Body & Goals,
Progress, or Stacks.

One canonical Settings sheet is reachable through the same gear control on all four primary tabs.
`nytr.appearance` persists System/Light/Dark in AppStorage; the app root applies the corresponding
preferred color scheme and System remains the default.

## Local meal-guidance notifications and branding

ADR-055 implements only local `UNUserNotificationCenter` scheduling. The master preference defaults
off; lunch and dinner may be enabled independently. Current-plan, current-local-day, in-window,
authorized reminders use stable identifiers and generic lock-screen copy with no exact nutrition or
body values. Recorded or unresolved consumption, stale/unavailable plans, passed windows, disabled
settings, denied permission, and sign-out cancel pending requests. A user-visible test schedules the
fixed privacy-safe test payload after about five seconds. Food deep links focus Lunch or Dinner.

The asset catalog now uses an approved no-text leaf/person mark on a deep green-black field. Standard,
dark, and monochrome tinted 1024 px sources are provided through the Xcode app-icon catalog, and the
same mark appears sparingly in Sign In and Settings/About. Asset generation and resizing do not alter
product data or runtime authority.

## Release and device checklist

1. Apply `backend/migrations/0019_manual_food_adjustments.sql` once in the production Supabase SQL
   Editor, then deploy the additive backend endpoints before installing the new app. Push/deploy
   only in a separately authorized release.
2. Keep the existing remote AI provider disabled. No billing or new secret setup is required.
3. Install on an Apple Intelligence-capable iOS 26+ device and also check the unavailable state
   with Apple Intelligence disabled or its model not ready. Only explicit explanation should
   invoke generation. Check refusal/cancel behavior and that deterministic findings stay usable.
4. Re-scan the whey package, verify its label, and import the corrected version. Confirm one
   manufacturer serving, then 80 g, 1.5 servings, and 2 servings. Check preview quantity/protein,
   confirm eaten, and verify the single persisted record matches. Check unknown nutrient display.
5. On Today, recover from airplane-mode failure after restoring network; also inspect a ledger
   containing barcode food and an empty day. Verify timezone near midnight and after Health Sync.
6. Toggle lb/kg in workout detail and coaching, relaunch, and confirm persistence and unchanged
   original history. Check assisted and bodyweight exercises.
7. Inspect all tabs, Body & Goals, target approval, barcode camera, keyboard, and sheets on a small
   iPhone, in light/dark mode, large text, increased contrast, and VoiceOver. Verify estimate vs
   approved target and coaching vs recorded history. Check multi-day chart gaps and sparse evidence.
8. Verify sign-out clears Review and food preview, and another owner cannot see prior evidence.

Validation counts and screenshot artifacts are recorded in the final task report. Synthetic
production-view renders validate visible layouts; they do not replace physical camera,
VoiceOver, scrolling, interaction, or Apple-model quality checks on the owner's device.

## Validation completed locally

- Full backend suite with a disposable PostgreSQL 16 database: **1,120 passed**, no skips;
  one existing Starlette/httpx deprecation warning.
- Ruff lint and format checks: passed (248 Python files); strict mypy: passed (122 sources).
- Swift production and test harness: all stages passed (57 production / 31 test sources).
- Xcode iPhone 17 Pro simulator suite: **219 passed**, no failures.
- CLI-exported native XCTest render attachments: **24 images**, eight production views/states
  in light, dark, and accessibility3 text size, at a 393 x 852 pt viewport. Inspected the
  images and corrected large-text truncation and both light/dark primary-button contrast.
- Synthetic captures and contact sheets are in ignored `build/nytr-product-review/`.
  The final result bundle is `/tmp/nytr-product-release-check.xcresult`.
- `git diff --check`: passed. Credential-pattern scan of changed/new source and documentation:
  no matches. The protected local config backup was neither read nor staged, and remains
  untracked. No public-repository edit, live Stacks ingestion, production AI request, or push.

The Apple adapter is SDK-compiled and tested through local reviewer doubles for availability,
structured validation, success/failure, timeout, cancellation, and session isolation. Real
Apple model generation quality and physical-device behavior remain explicit device checks.

### Real-device follow-up validation

- Focused Review, barcode/consumption, daily-ledger, and history backend tests: **59 passed**;
  the only warning is the existing Starlette/httpx deprecation.
- Changed Python files: Ruff format/check passed; changed Review application/API files passed mypy.
- Swift whole-production/test harness: all stages passed (**60 production / 33 test sources**).
- Focused iPhone 17 Pro simulator suite: **77 passed**, no failures, including Review invalidation,
  owner-local date, formatting, canonical plan presentation, notification scheduling/deep links,
  test notification, and production rendering. Result: `/tmp/nytr-ux-release-20260912.xcresult`.
- The asset compiler accepted the standard/dark/tinted AppIcon catalog with no icon warning and
  produced the thinned app icon plus `Assets.car`.
- Render test exported **24 images**. Food and Today were visually inspected in light, dark, and
  accessibility3 text size; physical scrolling, delivery, camera, VoiceOver, and icon appearance
  remain device-review gates.

### Daily-use patch validation

- Full backend suite against a fresh disposable PostgreSQL 16 database with migrations through
  0019: **1,124 passed**, no skips; one existing Starlette/httpx deprecation warning.
- Ruff check and format check passed; strict mypy passed across **122 source files**.
- Swift whole-production/test typecheck harness passed (**60 production / 34 test sources**).
- Full Xcode iPhone simulator suite: **238 passed**, no failures, including production rendering,
  request deduplication/concurrency, correction/void, appearance, and numeric-grounding coverage.
- Migration 0019 reapplication, owner isolation, append-only privileges, correction active-leaf
  totals, voided zero contribution, and retained audit rows passed on PostgreSQL 16.
- Physical request timing, Apple model prose quality, camera, VoiceOver, and real notification/
  HealthKit behavior remain device checks; no production backend, Stacks, or remote AI call ran.

### Final physical-device defect policy

- Barcode liquid handling uses only structured OFF serving/product/packaging quantity units to
  choose a mass or volume lane. Compatible 240 mL servings scale exact per-100-mL facts; a lone
  per-100-g set stays 100 g, and “1 bottle” without physical evidence stays one serving.
- The Apple schema was compiled against the installed FoundationModels SDK. Local failure telemetry
  is a fixed category only. The validator accepts exact signed and window values present in the
  minimized local input while retaining numeric, URL, empty-field, and length rejection.
- Appearance has one persisted root authority. The open Settings sheet writes that exact binding,
  so the selected scheme and the presented sheet update in the same transaction.
