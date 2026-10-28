"""Authoritative-table privilege lockdown (SEC-15, DB-enforcement half).

Spec section 12: "Authoritative tables deny direct `INSERT`, `UPDATE`, and
`DELETE` to runtime and administrative application roles. Mutations occur
only through a narrowly granted database-enforced transaction interface -
reviewed stored procedures or equivalently enforced mechanism... A database
owner/superuser can bypass this and remains a declared privileged
residual; witness comparison and clean rebuild are detection/recovery
controls, not prevention."

This module generates the REVOKE/GRANT SQL a product's own Alembic
migration applies, and a verification helper that queries Postgres's own
`information_schema` to PROVE the lockdown is actually in effect (not just
assumed from the migration file's existence) -- matching this session's
established practice of verifying against a real running Postgres rather
than trusting configuration alone.

This does not (and per the spec, cannot) protect against the database
superuser/owner. That residual is declared, not hidden.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection


def generate_lockdown_sql(*, table_names: tuple[str, ...], app_role: str, transaction_role: str) -> str:
    """Returns SQL a migration can execute: revoke direct DML from
    `app_role` on every listed authoritative table, and grant it only to
    `transaction_role` (the identity a narrowly-scoped stored
    procedure/function runs as, e.g. via `SECURITY DEFINER` or a dedicated
    connection role). Idempotent (`IF EXISTS`/plain REVOKE, safe to
    re-run)."""

    if not table_names:
        raise ValueError("at least one table name is required")

    statements: list[str] = []
    for table in table_names:
        statements.append(f'REVOKE INSERT, UPDATE, DELETE ON TABLE "{table}" FROM "{app_role}";')
        statements.append(f'GRANT INSERT, UPDATE, DELETE ON TABLE "{table}" TO "{transaction_role}";')
    return "\n".join(statements)


@dataclass(frozen=True)
class RoleTableGrantAudit:
    table_name: str
    role_name: str
    has_insert: bool
    has_update: bool
    has_delete: bool

    @property
    def has_any_direct_dml(self) -> bool:
        return self.has_insert or self.has_update or self.has_delete


def audit_role_table_grants(connection: Connection, *, table_name: str, role_name: str) -> RoleTableGrantAudit:
    """Queries Postgres's own `information_schema.role_table_grants` --
    the authoritative source of truth for what a role can actually do,
    independent of whatever a migration file claims to have run."""

    result = connection.execute(
        text(
            """
            SELECT privilege_type
            FROM information_schema.role_table_grants
            WHERE table_name = :table_name AND grantee = :role_name
            """
        ),
        {"table_name": table_name, "role_name": role_name},
    )
    privileges = {row[0] for row in result}
    return RoleTableGrantAudit(
        table_name=table_name,
        role_name=role_name,
        has_insert="INSERT" in privileges,
        has_update="UPDATE" in privileges,
        has_delete="DELETE" in privileges,
    )


def assert_role_lacks_direct_dml(connection: Connection, *, table_name: str, role_name: str) -> None:
    audit = audit_role_table_grants(connection, table_name=table_name, role_name=role_name)
    if audit.has_any_direct_dml:
        raise AssertionError(
            f"role {role_name!r} still has direct DML on {table_name!r}: "
            f"insert={audit.has_insert} update={audit.has_update} delete={audit.has_delete}"
        )
