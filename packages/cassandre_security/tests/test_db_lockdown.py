from __future__ import annotations

import uuid

from sqlalchemy import text

from cassandre_security.db_lockdown import (
    assert_role_lacks_direct_dml,
    audit_role_table_grants,
    generate_lockdown_sql,
)


def test_generate_lockdown_sql_shape():
    sql = generate_lockdown_sql(table_names=("obligation", "plan"), app_role="collateralos_app", transaction_role="collateralos_txn")
    assert 'REVOKE INSERT, UPDATE, DELETE ON TABLE "obligation" FROM "collateralos_app";' in sql
    assert 'GRANT INSERT, UPDATE, DELETE ON TABLE "obligation" TO "collateralos_txn";' in sql
    assert 'REVOKE INSERT, UPDATE, DELETE ON TABLE "plan" FROM "collateralos_app";' in sql
    assert 'GRANT INSERT, UPDATE, DELETE ON TABLE "plan" TO "collateralos_txn";' in sql


def test_generate_lockdown_sql_requires_at_least_one_table():
    import pytest

    with pytest.raises(ValueError):
        generate_lockdown_sql(table_names=(), app_role="x", transaction_role="y")


def test_real_postgres_lockdown_actually_removes_direct_dml(admin_postgres_engine):
    """End-to-end proof against a real Postgres cluster: create a
    non-owner app role with DML granted, apply the lockdown SQL, and
    confirm `information_schema.role_table_grants` -- not our own
    assumption -- shows the grant is gone."""

    suffix = uuid.uuid4().hex[:8]
    app_role = f"sec_test_app_{suffix}"
    txn_role = f"sec_test_txn_{suffix}"
    table = f"sec_test_table_{suffix}"

    with admin_postgres_engine.connect() as conn:
        conn.execute(text(f'CREATE ROLE "{app_role}" NOLOGIN'))
        conn.execute(text(f'CREATE ROLE "{txn_role}" NOLOGIN'))
        conn.execute(text(f'CREATE TABLE "{table}" (id text primary key)'))
        conn.execute(text(f'GRANT INSERT, UPDATE, DELETE ON TABLE "{table}" TO "{app_role}"'))

        before = audit_role_table_grants(conn, table_name=table, role_name=app_role)
        assert before.has_any_direct_dml

        lockdown_sql = generate_lockdown_sql(table_names=(table,), app_role=app_role, transaction_role=txn_role)
        for statement in lockdown_sql.splitlines():
            conn.execute(text(statement))

        assert_role_lacks_direct_dml(conn, table_name=table, role_name=app_role)

        txn_grants = audit_role_table_grants(conn, table_name=table, role_name=txn_role)
        assert txn_grants.has_any_direct_dml

        # cleanup
        conn.execute(text(f'DROP TABLE "{table}"'))
        conn.execute(text(f'DROP ROLE "{app_role}"'))
        conn.execute(text(f'DROP ROLE "{txn_role}"'))
