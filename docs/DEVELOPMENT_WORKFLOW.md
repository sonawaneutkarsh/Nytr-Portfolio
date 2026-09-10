# Development Workflow

Nytr follows a small, reviewable task cycle:

1. Read the relevant design and safety notes.
2. Inspect existing domain and adapter boundaries.
3. Implement only the requested scope with typed, deterministic code.
4. Add regression tests for non-trivial behavior.
5. Run the focused test, lint, and type-check gates.
6. Review the complete diff and record important decisions.

External integrations are tested with synthetic fixtures. Live credentials,
scheduled ingestion, and owner data belong to a separately controlled deployment;
the public mirror never performs those actions automatically.
