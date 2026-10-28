"""Repository port (plan Section 25.5: "dependency injection so tests can
substitute an in-memory repository implementing the same interface as the
SQLAlchemy adapter"). This module only declares the interface; concrete
implementations live under `clauseos.adapters`.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Protocol

from audit_protocol.chain import AuditEvent
from clauseos.domain.assertion import ContractAssertion
from clauseos.domain.bundle import AgreementBundle
from clauseos.domain.policy import PolicyRelease
from clauseos.domain.review import ReviewCase


class BundleRepository(Protocol):
    def save_bundle(self, bundle: AgreementBundle) -> None: ...

    def get_bundle(self, bundle_id: str) -> AgreementBundle | None: ...

    def list_bundles(self) -> list[AgreementBundle]: ...

    def save_assertion(self, assertion: ContractAssertion) -> None: ...

    def get_assertion(self, assertion_id: str) -> ContractAssertion | None: ...

    def list_assertions(
        self, *, bundle_id: str | None = None, concept: str | None = None, status: str | None = None
    ) -> list[ContractAssertion]: ...

    def save_review_case(self, review_case: ReviewCase) -> None: ...

    def get_review_case(self, review_id: str) -> ReviewCase | None: ...

    def list_review_cases(self, *, bundle_id: str | None = None) -> list[ReviewCase]: ...

    def save_policy_release(self, release: PolicyRelease) -> None: ...

    def get_policy_release(self, release_id: str) -> PolicyRelease | None: ...

    def list_policy_releases(self, *, bundle_id: str | None = None) -> list[PolicyRelease]: ...

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
    ) -> AuditEvent: ...

    def list_audit_events(self, aggregate_id: str) -> list[AuditEvent]: ...
