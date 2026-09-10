# Dining integration boundary

Nytr's dining adapter is intentionally provider-neutral at its core. The
portfolio mirror includes no institutional dining HTML, menus, images, IDs, or
scraped page dumps. Parser tests use fabricated HTML in
`tests/fixtures/stacks/`.

An institutional provider can be integrated only after its terms, access method,
rate limits, retention, and attribution requirements are reviewed. Provider
availability is never treated as proof that an item was eaten.
