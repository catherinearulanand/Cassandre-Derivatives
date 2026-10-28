"""Maker/checker approval-binding tests (plan Section 12.4 step 10, Section
13; work item COL-011).

Two independent levels are exercised:

* Pure domain level (`PlanApproval`/`ApprovalBindingInputs` directly): fast,
  no store/plan/reservation wiring, isolates the binding-hash comparison
  itself.
* Store level (`CollateralOSStore`): a fully wired obligation -> policy ->
  snapshot -> plan -> reservation, then a maker approval, then a simulated
  plan change, proving the *actual* production code path
  (`CollateralOSStore.checker_approve`) fails closed rather than just the
  domain object in isolation.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from collateralos.domain.approval import ApprovalBindingInputs, PlanApproval, SelfApprovalError
from collateralos.domain.status import ApprovalStatus, SolverStatus

from fixtures.inventory_corpus import AS_OF
from fixtures.store_scenario import build_reserved_plan_scenario

MAKER_ID = "SIM-USER-MAKER-0001"
CHECKER_ID = "SIM-USER-CHECKER-0001"


def _binding_inputs(**overrides) -> ApprovalBindingInputs:
    fields = dict(
        obligation_id="SIM-OBL-0001",
        obligation_version="v1",
        plan_id="SIM-PLAN-0001",
        plan_hash="plan-hash-v1",
        reservation_id="SIM-RES-0001",
        reservation_version=1,
        policy_id="SIM-POLICY-001",
        policy_version="1.0.0",
        inventory_snapshot_id="SIM-SNAP-0001",
        inventory_snapshot_version="v1",
        instruction_id="SIM-INSTR-0001",
        instruction_hash="instruction-hash-v1",
    )
    fields.update(overrides)
    return ApprovalBindingInputs(**fields)


# --- pure domain level ---------------------------------------------------


def test_domain_checker_approve_succeeds_when_nothing_changed():
    approval = PlanApproval(approval_id="SIM-APPR-0001", plan_id="SIM-PLAN-0001", reservation_id="SIM-RES-0001")
    inputs = _binding_inputs()

    approval.maker_approve(MAKER_ID, inputs)
    assert approval.status is ApprovalStatus.PENDING_CHECKER

    approval.checker_approve(CHECKER_ID, inputs)
    assert approval.status is ApprovalStatus.APPROVED
    assert approval.maker_id == MAKER_ID
    assert approval.checker_id == CHECKER_ID


def test_domain_checker_approve_fails_closed_when_binding_hash_drifts():
    """If any bound field changes between maker and checker approval, the
    recomputed binding hash no longer matches what the maker signed off on
    -- checker approval must reject, not silently proceed."""
    approval = PlanApproval(approval_id="SIM-APPR-0002", plan_id="SIM-PLAN-0001", reservation_id="SIM-RES-0001")
    inputs_at_maker_time = _binding_inputs(plan_hash="plan-hash-v1")
    approval.maker_approve(MAKER_ID, inputs_at_maker_time)
    binding_hash_maker_saw = approval.binding_hash

    inputs_at_checker_time = _binding_inputs(plan_hash="plan-hash-v2")  # plan changed underneath the approval
    with pytest.raises(ValueError, match="APPROVAL_INVALIDATED"):
        approval.checker_approve(CHECKER_ID, inputs_at_checker_time)

    assert approval.status is ApprovalStatus.APPROVAL_INVALIDATED
    assert approval.checker_id is None  # never recorded -- approval did not silently proceed
    assert approval.binding_hash == binding_hash_maker_saw  # untouched; not overwritten with the new hash


def test_domain_checker_approve_rejects_self_approval():
    approval = PlanApproval(approval_id="SIM-APPR-0003", plan_id="SIM-PLAN-0001", reservation_id="SIM-RES-0001")
    inputs = _binding_inputs()
    approval.maker_approve(MAKER_ID, inputs)

    with pytest.raises(SelfApprovalError):
        approval.checker_approve(MAKER_ID, inputs)  # same identity as maker

    # Self-approval is rejected before any hash comparison/mutation, so the
    # approval is left pending a (distinct) checker rather than invalidated.
    assert approval.status is ApprovalStatus.PENDING_CHECKER
    assert approval.checker_id is None


# --- store level (real production code path) -----------------------------


def test_store_checker_approve_fails_when_plan_changes_after_maker_approval():
    scenario = build_reserved_plan_scenario(seq="0401")
    store = scenario.store

    store.maker_approve(scenario.reservation_id, maker_id=MAKER_ID, actor_id=MAKER_ID, now=AS_OF)
    approval = store.approvals[scenario.reservation_id]
    assert approval.status is ApprovalStatus.PENDING_CHECKER
    binding_hash_maker_saw = approval.binding_hash

    # Simulate the plan changing underneath the approval (e.g. a re-plan)
    # between maker and checker approval: mutate the stored Plan so its
    # plan_hash() -- and therefore the recomputed binding hash -- differs.
    plan = store.get_plan(scenario.plan_id)
    new_status = (
        SolverStatus.FEASIBLE_BOUNDED_GAP
        if plan.status is not SolverStatus.FEASIBLE_BOUNDED_GAP
        else SolverStatus.FEASIBLE_OPTIMAL
    )
    store.plans[scenario.plan_id] = replace(plan, status=new_status)
    assert store.plans[scenario.plan_id].plan_hash() != plan.plan_hash()

    with pytest.raises(ValueError, match="APPROVAL_INVALIDATED"):
        store.checker_approve(scenario.reservation_id, checker_id=CHECKER_ID, actor_id=CHECKER_ID, now=AS_OF)

    assert approval.status is ApprovalStatus.APPROVAL_INVALIDATED
    assert approval.checker_id is None
    assert approval.binding_hash == binding_hash_maker_saw
    # The invalidation is itself audited, not silently swallowed.
    assert any(e.action == "PLAN_APPROVAL_INVALIDATED" for e in store.audit_events)


def test_store_checker_approve_rejects_maker_equals_checker():
    scenario = build_reserved_plan_scenario(seq="0402")
    store = scenario.store

    store.maker_approve(scenario.reservation_id, maker_id=MAKER_ID, actor_id=MAKER_ID, now=AS_OF)
    with pytest.raises(SelfApprovalError):
        store.checker_approve(scenario.reservation_id, checker_id=MAKER_ID, actor_id=MAKER_ID, now=AS_OF)

    assert store.approvals[scenario.reservation_id].status is ApprovalStatus.PENDING_CHECKER


def test_store_checker_approve_succeeds_when_nothing_changed():
    scenario = build_reserved_plan_scenario(seq="0403")
    store = scenario.store

    store.maker_approve(scenario.reservation_id, maker_id=MAKER_ID, actor_id=MAKER_ID, now=AS_OF)
    approval = store.checker_approve(scenario.reservation_id, checker_id=CHECKER_ID, actor_id=CHECKER_ID, now=AS_OF)

    assert approval.status is ApprovalStatus.APPROVED
    assert approval.maker_id == MAKER_ID
    assert approval.checker_id == CHECKER_ID
