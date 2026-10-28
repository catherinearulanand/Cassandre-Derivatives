#!/usr/bin/env python3
"""Generate release-manifest.yaml from the actual build state, per ADR-015.

Every field is computed from something real (git, the lockfile, a live
pytest run) or emitted as null with an explicit "not_yet_implemented"
reason — never a hand-typed or fabricated value. Run from the repo root:

    uv run python scripts/generate_release_manifest.py
"""

from __future__ import annotations

import datetime as dt
import hashlib
import subprocess
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(cmd: list[str]) -> str | None:
    try:
        return subprocess.check_output(cmd, cwd=REPO_ROOT, stderr=subprocess.DEVNULL, text=True).strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def git_commit_and_dirty_status() -> dict:
    toplevel = _run(["git", "rev-parse", "--show-toplevel"])
    if toplevel is None:
        return {"commit": None, "dirty": None, "reason": "not_a_git_repository_or_git_unavailable"}
    if Path(toplevel).resolve() != REPO_ROOT.resolve():
        # This project directory is an untracked subdirectory of a much
        # larger repository (the user's home directory), not its own repo.
        # Reporting that outer repo's dirty status here would be
        # meaningless/misleading, so this is surfaced as an explicit gap
        # instead of a fabricated clean/dirty claim.
        return {
            "commit": None, "dirty": None,
            "reason": f"project_root_is_not_a_git_repo_toplevel (nearest repo toplevel: {toplevel}); "
                      "initialize a dedicated repository for this project before this field is meaningful",
        }
    commit = _run(["git", "rev-parse", "HEAD"])
    status = _run(["git", "status", "--porcelain"])
    return {
        "commit": commit,
        "dirty": bool(status) if status is not None else None,
        "reason": None if commit else "no_commits_yet",
    }


def lockfile_hash() -> dict:
    lock = REPO_ROOT / "uv.lock"
    if not lock.exists():
        return {"sha256": None, "reason": "uv.lock not found; run `uv sync` or `uv lock` first"}
    return {"sha256": hashlib.sha256(lock.read_bytes()).hexdigest(), "reason": None}


def run_pytest_and_collect_report() -> dict:
    import xml.etree.ElementTree as ET

    report_path = REPO_ROOT / ".release_manifest_junit.xml"
    cmd = [
        sys.executable, "-m", "pytest", str(REPO_ROOT),
        "-q", f"--junitxml={report_path}",
    ]
    try:
        completed = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    except FileNotFoundError:
        return {
            "passed": None, "failed": None, "skipped": None,
            "reason": "pytest could not be invoked",
        }

    if not report_path.exists():
        return {
            "passed": None, "failed": None, "skipped": None,
            "reason": "pytest --junitxml did not produce a report file",
        }

    root = ET.parse(report_path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    total = failed = errors = skipped = 0
    for suite in suites:
        total += int(suite.get("tests", 0))
        failed += int(suite.get("failures", 0))
        errors += int(suite.get("errors", 0))
        skipped += int(suite.get("skipped", 0))
    passed = total - failed - errors - skipped
    report_path.unlink(missing_ok=True)
    digest = hashlib.sha256(f"{passed}-{failed}-{errors}-{skipped}-{completed.stdout}".encode()).hexdigest()
    return {
        "passed": passed, "failed": failed + errors, "skipped": skipped,
        "report_digest": digest, "reason": None,
    }


def migration_heads() -> dict:
    heads = {}
    for product in ("clauseos", "riskos", "collateralos"):
        versions_dir = REPO_ROOT / "products" / product / "migrations" / "versions"
        if versions_dir.exists() and any(versions_dir.glob("*.py")):
            heads[product] = "present_not_parsed"
        else:
            heads[product] = None
    return heads


def build() -> dict:
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    return {
        "generated_at": now,
        "git": git_commit_and_dirty_status(),
        "dependency_lockfile": lockfile_hash(),
        "python": {
            "version": sys.version.split()[0],
            "declared_target": "3.13-supported (plan Section 5); running 3.12 venv this session, see docs/INTERPRETATION.md item 2.3",
        },
        "os_arch": {
            "platform": sys.platform,
        },
        "database_migration_heads": migration_heads(),
        "test_report": run_pytest_and_collect_report(),
        "image_digests": {"value": None, "reason": "not_yet_implemented: no container image build in this session"},
        "sbom_digest": {"value": None, "reason": "not_yet_implemented: pip-audit/syft wiring is backlog"},
        "solver": {
            "value": None,
            "reason": "not_yet_implemented: populated by CollateralOS's own release step once its adapter records which backend (highspy vs scipy HiGHS) was used",
        },
        "supported_products": ["clauseos", "riskos", "collateralos"],
        "federation_layer": "cassandre_control_api (optional)",
        "pilot_stage": "S1 controlled simulation initial slice only (plan Section 1.4) — NOT S1 acceptance-grade, NOT S2",
    }


def main() -> int:
    manifest = build()
    out_path = REPO_ROOT / "release-manifest.yaml"
    out_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
