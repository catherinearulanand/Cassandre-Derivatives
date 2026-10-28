"""End-to-end: `generate_all()` writes real files with the right shapes and
counts to disk (not just in-memory objects)."""

from __future__ import annotations

import json

from simulation_factory.corpus.generate_all import generate_all


def test_generate_all_writes_expected_files_and_counts(tmp_path):
    summary = generate_all(tmp_path)

    assert summary.user_count == 8
    assert summary.custody_account_count == 6
    assert summary.ssi_count == 6
    assert summary.calendar_count == 5
    assert summary.agreement_bundle_count == 12
    assert summary.riskos_calculable_bundle_count == 5
    assert summary.trade_count == 300
    assert summary.market_snapshot_count == 30
    assert summary.inventory_position_count == 600
    assert summary.scenario_count == 16
    assert summary.adversarial_case_count > 0

    assert (tmp_path / "users" / "static_data.json").exists()
    assert (tmp_path / "agreements" / "index.json").exists()
    assert (tmp_path / "trades.json").exists()
    assert (tmp_path / "market_snapshots" / "index.json").exists()
    assert (tmp_path / "inventory.json").exists()
    assert (tmp_path / "scenario_catalogue.json").exists()
    assert (tmp_path / "adversarial_index.json").exists()

    agreement_dirs = [p for p in (tmp_path / "agreements").iterdir() if p.is_dir()]
    assert len(agreement_dirs) == 12
    for d in agreement_dirs:
        assert (d / "ground_truth.json").exists()

    market_files = list((tmp_path / "market_snapshots").glob("sim-mkt-snap-*.json"))
    assert len(market_files) == 30


def test_generated_trades_json_has_no_json_floats(tmp_path):
    generate_all(tmp_path)
    text = (tmp_path / "trades.json").read_text(encoding="utf-8")
    payload = json.loads(text)
    assert isinstance(payload["trades"][0]["mtm"]["amount"], str)
    assert isinstance(payload["source_control_manifest"]["expected_accepted_mtm_total"]["amount"], str)


def test_generated_inventory_json_money_fields_are_strings(tmp_path):
    generate_all(tmp_path)
    payload = json.loads((tmp_path / "inventory.json").read_text(encoding="utf-8"))
    for position in payload[:20]:
        assert isinstance(position["market_value"]["amount"], str)
        assert isinstance(position["settled_quantity"]["amount"], str)


def test_every_generated_object_carries_is_simulated_true(tmp_path):
    generate_all(tmp_path)
    users_payload = json.loads((tmp_path / "users" / "static_data.json").read_text(encoding="utf-8"))
    for user in users_payload["users"]:
        assert user["provenance"]["is_simulated"] is True

    trades_payload = json.loads((tmp_path / "trades.json").read_text(encoding="utf-8"))
    for trade in trades_payload["trades"][:20]:
        assert trade["provenance"]["is_simulated"] is True
