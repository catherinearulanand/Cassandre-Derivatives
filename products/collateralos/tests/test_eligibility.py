"""Eligibility compiler tests (plan Section 12.3, work item COL-005): every
position returns either ELIGIBLE or a complete ordered rejection set."""

from __future__ import annotations

import json
from pathlib import Path

from collateralos.domain.eligibility import compile_eligibility, compile_eligibility_matrix
from collateralos.domain.policy import build_policy_bundle_sim_001, load_wrong_way_map
from collateralos.domain.status import PolicyBundleStatus, RejectionReason

from fixtures.inventory_corpus import AS_OF, COUNTERPARTY_ID, build_twenty_position_corpus

_WRONG_WAY_MAP_PATH = Path(__file__).resolve().parents[3] / "docs" / "controls" / "wrong-way-map-001.json"


def _active_policy():
    raw = json.loads(_WRONG_WAY_MAP_PATH.read_text(encoding="utf-8"))
    return build_policy_bundle_sim_001(status=PolicyBundleStatus.ACTIVE, wrong_way_map=load_wrong_way_map(raw))


def test_eligible_cash_and_treasury_positions_pass():
    policy = _active_policy()
    positions = build_twenty_position_corpus()
    matrix = compile_eligibility_matrix(positions, policy=policy, counterparty_id=COUNTERPARTY_ID, as_of=AS_OF)
    for position_id in [f"SIM-POS-{i:04d}" for i in range(1, 11)]:
        assert matrix[position_id].eligible, (position_id, matrix[position_id].rejection_reasons)
        assert matrix[position_id].rejection_reasons == ()


def test_asset_class_not_eligible_rejected():
    policy = _active_policy()
    positions = {p.position_id: p for p in build_twenty_position_corpus()}
    result = compile_eligibility(positions["SIM-POS-0011"], policy=policy, counterparty_id=COUNTERPARTY_ID, as_of=AS_OF)
    assert not result.eligible
    assert result.rejection_reasons == (RejectionReason.ASSET_CLASS_NOT_ELIGIBLE,)


def test_wrong_way_excluded():
    policy = _active_policy()
    positions = {p.position_id: p for p in build_twenty_position_corpus()}
    result = compile_eligibility(positions["SIM-POS-0012"], policy=policy, counterparty_id=COUNTERPARTY_ID, as_of=AS_OF)
    assert not result.eligible
    assert RejectionReason.WRONG_WAY_EXCLUDED in result.rejection_reasons


def test_stale_price_rejected():
    policy = _active_policy()
    positions = {p.position_id: p for p in build_twenty_position_corpus()}
    result = compile_eligibility(positions["SIM-POS-0013"], policy=policy, counterparty_id=COUNTERPARTY_ID, as_of=AS_OF)
    assert not result.eligible
    assert result.rejection_reasons == (RejectionReason.STALE_PRICE,)


def test_encumbered_rejected():
    policy = _active_policy()
    positions = {p.position_id: p for p in build_twenty_position_corpus()}
    result = compile_eligibility(positions["SIM-POS-0014"], policy=policy, counterparty_id=COUNTERPARTY_ID, as_of=AS_OF)
    assert not result.eligible
    assert result.rejection_reasons == (RejectionReason.ENCUMBERED,)


def test_self_issued_rejected():
    policy = _active_policy()
    positions = {p.position_id: p for p in build_twenty_position_corpus()}
    result = compile_eligibility(positions["SIM-POS-0015"], policy=policy, counterparty_id=COUNTERPARTY_ID, as_of=AS_OF)
    assert not result.eligible
    assert result.rejection_reasons == (RejectionReason.SELF_ISSUED,)


def test_affiliate_issued_rejected():
    policy = _active_policy()
    positions = {p.position_id: p for p in build_twenty_position_corpus()}
    result = compile_eligibility(positions["SIM-POS-0016"], policy=policy, counterparty_id=COUNTERPARTY_ID, as_of=AS_OF)
    assert not result.eligible
    assert result.rejection_reasons == (RejectionReason.AFFILIATE_ISSUED,)


def test_recalled_rejected():
    policy = _active_policy()
    positions = {p.position_id: p for p in build_twenty_position_corpus()}
    result = compile_eligibility(positions["SIM-POS-0017"], policy=policy, counterparty_id=COUNTERPARTY_ID, as_of=AS_OF)
    assert not result.eligible
    assert result.rejection_reasons == (RejectionReason.RECALLED,)


def test_second_asset_class_trap_rejected():
    policy = _active_policy()
    positions = {p.position_id: p for p in build_twenty_position_corpus()}
    result = compile_eligibility(positions["SIM-POS-0018"], policy=policy, counterparty_id=COUNTERPARTY_ID, as_of=AS_OF)
    assert not result.eligible
    assert result.rejection_reasons == (RejectionReason.ASSET_CLASS_NOT_ELIGIBLE,)


def test_combined_reason_position_returns_complete_ordered_list():
    """Plan Section 12.3: "the complete ordered rejection set" -- not just
    the first matching reason."""
    policy = _active_policy()
    positions = {p.position_id: p for p in build_twenty_position_corpus()}
    result = compile_eligibility(positions["SIM-POS-0019"], policy=policy, counterparty_id=COUNTERPARTY_ID, as_of=AS_OF)
    assert not result.eligible
    assert result.rejection_reasons == (RejectionReason.ENCUMBERED, RejectionReason.STALE_PRICE)


def test_minimum_lot_trap_is_eligible_but_undeliverable():
    """Section 9.5's "minimum-lot-conflicting" position is ELIGIBLE under
    the static compiler (lot-size is an allocation-time constraint, not an
    eligibility-time one) but never enters the solver's decision space --
    see `test_planner.py::test_sub_minimum_lot_position_never_offered`."""
    policy = _active_policy()
    positions = {p.position_id: p for p in build_twenty_position_corpus()}
    result = compile_eligibility(positions["SIM-POS-0020"], policy=policy, counterparty_id=COUNTERPARTY_ID, as_of=AS_OF)
    assert result.eligible
    assert result.rejection_reasons == ()


def test_inactive_policy_rejects_everything():
    raw = json.loads(_WRONG_WAY_MAP_PATH.read_text(encoding="utf-8"))
    draft_policy = build_policy_bundle_sim_001(status=PolicyBundleStatus.DRAFT, wrong_way_map=load_wrong_way_map(raw))
    positions = {p.position_id: p for p in build_twenty_position_corpus()}
    result = compile_eligibility(positions["SIM-POS-0001"], policy=draft_policy, counterparty_id=COUNTERPARTY_ID, as_of=AS_OF)
    assert not result.eligible
    assert RejectionReason.POLICY_NOT_ACTIVE in result.rejection_reasons
