# Synthetic parser fixtures

Every HTML/TSV fixture in this directory is fabricated for tests. It is not a
copy, reconstruction, or redistribution of an institutional dining page.
Names, dates, identifiers, portions, and nutrition values are intentionally
fictional. The fixtures exercise parser states (complete, placeholder,
incomplete, and empty) without bundling source-provider content.

Nytr can integrate with an institutional dining provider through the bounded
adapter in `backend/src/nutrition_agent/infrastructure/stacks_source/`, but
public builds do not include institutional source pages or credentials.
