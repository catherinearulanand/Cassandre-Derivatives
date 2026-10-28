"""Dependency injection wiring for the RiskOS API. Uses the in-memory
repository so API tests run without Postgres (Docker Desktop is not
functional on this build machine). A real deployment would swap in
`riskos.adapters.sql_repository.SqlAlchemyRiskOSRepository` bound to a
per-request SQLAlchemy `Session` instead — the `RiskOSService` and every
route below depend only on `riskos.application.repositories.RiskOSRepository`,
so that swap needs no route changes.
"""

from __future__ import annotations

from audit_protocol.keys import generate_keypair

from riskos.adapters.in_memory import InMemoryRiskOSRepository
from riskos.application.service import RiskOSService

_repository = InMemoryRiskOSRepository()
_keypair = generate_keypair("SIM-RISKOS-API-KEY-0001")
_service = RiskOSService(_repository, keypair=_keypair, issuer="riskos_api")


def get_service() -> RiskOSService:
    return _service
