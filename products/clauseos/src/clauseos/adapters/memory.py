"""In-memory repository implementing `clauseos.application.ports.BundleRepository`.

Used by domain-level tests and by the FastAPI app's tests (no Postgres
required). The SQLAlchemy-backed adapter in `clauseos.adapters.db` implements
the identical interface so the application/API layer is storage-agnostic.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from audit_protocol.chain import AuditEvent, append_event
from clauseos.domain.assertion import ContractAssertion
from clauseos.domain.bundle import AgreementBundle
from clauseos.domain.policy import PolicyRelease
from clauseos.domain.review import ReviewCase

PRODUCT_NAME = "clauseos"


class InMemoryBundleRepository:
    def __init__(self) -> None:
        self._bundles: dict[str, AgreementBundle] = {}
        self._assertions: dict[str, ContractAssertion] = {}
        self._review_cases: dict[str, ReviewCase] = {}
        self._policy_releases: dict[str, PolicyRelease] = {}
        self._audit_events: dict[str, list[AuditEvent]] = {}

    def save_bundle(self, bundle: AgreementBundle) -> None:
        self._bundles[bundle.bundle_id] = bundle

    def get_bundle(self, bundle_id: str) -> AgreementBundle | None:
        return self._bundles.get(bundle_id)

    def list_bundles(self) -> list[AgreementBundle]:
        return list(self._bundles.values())

    def save_assertion(self, assertion: ContractAssertion) -> None:
        self._assertions[assertion.assertion_id] = assertion

    def get_assertion(self, assertion_id: str) -> ContractAssertion | None:
        return self._assertions.get(assertion_id)

    def list_assertions(
        self, *, bundle_id: str | None = None, concept: str | None = None, status: str | None = None
    ) -> list[ContractAssertion]:
        results = list(self._assertions.values())
        if bundle_id is not None:
            results = [a for a in results if a.bundle_id == bundle_id]
        if concept is not None:
            results = [a for a in results if a.concept.value == concept]
        if status is not None:
            results = [a for a in results if a.status.value == status]
        return results

    def save_review_case(self, review_case: ReviewCase) -> None:
        self._review_cases[review_case.review_id] = review_case

    def get_review_case(self, review_id: str) -> ReviewCase | None:
        return self._review_cases.get(review_id)

    def list_review_cases(self, *, bundle_id: str | None = None) -> list[ReviewCase]:
        results = list(self._review_cases.values())
        if bundle_id is not None:
            results = [r for r in results if r.bundle_id == bundle_id]
        return results

    def save_policy_release(self, release: PolicyRelease) -> None:
        self._policy_releases[release.release_id] = release

    def get_policy_release(self, release_id: str) -> PolicyRelease | None:
        return self._policy_releases.get(release_id)

    def list_policy_releases(self, *, bundle_id: str | None = None) -> list[PolicyRelease]:
        results = list(self._policy_releases.values())
        if bundle_id is not None:
            results = [r for r in results if r.bundle_id == bundle_id]
        return results

    def append_audit_event(
        self,
        *,
        aggregate_id: str,
        actor_id: str,
        actor_type: str,
        action: str,
        reason_code: str | None,
        object_version: str,
        object_content_hash: str,
        event_timestamp: dt.datetime,
        simulation_provenance: dict[str, Any],
        trace_id: str,
    ) -> AuditEvent:
        chain = self._audit_events.setdefault(aggregate_id, [])
        previous = chain[-1] if chain else None
        event = append_event(
            previous_event=previous,
            event_id=str(uuid.uuid4()),
            product=PRODUCT_NAME,
            aggregate_id=aggregate_id,
            actor_id=actor_id,
            actor_type=actor_type,
            action=action,
            reason_code=reason_code,
            object_version=object_version,
            object_content_hash=object_content_hash,
            event_timestamp=event_timestamp,
            simulation_provenance=simulation_provenance,
            trace_id=trace_id,
        )
        chain.append(event)
        return event

    def list_audit_events(self, aggregate_id: str) -> list[AuditEvent]:
        return list(self._audit_events.get(aggregate_id, []))
