"""Entry point that writes the full generated corpus (SIM-002 through
SIM-009) to real files on disk under `schemas/fixtures/`.

Run as a module from the repository root::

    uv run python -m simulation_factory.corpus.generate_all

or call `generate_all()` directly from another script. Both paths are
deterministic: the same seeds always produce byte-identical files (see
`packages/simulation_factory/tests/test_determinism.py`).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from simulation_factory.adversarial_index import build_adversarial_index
from simulation_factory.corpus.agreements import build_agreement_bundles
from simulation_factory.corpus.inventory import build_inventory_corpus
from simulation_factory.corpus.market import build_market_scenarios
from simulation_factory.corpus.trades import build_trade_corpus
from simulation_factory.corpus.users import build_static_data_corpus
from simulation_factory.independent_reference.trade_population_reference import (
    compute_source_control_manifest,
)
from simulation_factory.scenario_catalogue import SCENARIO_CATALOGUE
from simulation_factory.serialization import write_json_file, write_text_file

REPO_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "schemas" / "fixtures"


@dataclass(frozen=True)
class GenerationSummary:
    output_root: Path
    user_count: int
    custody_account_count: int
    ssi_count: int
    calendar_count: int
    agreement_bundle_count: int
    riskos_calculable_bundle_count: int
    trade_count: int
    market_snapshot_count: int
    inventory_position_count: int
    scenario_count: int
    adversarial_case_count: int


def _write_static_data(output_root: Path) -> tuple[int, int, int, int]:
    corpus = build_static_data_corpus()
    write_json_file(
        output_root / "users" / "static_data.json",
        {
            "users": corpus.users,
            "custody_accounts": corpus.custody_accounts,
            "ssis": corpus.ssis,
            "calendars": corpus.calendars,
        },
    )
    return len(corpus.users), len(corpus.custody_accounts), len(corpus.ssis), len(corpus.calendars)


def _write_agreements(output_root: Path) -> tuple[int, int]:
    bundles = build_agreement_bundles()
    index_entries = []
    for bundle in bundles:
        bundle_dir = output_root / "agreements" / bundle.bundle_id.lower()
        for filename, text in bundle.documents:
            write_text_file(bundle_dir / filename, text)
        write_json_file(
            bundle_dir / "ground_truth.json",
            {
                "bundle_id": bundle.bundle_id,
                "ground_truth": bundle.ground_truth,
                "ground_truth_content_hash": bundle.ground_truth_content_hash,
            },
        )
        index_entries.append(
            {
                "bundle_id": bundle.bundle_id,
                "title": bundle.title,
                "coverage_item": bundle.coverage_item,
                "family": bundle.family,
                "riskos_calculable": bundle.riskos_calculable,
                "documents": [name for name, _ in bundle.documents],
                "ground_truth_content_hash": bundle.ground_truth_content_hash,
                "provenance": bundle.provenance,
            }
        )
    write_json_file(output_root / "agreements" / "index.json", index_entries)
    return len(bundles), sum(1 for b in bundles if b.riskos_calculable)


def _write_trades(output_root: Path) -> int:
    corpus = build_trade_corpus()
    manifest = compute_source_control_manifest(
        corpus.trades,
        manifest_id="SIM-MANIFEST-TRADES-001",
        expected_missing_trade_ids=corpus.expected_missing_trade_ids,
    )
    write_json_file(
        output_root / "trades.json",
        {
            "seed": corpus.seed,
            "counterparties": corpus.counterparties,
            "netting_sets": corpus.netting_sets,
            "trades": corpus.trades,
            "source_control_manifest": manifest,
        },
    )
    return len(corpus.trades)


def _write_market(output_root: Path) -> int:
    snapshots = build_market_scenarios()
    index = []
    for snapshot in snapshots:
        write_json_file(output_root / "market_snapshots" / f"{snapshot.snapshot_id.lower()}.json", snapshot)
        index.append({"snapshot_id": snapshot.snapshot_id, "regime": snapshot.regime, "as_of": snapshot.as_of})
    write_json_file(output_root / "market_snapshots" / "index.json", index)
    return len(snapshots)


def _write_inventory(output_root: Path) -> int:
    positions = build_inventory_corpus()
    write_json_file(output_root / "inventory.json", positions)
    return len(positions)


def _write_scenario_catalogue(output_root: Path) -> int:
    write_json_file(output_root / "scenario_catalogue.json", SCENARIO_CATALOGUE)
    return len(SCENARIO_CATALOGUE)


def _write_adversarial_index(output_root: Path) -> int:
    index = build_adversarial_index(
        agreement_bundles=build_agreement_bundles(),
        trade_corpus=build_trade_corpus(),
        market_snapshots=build_market_scenarios(),
        inventory_positions=build_inventory_corpus(),
    )
    write_json_file(output_root / "adversarial_index.json", index.cases)
    return len(index.cases)


def generate_all(output_root: Path | None = None) -> GenerationSummary:
    root = Path(output_root) if output_root is not None else DEFAULT_OUTPUT_ROOT
    root.mkdir(parents=True, exist_ok=True)

    user_count, account_count, ssi_count, calendar_count = _write_static_data(root)
    bundle_count, calculable_count = _write_agreements(root)
    trade_count = _write_trades(root)
    market_count = _write_market(root)
    inventory_count = _write_inventory(root)
    scenario_count = _write_scenario_catalogue(root)
    adversarial_count = _write_adversarial_index(root)

    return GenerationSummary(
        output_root=root,
        user_count=user_count,
        custody_account_count=account_count,
        ssi_count=ssi_count,
        calendar_count=calendar_count,
        agreement_bundle_count=bundle_count,
        riskos_calculable_bundle_count=calculable_count,
        trade_count=trade_count,
        market_snapshot_count=market_count,
        inventory_position_count=inventory_count,
        scenario_count=scenario_count,
        adversarial_case_count=adversarial_count,
    )


def main() -> int:
    summary = generate_all()
    print(f"Wrote simulation factory corpus to {summary.output_root}")
    print(f"  users={summary.user_count} custody_accounts={summary.custody_account_count} "
          f"ssis={summary.ssi_count} calendars={summary.calendar_count}")
    print(f"  agreement_bundles={summary.agreement_bundle_count} "
          f"riskos_calculable={summary.riskos_calculable_bundle_count}")
    print(f"  trades={summary.trade_count}")
    print(f"  market_snapshots={summary.market_snapshot_count}")
    print(f"  inventory_positions={summary.inventory_position_count}")
    print(f"  scenarios={summary.scenario_count}")
    print(f"  adversarial_cases={summary.adversarial_case_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
