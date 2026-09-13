# Body & Goals completion milestone

## Authority model

- HealthKit is the only current-body-weight authority. Body & Goals reads the latest active HealthKit sample; it never accepts manual current weight.
- Height, date of birth, the Mifflin-St Jeor formula category, activity level, and optional target weight are explicit owner-entered profile evidence. Profile changes append a new version.
- Waist measurements are owner-entered, append-only evidence. A correction appends a replacement linked to the prior record; it never rewrites or deletes history.
- Goal direction remains immutable owner intent in `goal_policy_version`.
- Calorie and protein targets remain immutable, explicitly approved `target_policy_version` records.
- All assessments and proposals are recommendations. They cannot change goals, targets, HealthKit facts, food records, plans, or training history.

## Starting calorie estimate

Policy version: `mifflin-st-jeor-starting-target.v1`.

Required evidence is a fresh active HealthKit weight, height, date of birth, formula category (`male` or `female`, used only for the published equation), owner-selected activity level, and current goal direction. Waist and target weight are optional. Missing evidence is unavailable, never zero. Weight must be no more than seven local calendar days old.

The server calculates with `Decimal`:

`BMR = 10 × weight_kg + 6.25 × height_cm − 5 × age_years + coefficient`

The coefficient is `+5` for the male equation and `−161` for the female equation. Age is derived for the requested local date. The owner explicitly selects one versioned activity multiplier:

| Activity level | Multiplier |
| --- | ---: |
| Sedentary | 1.2 |
| Lightly active | 1.375 |
| Moderately active | 1.55 |
| Very active | 1.725 |

Nytr does not infer activity from one workout and never adds exercise calories back. Estimated maintenance is BMR multiplied by the selected activity factor. The conservative starting adjustment is `+200 kcal/day` for Gain, `0` for Maintain, and `−300 kcal/day` for Lose. The result is rounded half-up to the nearest `50 kcal`.

The proposal freezes the profile, goal, active HealthKit sample, equation inputs, maintenance estimate, adjustment, proposed target, policy version, and digest. It is clearly labeled as an estimate. Approval atomically appends the calorie target and a decision; rejection appends only the terminal decision. Generation alone has no effect. Once sufficient longitudinal evidence exists, Target Review is the preferred calibration path and never runs automatically.

## Waist trend and phase assessment

Policy versions: `owner-waist-trend.v1` and `owner-phase-assessment.v1`.

Waist values are stored canonically in centimeters while retaining the owner-entered value and unit. The read model uses latest-active correction leaves, groups same-local-day measurements by median, and computes an exact Theil-Sen weekly slope over 90 days. A ready trend needs at least three represented days spanning at least fourteen days and a latest value no more than fourteen days old. Otherwise it returns `no_data`, `insufficient`, or `stale`.

Phase assessment requires both a ready 28-day body-weight trend and a ready waist trend. It is recommendation-only:

- Gain is on track when weight is inside the configured goal band and waist changes no faster than `+0.25 cm/week`; excessive weight gain with waist above `+0.50 cm/week` suggests review.
- Lose is on track when weight is inside the configured goal band and waist is decreasing by at least `0.10 cm/week`; near-flat weight and waist suggest review.
- Maintain is on track when absolute weight drift is at most `0.10 kg/week` and absolute waist drift is at most `0.25 cm/week`; weight above `0.20 kg/week` or waist above `0.50 cm/week` suggests review.
- Mixed evidence returns observe; missing, stale, or insufficient evidence returns unavailable.

The assessment never estimates body-fat percentage, changes the goal, or changes a target.

## AI review privacy boundary

The Gemini Developer API adapter remains optional and downstream. Under the current hard zero-incremental-spend constraint, unpaid Gemini terms are not appropriate for personal health/nutrition evidence, so provider-backed review is disabled by default and requires an explicit production enable flag in addition to a server-only key. The disabled state is visible and deterministic Nytr remains fully functional.

If a future compliant configuration is explicitly approved, the adapter targets
`gemini-2.5-flash` through the `v1beta models.generateContent` REST endpoint using
`responseMimeType: application/json` and `responseJsonSchema`. Its provider
payload is categorical and contains no dates, timezone, exact targets, intake
totals, body measurements, age, record counts, raw HealthKit/Hevy/food data,
identity, or source identifiers. The full deterministic snapshot remains
server-to-iOS evidence and is not the third-party payload.
