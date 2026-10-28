"""CollateralOS HTTP API (plan Section 25.5's slice; work item COL-014's
API half).

Thin FastAPI wrapper around `collateralos.application.store.CollateralOSStore`
-- every endpoint here is a direct translation of one store method into an
HTTP verb/path, with request/response bodies as plain JSON (never a
dataclass/pydantic-domain-type leaking directly into the wire format, so the
domain layer stays framework-free).

Persistence: one `CollateralOSStore` instance per app, injected as a FastAPI
dependency (`get_store`) so tests can spin up an isolated in-memory store per
test via `create_app()` without any Postgres/Docker dependency (Docker
Desktop is unavailable this session -- see `docs/adr/ADR-002...md`). A real
deployment replaces the in-memory store's `InMemoryInventoryRepository` /
in-process dicts with `collateralos.adapters` (SQLAlchemy + Postgres) behind
the same store interface, without changing anything in this module.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from audit_protocol import content_hash, generate_keypair
from collateralos.application.idempotency import IdempotencyConflictError, PreflightFailedError
from collateralos.application.security import AuthorizationDeniedError
from collateralos.application.store import CollateralOSStore, NotFoundError, PlanNotActionableError
from collateralos.domain.approval import PlanApproval, SelfApprovalError
from collateralos.domain.inventory import InventoryPosition, InventorySnapshot
from collateralos.domain.obligation import Obligation
from collateralos.domain.plan import AllocationLine, Plan, SolverEvidence
from collateralos.domain.policy import AssetClass
from collateralos.domain.reservation import ReservationConflictError
from collateralos.domain.settlement import SettlementPreflight
from collateralos.domain.status import ObligationDirection, ObligationStatus
from money_types import Money, Quantity

DEFAULT_ACTOR = "SIM-USER-API-0001"

# Plain HTML/CSS/vanilla-JS control UI, built and mounted per the platform's
# "no build step" approach for internal tooling. Resolved relative to this
# file (apps/collateralos_api/src/collateralos_api/main.py) rather than the
# process cwd, so `uvicorn collateralos_api.main:app` and pytest both find it
# regardless of where they are invoked from.
_WEB_STATIC_DIR = Path(__file__).resolve().parents[3] / "collateralos_web" / "static"


# --------------------------------------------------------------------------
# Request bodies (wire format only -- converted into domain types below)
# --------------------------------------------------------------------------


class MoneyIn(BaseModel):
    amount: str
    currency: str


class QuantityIn(BaseModel):
    amount: str
    unit: str


class ObligationIn(BaseModel):
    obligation_id: str
    counterparty_id: str
    entity_id: str
    agreement_id: str
    direction: str = "BANK_DELIVERS"
    amount: MoneyIn
    due_at: dt.datetime
    source: str
    version: str = "v1"
    status: str = "VALIDATED"
    actor_id: str = DEFAULT_ACTOR


class PolicyActionIn(BaseModel):
    actor_id: str = DEFAULT_ACTOR


class PositionIn(BaseModel):
    position_id: str
    owner_entity_id: str
    account_id: str
    location: str
    asset_class: str
    issuer_group_id: str | None = None
    currency: str
    market_value: MoneyIn
    quantity: QuantityIn
    price_as_of: dt.datetime
    stale_after: dt.datetime
    is_encumbered: bool = False
    is_recalled: bool = False
    is_self_issued: bool = False
    is_affiliate_issued: bool = False


class SnapshotIn(BaseModel):
    snapshot_id: str
    version: str
    as_of: dt.datetime
    positions: list[PositionIn]
    actor_id: str = DEFAULT_ACTOR


class PlanCreateIn(BaseModel):
    plan_id: str
    obligation_id: str
    policy_id: str
    snapshot_id: str
    as_of: dt.datetime
    existing_held_by_issuer_group: dict[str, str] = {}
    existing_held_total: str = "0"
    cost_rate_by_position: dict[str, str] | None = None
    actor_id: str = DEFAULT_ACTOR


class ReserveIn(BaseModel):
    reservation_id: str
    instruction_id: str
    ttl_minutes: int | None = None
    actor_id: str = DEFAULT_ACTOR


class MakerApproveIn(BaseModel):
    maker_id: str
    actor_id: str | None = None


class CheckerApproveIn(BaseModel):
    checker_id: str
    actor_id: str | None = None


class PreflightIn(BaseModel):
    actor_id: str = DEFAULT_ACTOR


class ExportIn(BaseModel):
    model_config = ConfigDict(extra="allow")

    idempotency_key: str
    actor_id: str = DEFAULT_ACTOR


# --------------------------------------------------------------------------
# Domain <-> wire conversions
# --------------------------------------------------------------------------


def _money_out(m: Money) -> dict[str, str]:
    return {"amount": str(m.amount), "currency": m.currency}


def _quantity_out(q: Quantity) -> dict[str, str]:
    return {"amount": str(q.amount), "unit": q.unit}


def _line_out(line: AllocationLine) -> dict[str, Any]:
    return {
        "position_id": line.position_id,
        "delivered_quantity": _quantity_out(line.delivered_quantity),
        "adjusted_value": _money_out(line.adjusted_value),
    }


def _evidence_out(evidence: SolverEvidence) -> dict[str, Any]:
    return {
        "method": evidence.method,
        "solver_build": evidence.solver_build,
        "status": evidence.status.value,
        "objective_value": evidence.objective_value,
        "gap_pct": evidence.gap_pct,
        "runtime_seconds": evidence.runtime_seconds,
        "time_limit_seconds": evidence.time_limit_seconds,
        "gap_tolerance_pct": evidence.gap_tolerance_pct,
        "verified": evidence.verified,
        "verifier_notes": list(evidence.verifier_notes),
    }


def _plan_out(plan: Plan) -> dict[str, Any]:
    return {
        "plan_id": plan.plan_id,
        "obligation_id": plan.obligation_id,
        "policy_id": plan.policy_id,
        "inventory_snapshot_id": plan.inventory_snapshot_id,
        "status": plan.status.value,
        "plan_hash": plan.plan_hash(),
        "lines": [_line_out(line) for line in plan.lines],
        "evidence": _evidence_out(plan.evidence),
    }


def _preflight_out(preflight: SettlementPreflight) -> dict[str, Any]:
    return {
        "preflight_id": preflight.preflight_id,
        "instruction_id": preflight.instruction_id,
        "performed_at": preflight.performed_at.isoformat(),
        "passed": preflight.passed,
        "failure_reasons": list(preflight.failure_reasons),
    }


def _approval_out(approval: PlanApproval) -> dict[str, Any]:
    return {
        "approval_id": approval.approval_id,
        "plan_id": approval.plan_id,
        "reservation_id": approval.reservation_id,
        "maker_id": approval.maker_id,
        "checker_id": approval.checker_id,
        "status": approval.status.value,
    }


def _position_from_wire(position: PositionIn) -> InventoryPosition:
    return InventoryPosition(
        position_id=position.position_id,
        owner_entity_id=position.owner_entity_id,
        account_id=position.account_id,
        location=position.location,
        asset_class=AssetClass(position.asset_class),
        issuer_group_id=position.issuer_group_id,
        currency=position.currency,
        market_value=Money(
            amount=position.market_value.amount, currency=position.market_value.currency, rounding_context="api_ingest"
        ),
        quantity=Quantity(amount=position.quantity.amount, unit=position.quantity.unit),
        price_as_of=position.price_as_of,
        stale_after=position.stale_after,
        is_encumbered=position.is_encumbered,
        is_recalled=position.is_recalled,
        is_self_issued=position.is_self_issued,
        is_affiliate_issued=position.is_affiliate_issued,
    )


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# --------------------------------------------------------------------------
# App factory + store dependency
# --------------------------------------------------------------------------


def create_app(store: CollateralOSStore | None = None) -> FastAPI:
    """Build a fresh app with its own in-memory `CollateralOSStore`.

    Tests call this directly to get full isolation between test cases (no
    shared mutable state, no Postgres/Docker required). The module-level
    `app` below is one instance of this for `uvicorn collateralos_api.main:app`.
    """
    app = FastAPI(title="CollateralOS API", version="0.1.0")
    app.state.store = store or CollateralOSStore(keypair=generate_keypair("collateralos-api-key-1"))

    def get_store(request: Request) -> CollateralOSStore:
        return request.app.state.store

    @app.exception_handler(AuthorizationDeniedError)
    async def _authorization_denied(request: Request, exc: AuthorizationDeniedError):
        # SEC-04: a DENY from cassandre_security's deny-by-default engine
        # actually blocks the request at the HTTP layer -- 403, not a
        # warning log and a 200.
        return _json_error(403, "AUTHORIZATION_DENIED", str(exc), extra={"reason_code": exc.reason_code.value})

    @app.exception_handler(NotFoundError)
    async def _not_found(request: Request, exc: NotFoundError):
        return _json_error(404, "NOT_FOUND", str(exc))

    @app.exception_handler(PlanNotActionableError)
    async def _plan_not_actionable(request: Request, exc: PlanNotActionableError):
        return _json_error(409, exc.status.value, str(exc))

    @app.exception_handler(ReservationConflictError)
    async def _reservation_conflict(request: Request, exc: ReservationConflictError):
        return _json_error(
            409, "RESERVATION_CONFLICT", str(exc),
            extra={"shortfalls": {k: str(v) for k, v in exc.shortfalls.items()}},
        )

    @app.exception_handler(SelfApprovalError)
    async def _self_approval(request: Request, exc: SelfApprovalError):
        return _json_error(422, "SELF_APPROVAL_REJECTED", str(exc))

    @app.exception_handler(IdempotencyConflictError)
    async def _idempotency_conflict(request: Request, exc: IdempotencyConflictError):
        return _json_error(409, "IDEMPOTENCY_KEY_CONFLICT", str(exc))

    @app.exception_handler(PreflightFailedError)
    async def _preflight_failed(request: Request, exc: PreflightFailedError):
        return _json_error(422, "PREFLIGHT_FAILED", str(exc), extra={"reasons": list(exc.reasons)})

    @app.exception_handler(ValueError)
    async def _value_error(request: Request, exc: ValueError):
        message = str(exc)
        code = "APPROVAL_INVALIDATED" if message.startswith("APPROVAL_INVALIDATED") else "BAD_REQUEST"
        status_code = 409 if code == "APPROVAL_INVALIDATED" else 400
        return _json_error(status_code, code, message)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    # --- COL-001: obligation ingest ---
    @app.post("/v1/obligations", status_code=201)
    def create_obligation(body: ObligationIn, store: CollateralOSStore = Depends(get_store)) -> dict[str, Any]:
        obligation = Obligation(
            obligation_id=body.obligation_id,
            counterparty_id=body.counterparty_id,
            entity_id=body.entity_id,
            agreement_id=body.agreement_id,
            direction=ObligationDirection(body.direction),
            amount=Money(amount=body.amount.amount, currency=body.amount.currency, rounding_context="api_ingest"),
            due_at=body.due_at,
            source=body.source,
            version=body.version,
            status=ObligationStatus(body.status),
        )
        store.add_obligation(obligation, actor_id=body.actor_id, now=_now())
        return {
            "obligation_id": obligation.obligation_id,
            "status": obligation.status.value,
            "version": obligation.version,
        }

    # --- COL-004: policy bundle lifecycle ---
    @app.post("/v1/policy-bundles:validate", status_code=201)
    def validate_policy_bundle(
        body: PolicyActionIn = PolicyActionIn(), store: CollateralOSStore = Depends(get_store)
    ) -> dict[str, Any]:
        draft = store.add_draft_policy()
        validated = store.validate_policy(draft.policy_id, actor_id=body.actor_id, now=_now())
        return {"policy_id": validated.policy_id, "version": validated.version, "status": validated.status.value}

    @app.post("/v1/policy-bundles/{policy_id}:activate")
    def activate_policy_bundle(
        policy_id: str, body: PolicyActionIn = PolicyActionIn(), store: CollateralOSStore = Depends(get_store)
    ) -> dict[str, Any]:
        activated = store.activate_policy(policy_id, actor_id=body.actor_id, now=_now())
        return {"policy_id": activated.policy_id, "version": activated.version, "status": activated.status.value}

    # --- COL-002: inventory snapshot ingest ---
    @app.post("/v1/inventory-snapshots", status_code=201)
    def create_inventory_snapshot(body: SnapshotIn, store: CollateralOSStore = Depends(get_store)) -> dict[str, Any]:
        positions = tuple(_position_from_wire(p) for p in body.positions)
        snapshot = InventorySnapshot(
            snapshot_id=body.snapshot_id, version=body.version, as_of=body.as_of, positions=positions
        )
        store.add_snapshot(snapshot, actor_id=body.actor_id, now=_now())
        return {"snapshot_id": snapshot.snapshot_id, "version": snapshot.version, "position_count": len(positions)}

    # --- COL-006/007/008/009: planning ---
    @app.post("/v1/plans", status_code=201)
    def create_plan(body: PlanCreateIn, store: CollateralOSStore = Depends(get_store)) -> dict[str, Any]:
        existing_held_by_issuer_group = {k: Decimal(v) for k, v in body.existing_held_by_issuer_group.items()}
        cost_rate_by_position = (
            {k: Decimal(v) for k, v in body.cost_rate_by_position.items()}
            if body.cost_rate_by_position is not None
            else None
        )
        plan = store.create_plan(
            plan_id=body.plan_id,
            obligation_id=body.obligation_id,
            policy_id=body.policy_id,
            snapshot_id=body.snapshot_id,
            existing_held_by_issuer_group=existing_held_by_issuer_group,
            existing_held_total=Decimal(body.existing_held_total),
            cost_rate_by_position=cost_rate_by_position,
            as_of=body.as_of,
            actor_id=body.actor_id,
            now=_now(),
        )
        return _plan_out(plan)

    @app.get("/v1/plans/{plan_id}")
    def get_plan(plan_id: str, store: CollateralOSStore = Depends(get_store)) -> dict[str, Any]:
        return _plan_out(store.get_plan(plan_id))

    @app.get("/v1/plans/{plan_id}/evidence")
    def get_plan_evidence(plan_id: str, store: CollateralOSStore = Depends(get_store)) -> dict[str, Any]:
        return _evidence_out(store.get_plan(plan_id).evidence)

    # --- COL-010: reservation ---
    @app.post("/v1/plans/{plan_id}:reserve", status_code=201)
    def reserve_plan(plan_id: str, body: ReserveIn, store: CollateralOSStore = Depends(get_store)) -> dict[str, Any]:
        ttl = dt.timedelta(minutes=body.ttl_minutes) if body.ttl_minutes is not None else dt.timedelta(minutes=15)
        view, instruction = store.reserve_plan(
            plan_id,
            reservation_id=body.reservation_id,
            instruction_id=body.instruction_id,
            actor_id=body.actor_id,
            now=_now(),
            ttl=ttl,
        )
        return {
            "reservation_id": view.reservation_id,
            "plan_id": view.plan_id,
            "status": view.status.value,
            "expires_at": view.expires_at.isoformat(),
            "lines": {k: str(v) for k, v in view.lines.items()},
            "instruction_id": instruction.instruction_id,
            "instruction_hash": instruction.instruction_hash(),
        }

    # --- COL-011: maker/checker approval ---
    @app.post("/v1/reservations/{reservation_id}:maker-approve")
    def maker_approve(
        reservation_id: str, body: MakerApproveIn, store: CollateralOSStore = Depends(get_store)
    ) -> dict[str, Any]:
        approval = store.maker_approve(
            reservation_id, maker_id=body.maker_id, actor_id=body.actor_id or body.maker_id, now=_now()
        )
        return _approval_out(approval)

    @app.post("/v1/reservations/{reservation_id}:checker-approve")
    def checker_approve(
        reservation_id: str, body: CheckerApproveIn, store: CollateralOSStore = Depends(get_store)
    ) -> dict[str, Any]:
        approval = store.checker_approve(
            reservation_id, checker_id=body.checker_id, actor_id=body.actor_id or body.checker_id, now=_now()
        )
        return _approval_out(approval)

    # --- COL-012: preflight ---
    @app.post("/v1/instructions/{instruction_id}:preflight")
    def preflight(
        instruction_id: str, body: PreflightIn = PreflightIn(), store: CollateralOSStore = Depends(get_store)
    ) -> dict[str, Any]:
        return _preflight_out(store.run_preflight(instruction_id, actor_id=body.actor_id, now=_now()))

    # --- COL-013: idempotent internal export ---
    @app.post("/v1/instructions/{instruction_id}:export-internal")
    def export_internal(
        instruction_id: str, body: ExportIn, store: CollateralOSStore = Depends(get_store)
    ) -> dict[str, Any]:
        fingerprint_fields = {k: v for k, v in body.model_dump(mode="json").items() if k != "idempotency_key"}
        request_fingerprint = content_hash(fingerprint_fields)
        envelope = store.export_internal(
            instruction_id,
            idempotency_key=body.idempotency_key,
            request_fingerprint=request_fingerprint,
            actor_id=body.actor_id,
            now=_now(),
        )
        return envelope.model_dump(mode="json")

    # Static control-UI mount -- registered last so it never shadows an API
    # route above (Starlette matches routes in registration order; the
    # explicit @app.get/@app.post routes above always win on exact-path
    # matches, and this catch-all Mount only serves paths none of them claim,
    # e.g. "/", "/app.js", "/style.css"). Skipped gracefully (rather than
    # raising) if the static directory doesn't exist, so this module still
    # imports cleanly in any environment where apps/collateralos_web hasn't
    # been checked out.
    if _WEB_STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=_WEB_STATIC_DIR, html=True), name="web-ui")

    return app


def _json_error(status_code: int, code: str, message: str, *, extra: dict[str, Any] | None = None):
    from fastapi.responses import JSONResponse

    body = {"error": code, "detail": message}
    if extra:
        body.update(extra)
    return JSONResponse(status_code=status_code, content=body)


# Default app instance for `uvicorn collateralos_api.main:app`.
app = create_app()
