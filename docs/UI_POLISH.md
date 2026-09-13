# Nytr UI polish — 2026-09-12

Base: `51ed945849542fbce4a4da12a6e615f3b047b293`.
Scope: presentation of existing state only; no backend, schema, API, nutrition,
training, provider, or notification changes. Audit and validation used terminal
capabilities only. No live backend calls or GUI/browser automation were used.

## Read-only audit findings

The SwiftUI audit covered Today, Next Meal, Body & Goals, Progress, Training and
coaching, manual/barcode entry, nutrition history, Nytr Review, authentication,
Health Sync, Settings, and shared target-review controls, including their loading,
empty, unavailable, and error branches.

Highest-value issues reported before editing:

- Today began with secondary navigation instead of calorie/protein status.
- Inset list rows contained additional padded gray panels, reducing usable width.
- Active targets, starting estimates, and proposals needed clearer separation.
- Long metric values and horizontally competing workout metadata risked cramped
  layouts at larger text sizes.
- Numeric fields lost visible context after entry because labels were placeholders.
- Privacy-disabled AI review used an orange failure warning and technical copy.
- Policy identifiers and engineering explanations competed with actionable content.
- Training facts and coaching needed stronger source and purpose labels.

## Design and implementation

Native list sections, semantic colors, SF Symbols, and system text styles remain
primary. No custom theme or design-system framework was introduced.

- Today leads with nutrition, then Next Meal, food logging, and grouped progress
  links. Prominent recorded totals retain partial/estimated warnings. Redundant
  panel backgrounds were removed, and recommendation metadata has a disclosure.
- Body & Goals separates current body evidence, approved targets, goal/review,
  profile, starting estimate, waist, and phase assessment. Pending calorie
  proposals explicitly say they are estimated and inactive until approved.
- Progress presents the existing waist and phase response alongside weight and
  nutrition evidence. It uses the existing BodyGoalsViewModel and read endpoint;
  refresh remains explicit. Non-ready waist evidence does not display a trend rate.
- Training labels Hevy records separately from Nytr next-session coaching; dense
  workout metadata stacks vertically. Source explanations remain available.
- Review presents privacy as an intentional neutral lock/shield state, with no
  retry for the privacy-disabled response. The existing explicit evidence request
  remains; providers were not enabled and no background request was introduced.
- Food entry retains barcode review/import and separate consumption confirmation.
  Sign-in uses an email keyboard, no automatic capitalization, and clearer copy.

Shared presentation components in `Support/NytrUI.swift` are a wrapping metric row,
a text-and-symbol status label, a persistently labeled number field, and shared
waist/phase summaries. `BodyEvidenceCopy` translates server reason codes without
calculating or reclassifying evidence; raw phase details remain in a disclosure.
Unknown reasons never become an affirmative assessment.

## Accessibility and verification

Metric rows stack at accessibility Dynamic Type sizes and fall back to wrapping
when horizontal space is insufficient. VoiceOver receives explicit metric labels
and values. Numeric fields keep their visible labels and have 44-point minimum
field heights; primary actions use large native controls. Statuses include words
and symbols rather than relying only on color. New surfaces use semantic colors
for system appearance adaptation.

Validation:

- Swift production harness: whole-module typecheck and module emission.
- Swift test harness: all test sources typechecked against the production module.
- Full iOS simulator build and XCTest: 209 tests passed, zero failures.
- Regression coverage verifies privacy-disabled presentation vs retryable failures,
  tentative assessment wording, and unknown reason-code handling.
- `swift-format` format and strict lint passed on changed Swift files, with
  four-space indentation and a 120-column line length.
- `git diff --check` passed.

Existing warnings remain in unchanged `ViewModelTests.swift` (unused mutation),
`BodyGoalsViewModelTests.swift` (Swift 6 actor-isolation migration), and Xcode's
App Intents metadata extraction. No new warning from the edited UI was observed.

## Device review still required

No visual or assistive-technology runtime validation is claimed. Before the demo,
review on a small iPhone and the owner's device:

- Light/dark appearance, increased contrast, and all accessibility text sizes.
- VoiceOver order and metric announcements; actual tap targets and keyboard dismissal.
- Today with complete, partial, unavailable, and estimated nutrition.
- Starting estimate with no target, pending proposal, rejection, and active target;
  protein approval and rejection retain their existing confirmation flow.
- Sparse/stale weight and waist evidence; coaching clearly separate from history.
- Barcode camera/fallback, review/import, manual entry, and consumption confirmation.
- Privacy-disabled Review, sign-in, loading, offline, empty, and retry states.

Capture screenshots during that review. This pass is code-complete and suitable
for demo/device review; final visual demo readiness requires seeing the app on
hardware. Health Sync, Settings, scanner implementation, underlying calculations,
all data/approval boundaries, and public repository remain unchanged. The protected
`Config.local.xcconfig.save` remains untouched, unstaged, and untracked.

## Subsequent product pass

The owner requested a more substantial redesign and functional repairs after this
presentation-only pass. Those additive changes build on its commit and are documented
in `PRODUCT_QUALITY.md`; the descriptions and validation above remain the historical
record of the original pass.
