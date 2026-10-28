"""Independent domain verifier tests (ADR-008, plan Section 12.5).

The key test here (`test_verifier_rejects_hand_built_concentration_violation`)
constructs a deliberately-wrong candidate allocation directly -- no solver
involved at all -- and asserts the verifier rejects it. This is what proves
the verifier is a real independent check rather than decoration around
whatever the solver already decided.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

from money_types import Money, Quantity

from collateralos.domain.plan import AllocationLine
from collateralos.domain.policy import build_policy_bundle_sim_001, load_wrong_way_map
from collateralos.domain.status import PolicyBundleStatus
from collateralos.domain.verifier import (
    recompute_adjusted_value,
    required_coverage_threshold,
    verify_candidate_allocation,
)

from fixtures.inventory_corpus import AS_OF, COUNTERPARTY_ID, build_twenty_position_corpus

_WRONG_WAY_MAP_PATH = Path(__file__).resolve().parents[3] / "docs" / "controls" / "wrong-way-map-001.json"


def _active_policy():
    raw = json.loads(_WRONG_WAY_MAP_PATH.read_text(encoding="utf-8"))
    return build_policy_bundle_sim_001(status=PolicyBundleStatus.ACTIVE, wrong_way_map=load_wrong_way_map(raw))


def _positions_by_id():
    return {p.position_id: p for p in build_twenty_position_corpus()}


def _reservable_now(positions_by_id):
    return {pid: p.quantity.amount for pid, p in positions_by_id.items()}


def _line_for(position, delivered_amount: Decimal, policy):
    haircut = policy.haircut_for(position.asset_class)
    adjusted = recompute_adjusted_value(position, delivered_amount, haircut)
    return AllocationLine(
        position_id=position.position_id,
        delivered_quantity=Quantity(amount=str(delivered_amount), unit=position.quantity.unit),
        adjusted_value=Money(amount=str(adjusted), currency=position.market_value.currency, rounding_context="test"),
    )


def test_verifier_accepts_a_correctly_built_candidate():
    policy = _active_policy()
    positions = _positions_by_id()
    obligation_amount = Money(amount="500000.00", currency="USD", rounding_context="obligation")
    cash = positions["SIM-POS-0001"]
    line = _line_for(cash, Decimal("500000"), policy)

    outcome = verify_candidate_allocation(
        obligation_amount=obligation_amount,
        obligation_currency="USD",
        counterparty_id=COUNTERPARTY_ID,
        candidate_lines=(line,),
        positions_by_id=positions,
        policy=policy,
        as_of=AS_OF,
        reservable_now=_reservable_now(positions),
        existing_held_by_issuer_group={},
        existing_held_total=Decimal("0"),
    )
    assert outcome.ok, outcome.violations


def test_verifier_rejects_hand_built_concentration_violation():
    """No solver call anywhere in this test. A candidate that delivers 100%
    of its value from one issuer group, deliberately built to blow through
    the 40% post-transfer concentration cap, must be rejected by the
    independent verifier on its own."""
    policy = _active_policy()
    positions = _positions_by_id()
    ust_position = positions["SIM-POS-0004"]  # issuer_group_id=SIM-ISSUER-GROUP-UST-SOVEREIGN

    obligation_amount = Money(amount="500000.00", currency="USD", rounding_context="obligation")
    # Deliver the obligation entirely from one UST issuer group -- 100%
    # concentration, far over the policy's 40% post-transfer cap.
    line = _line_for(ust_position, Decimal("500000"), policy)

    outcome = verify_candidate_allocation(
        obligation_amount=obligation_amount,
        obligation_currency="USD",
        counterparty_id=COUNTERPARTY_ID,
        candidate_lines=(line,),
        positions_by_id=positions,
        policy=policy,
        as_of=AS_OF,
        reservable_now=_reservable_now(positions),
        existing_held_by_issuer_group={},
        existing_held_total=Decimal("0"),
    )
    assert not outcome.ok
    assert any(v.startswith("CONCENTRATION_CAP_EXCEEDED:") for v in outcome.violations)


def test_verifier_rejects_ineligible_position_even_if_candidate_claims_it():
    policy = _active_policy()
    positions = _positions_by_id()
    wrong_way_position = positions["SIM-POS-0012"]
    line = _line_for(wrong_way_position, Decimal("200000"), policy)

    outcome = verify_candidate_allocation(
        obligation_amount=Money(amount="100000.00", currency="USD", rounding_context="obligation"),
        obligation_currency="USD",
        counterparty_id=COUNTERPARTY_ID,
        candidate_lines=(line,),
        positions_by_id=positions,
        policy=policy,
        as_of=AS_OF,
        reservable_now=_reservable_now(positions),
        existing_held_by_issuer_group={},
        existing_held_total=Decimal("0"),
    )
    assert not outcome.ok
    assert any(v.startswith("INELIGIBLE_POSITION:SIM-POS-0012") for v in outcome.violations)


def test_verifier_rejects_minimum_lot_violation():
    policy = _active_policy()
    positions = _positions_by_id()
    cash = positions["SIM-POS-0001"]
    # 1000 is the minimum lot for CASH_USD; 1500 is not a multiple of it.
    bad_line = AllocationLine(
        position_id=cash.position_id,
        delivered_quantity=Quantity(amount="1500", unit="CASH_USD"),
        adjusted_value=Money(amount="1500.00", currency="USD", rounding_context="test"),
    )
    outcome = verify_candidate_allocation(
        obligation_amount=Money(amount="1000.00", currency="USD", rounding_context="obligation"),
        obligation_currency="USD",
        counterparty_id=COUNTERPARTY_ID,
        candidate_lines=(bad_line,),
        positions_by_id=positions,
        policy=policy,
        as_of=AS_OF,
        reservable_now=_reservable_now(positions),
        existing_held_by_issuer_group={},
        existing_held_total=Decimal("0"),
    )
    assert not outcome.ok
    assert any(v.startswith("MINIMUM_LOT_VIOLATION:") for v in outcome.violations)


def test_verifier_rejects_exceeding_available_inventory():
    policy = _active_policy()
    positions = _positions_by_id()
    cash = positions["SIM-POS-0010"]  # only 50000 available
    line = _line_for(cash, Decimal("60000"), policy)
    outcome = verify_candidate_allocation(
        obligation_amount=Money(amount="10000.00", currency="USD", rounding_context="obligation"),
        obligation_currency="USD",
        counterparty_id=COUNTERPARTY_ID,
        candidate_lines=(line,),
        positions_by_id=positions,
        policy=policy,
        as_of=AS_OF,
        reservable_now={cash.position_id: Decimal("50000")},
        existing_held_by_issuer_group={},
        existing_held_total=Decimal("0"),
    )
    assert not outcome.ok
    assert any(v.startswith("EXCEEDS_AVAILABLE_INVENTORY:") for v in outcome.violations)


def test_verifier_rejects_insufficient_coverage():
    policy = _active_policy()
    positions = _positions_by_id()
    cash = positions["SIM-POS-0010"]
    line = _line_for(cash, Decimal("1000"), policy)
    outcome = verify_candidate_allocation(
        obligation_amount=Money(amount="500000.00", currency="USD", rounding_context="obligation"),
        obligation_currency="USD",
        counterparty_id=COUNTERPARTY_ID,
        candidate_lines=(line,),
        positions_by_id=positions,
        policy=policy,
        as_of=AS_OF,
        reservable_now=_reservable_now(positions),
        existing_held_by_issuer_group={},
        existing_held_total=Decimal("0"),
    )
    assert not outcome.ok
    assert any(v.startswith("INSUFFICIENT_COVERAGE:") for v in outcome.violations)


def test_required_coverage_threshold_is_conservative_round_up():
    threshold = required_coverage_threshold(Decimal("1000000.00"), Decimal("0.0001"))
    # 1,000,000 * (1 - 0.0001) = 999,900.00 exactly; still exercises ROUND_UP.
    assert threshold == Decimal("999900.00")


def test_verifier_rejects_overstated_candidate_value():
    policy = _active_policy()
    positions = _positions_by_id()
    cash = positions["SIM-POS-0001"]
    overstated_line = AllocationLine(
        position_id=cash.position_id,
        delivered_quantity=Quantity(amount="1000", unit="CASH_USD"),
        adjusted_value=Money(amount="999999.00", currency="USD", rounding_context="test"),  # wildly overstated
    )
    outcome = verify_candidate_allocation(
        obligation_amount=Money(amount="500.00", currency="USD", rounding_context="obligation"),
        obligation_currency="USD",
        counterparty_id=COUNTERPARTY_ID,
        candidate_lines=(overstated_line,),
        positions_by_id=positions,
        policy=policy,
        as_of=AS_OF,
        reservable_now=_reservable_now(positions),
        existing_held_by_issuer_group={},
        existing_held_total=Decimal("0"),
    )
    assert not outcome.ok
    assert any(v.startswith("STATED_VALUE_OVERSTATED:") for v in outcome.violations)
