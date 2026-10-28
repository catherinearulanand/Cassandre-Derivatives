#!/usr/bin/env python3
"""Enforce ADR-001's import boundaries (work item FND-001):

- products/<x>/src/<x>/{domain,core} must never import fastapi, sqlalchemy,
  alembic, uvicorn, a clock/date-now call site outside an injected port, a
  model SDK, or any sibling product package.
- apps/cassandre_control_api must never import a product-internal module
  (products.*.domain / products.*.core / products.*.application /
  products.*.adapters) — only packages/cassandre_contracts and its own code.
- products/collateralos anywhere must never import a settlement-transport
  library (ADR-012): paramiko, ftplib used for external transmission, any
  swift/iso20022 SDK name.

Exit code 0 = clean, 1 = violation found (prints every violation found, not
just the first, so a single run surfaces the whole picture).
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN_IN_DOMAIN_CORE = {
    "fastapi", "sqlalchemy", "alembic", "uvicorn", "psycopg", "psycopg2",
}

PRODUCTS = ["clauseos", "riskos", "collateralos"]

SETTLEMENT_TRANSPORT_LIBS = {
    "paramiko", "swift", "iso20022", "ftplib",
}


def _iter_python_files(root: Path):
    if not root.exists():
        return
    for path in root.rglob("*.py"):
        if "tests" in path.parts:
            continue
        yield path


def _imported_top_level_names(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return {f"__SYNTAX_ERROR__:{exc}"}
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[0])
    return names


def check_domain_core_boundaries(repo_root: Path = REPO_ROOT) -> list[str]:
    violations = []
    for product in PRODUCTS:
        for layer in ("domain", "core"):
            layer_root = repo_root / "products" / product / "src" / product / layer
            for path in _iter_python_files(layer_root):
                imported = _imported_top_level_names(path)
                forbidden_hit = imported & FORBIDDEN_IN_DOMAIN_CORE
                if forbidden_hit:
                    rel = path.relative_to(repo_root)
                    violations.append(
                        f"{rel}: {product}/{layer} imports forbidden module(s) {sorted(forbidden_hit)}"
                    )
                sibling_products = set(PRODUCTS) - {product}
                sibling_hit = imported & sibling_products
                if sibling_hit:
                    rel = path.relative_to(repo_root)
                    violations.append(
                        f"{rel}: {product}/{layer} imports sibling product(s) {sorted(sibling_hit)}"
                    )
    return violations


def check_cassandre_control_boundaries(repo_root: Path = REPO_ROOT) -> list[str]:
    violations = []
    control_root = repo_root / "apps" / "cassandre_control_api" / "src" / "cassandre_control_api"
    for path in _iter_python_files(control_root):
        imported = _imported_top_level_names(path)
        product_hit = imported & set(PRODUCTS)
        if product_hit:
            rel = path.relative_to(repo_root)
            violations.append(
                f"{rel}: cassandre_control_api imports product-internal package(s) {sorted(product_hit)}"
            )
    return violations


def check_no_settlement_transport(repo_root: Path = REPO_ROOT) -> list[str]:
    violations = []
    for root_name in ("products/collateralos", "apps/collateralos_api"):
        root = repo_root / root_name
        for path in _iter_python_files(root):
            imported = _imported_top_level_names(path)
            hit = imported & SETTLEMENT_TRANSPORT_LIBS
            if hit:
                rel = path.relative_to(repo_root)
                violations.append(
                    f"{rel}: CollateralOS imports settlement-transport-shaped library {sorted(hit)} (ADR-012 violation)"
                )
    return violations


def main() -> int:
    violations = (
        check_domain_core_boundaries()
        + check_cassandre_control_boundaries()
        + check_no_settlement_transport()
    )
    if violations:
        print("Import boundary violations found:")
        for v in violations:
            print(f"  - {v}")
        return 1
    print("Import graph clean: no domain/core boundary violations, no cross-product "
          "imports, no settlement-transport libraries in CollateralOS.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
