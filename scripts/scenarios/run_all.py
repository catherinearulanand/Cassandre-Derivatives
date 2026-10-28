"""Runs all 10 `scripts/scenarios/scenario_*.py` simulation scenarios
against the live ClauseOS/RiskOS/CollateralOS services and prints a
summary table. Each scenario is run as its own subprocess (so one
scenario's crash can't take down the rest), capturing full stdout for
inspection on failure.

Usage: `uv run python scripts/scenarios/run_all.py` from the repo root.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

SCENARIOS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCENARIOS_DIR.parents[1]

SCENARIO_FILES = [
    "scenario_001_quiet_no_call.py",
    "scenario_002_delivery_by_bank.py",
    "scenario_005_ratings_event_unknown.py",
    "scenario_006_agreement_conflict.py",
    "scenario_007_unsupported_option_trade.py",
    "scenario_008_stale_market_inventory.py",
    "scenario_009_wrong_way_cheapest_asset.py",
    "scenario_011_infeasible_inventory.py",
    "scenario_013_approval_after_change.py",
    "scenario_015_duplicate_export.py",
]


def main() -> int:
    results = []
    for filename in SCENARIO_FILES:
        path = SCENARIOS_DIR / filename
        print(f"\n{'#' * 78}\n# RUNNING {filename}\n{'#' * 78}")
        proc = subprocess.run([sys.executable, str(path)], cwd=REPO_ROOT, capture_output=True, text=True, timeout=120)
        print(proc.stdout)
        if proc.stderr.strip():
            print("--- stderr ---")
            print(proc.stderr)

        passed = proc.returncode == 0
        scenario_id = "?"
        name = filename
        reason = ""
        for line in proc.stdout.splitlines():
            # Parsed with a regex rather than naive whitespace-splitting --
            # scenario names contain spaces ("Quiet/no call"), which broke
            # a naive `line.split(" ")` into the wrong fields. Found and
            # fixed during this session's own real run.
            match = re.match(r"scenario_id=(\S+) name='(.*)'$", line)
            if match:
                scenario_id, name = match.group(1), match.group(2)
            if "RESULT: FAIL" in line:
                reason = line.split("RESULT: FAIL -- ", 1)[-1] if "RESULT: FAIL -- " in line else "see output above"
        results.append((scenario_id, name, passed, reason))

    print(f"\n\n{'=' * 78}\nSUMMARY\n{'=' * 78}")
    print(f"{'Scenario ID':<22}{'Name':<28}{'Result':<8}Reason")
    print("-" * 100)
    for scenario_id, name, passed, reason in results:
        print(f"{scenario_id:<22}{name:<28}{'PASS' if passed else 'FAIL':<8}{reason}")

    total = len(results)
    passed_count = sum(1 for _, _, p, _ in results if p)
    print("-" * 78)
    print(f"{passed_count}/{total} passed")

    return 0 if passed_count == total else 1


if __name__ == "__main__":
    sys.exit(main())
