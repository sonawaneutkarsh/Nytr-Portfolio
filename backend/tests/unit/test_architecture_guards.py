"""Architecture guard tests: domain isolation and no-delete persistence."""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "nutrition_agent"

ALLOWED_DOMAIN_ROOTS = {
    "nutrition_agent",
    "__future__",
    "collections",
    "dataclasses",
    "datetime",
    "decimal",
    "enum",
    "itertools",
    "json",
    "types",
    "uuid",
    "zoneinfo",
    "typing",
    "re",
    "html",
    "hashlib",
    "unicodedata",
}


def _iter_python_files(base: Path):
    yield from base.rglob("*.py")


def test_domain_layer_has_no_infrastructure_imports() -> None:
    domain = SRC / "domain"
    violations: list[str] = []
    for path in _iter_python_files(domain):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root not in ALLOWED_DOMAIN_ROOTS and not alias.name.startswith(
                        "nutrition_agent.domain"
                    ):
                        violations.append(f"{path.name}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                root = module.split(".")[0]
                if node.level > 0:
                    continue
                if root not in ALLOWED_DOMAIN_ROOTS and not module.startswith(
                    "nutrition_agent.domain"
                ):
                    violations.append(f"{path.name}: from {module}")
    assert violations == [], f"domain layer purity violated: {violations}"


def test_persistence_layer_contains_no_delete_statements() -> None:
    forbidden_markers = ("DELETE FROM", "delete(", ".delete", "session.delete")
    hits: list[str] = []
    for base in (SRC / "db", SRC / "application"):
        for path in _iter_python_files(base):
            text = path.read_text(encoding="utf-8")
            upper = text.upper()
            for marker in forbidden_markers:
                if marker.upper() in upper and marker != "delete(":
                    hits.append(f"{path.name}: contains '{marker}'")
            if ".delete(" in text:
                hits.append(f"{path.name}: contains '.delete('")
    assert hits == [], f"no-delete rule (ADR-013) violated: {hits}"


def test_authoritative_calculation_domains_contain_no_floats() -> None:
    """ADR-014/015/026/027: authoritative nutrition/health/review math is Decimal-only."""
    targets = [
        (SRC / "domain" / "nutrition", "domain/nutrition"),
        (SRC / "domain" / "planning", "domain/planning"),
        (SRC / "domain" / "health", "domain/health"),
        (SRC / "domain" / "training", "domain/training"),
        (SRC / "domain" / "progress.py", "domain/progress"),
        (SRC / "domain" / "target_review.py", "domain/target_review"),
        (SRC / "domain" / "stacks" / "facts_bridge.py", "facts_bridge"),
        (SRC / "domain" / "configurable_meals.py", "domain/configurable_meals"),
        (SRC / "domain" / "external_nutrition.py", "domain/external_nutrition"),
        (SRC / "domain" / "owner_meal_config.py", "domain/owner_meal_config"),
    ]
    violations: list[str] = []
    for path_like, label in targets:
        paths = [path_like] if path_like.is_file() else sorted(path_like.rglob("*.py"))
        for path in paths:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, float):
                    violations.append(f"{label}/{path.name}:{node.lineno} float literal")
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    module = getattr(node, "module", "") or ",".join(
                        alias.name for alias in node.names
                    )
                    if "numpy" in module or module == "math":
                        violations.append(f"{label}/{path.name}:{node.lineno} import {module}")
    assert violations == [], f"float ban violated in nutrition engine: {violations}"


def test_m12b_training_context_is_not_wired_into_planning_or_targets() -> None:
    """M12B may expose evidence but cannot change meals or approved calories."""

    targets = [
        SRC / "application" / "daily_plan.py",
        SRC / "application" / "server_inputs.py",
        SRC / "domain" / "planning" / "planner.py",
        SRC / "domain" / "planning" / "policy.py",
        SRC / "domain" / "nutrition" / "targets.py",
    ]
    violations: list[str] = []
    for path in targets:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            if any(module.startswith("nutrition_agent.domain.training") for module in modules):
                violations.append(f"{path.name}: imports training context")
    assert violations == []
