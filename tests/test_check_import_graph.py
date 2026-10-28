import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check_import_graph as cig  # noqa: E402


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_detects_sqlalchemy_in_domain(tmp_path: Path):
    _write(tmp_path / "products/clauseos/src/clauseos/domain/bad.py", "import sqlalchemy\n")
    violations = cig.check_domain_core_boundaries(repo_root=tmp_path)
    assert any("sqlalchemy" in v for v in violations)


def test_detects_sibling_product_import_in_core(tmp_path: Path):
    _write(tmp_path / "products/riskos/src/riskos/core/bad.py", "import collateralos\n")
    violations = cig.check_domain_core_boundaries(repo_root=tmp_path)
    assert any("collateralos" in v for v in violations)


def test_clean_domain_core_has_no_violations(tmp_path: Path):
    _write(
        tmp_path / "products/clauseos/src/clauseos/domain/good.py",
        "from dataclasses import dataclass\nfrom money_types import Money\n",
    )
    violations = cig.check_domain_core_boundaries(repo_root=tmp_path)
    assert violations == []


def test_detects_cassandre_control_importing_product_internals(tmp_path: Path):
    _write(
        tmp_path / "apps/cassandre_control_api/src/cassandre_control_api/bad.py",
        "import riskos\n",
    )
    violations = cig.check_cassandre_control_boundaries(repo_root=tmp_path)
    assert any("riskos" in v for v in violations)


def test_detects_settlement_transport_in_collateralos(tmp_path: Path):
    _write(tmp_path / "products/collateralos/src/collateralos/adapters/bad.py", "import paramiko\n")
    violations = cig.check_no_settlement_transport(repo_root=tmp_path)
    assert any("paramiko" in v for v in violations)


def test_real_repo_currently_clean_or_has_only_expected_gaps():
    repo_root = Path(__file__).resolve().parent.parent
    violations = (
        cig.check_domain_core_boundaries(repo_root=repo_root)
        + cig.check_cassandre_control_boundaries(repo_root=repo_root)
        + cig.check_no_settlement_transport(repo_root=repo_root)
    )
    assert violations == [], f"Import boundary violations in the real repo: {violations}"
