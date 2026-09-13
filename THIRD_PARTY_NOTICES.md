# Third-party notices

## Open Food Facts

Nytr may read exact product facts from Open Food Facts for barcode lookup. Nytr
does not own, certify, or guarantee that community-contributed data. Open Food
Facts database data is published under the Open Database License (ODbL), with
individual database contents under the Database Contents License (DbCL), and
may require attribution, share-alike, and notice obligations when data is
redistributed or publicly displayed.

Any deployment that stores or redistributes Open Food Facts data must review the
current Open Food Facts terms, provide the required attribution and source link,
preserve the applicable notices, and comply with ODbL/DbCL obligations. This
portfolio mirror contains no Open Food Facts dump.

Imported products keep their provider identity, product URL, fetch time, payload
digest, and `ODbL-1.0/DbCL-1.0` licence label so displayed values stay traceable
to the community source rather than presented as Nytr's own facts.

## Institutional dining data

Nytr can integrate with an institutional dining provider through a bounded,
fail-closed adapter. No institutional pages, menus, images, identifiers, or
scraped content are distributed in this repository, and ingestion is disabled by
default. Parser tests run against fabricated fixtures in `tests/fixtures/stacks/`
(see that directory's manifest). Any real integration must first satisfy the
provider's terms, access method, rate limits, retention rules, and attribution
requirements.

## Hevy

Training history can be imported from Hevy through the owner's own API
credentials. Hevy remains authoritative for the exercise and set detail it
returns. No Hevy account data, credentials, or exported history is included
here; the Hevy fixtures under `backend/tests/fixtures/hevy/` are fabricated.

## Apple platform frameworks

The iOS companion uses only Apple system frameworks: SwiftUI, HealthKit, Swift
Charts, VisionKit, CryptoKit, Observation, UserNotifications, and — where the
device supports it — Foundation Models. These are governed by Apple's SDK and
platform terms, are not redistributed here, and no Apple framework code is
vendored into this repository. "Apple Intelligence", "HealthKit", and related
marks belong to Apple Inc.

## Python dependencies

The backend depends on beautifulsoup4, lxml, httpx, FastAPI, uvicorn, and PyJWT,
plus pytest, mypy, and Ruff for development. Each retains its own upstream
licence; consult those projects before redistribution.

Nytr's own source in this repository is offered under the MIT terms in
[LICENSE](LICENSE). That grant covers Nytr's code only, not third-party data or
frameworks referenced above.
