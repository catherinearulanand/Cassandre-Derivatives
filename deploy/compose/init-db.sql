-- Per-product database + least-privilege role, per ADR-002.
-- Each role can only connect to and act within its own database; no role has
-- grants on a sibling product's database. scripts/verify_db_isolation.py
-- proves this by attempting sibling access with each service credential.

CREATE ROLE clauseos_app LOGIN PASSWORD 'clauseos_dev_only_change_me';
CREATE DATABASE clauseos OWNER clauseos_app;

CREATE ROLE riskos_app LOGIN PASSWORD 'riskos_dev_only_change_me';
CREATE DATABASE riskos OWNER riskos_app;

CREATE ROLE collateralos_app LOGIN PASSWORD 'collateralos_dev_only_change_me';
CREATE DATABASE collateralos OWNER collateralos_app;

CREATE ROLE cassandre_control_app LOGIN PASSWORD 'cassandre_control_dev_only_change_me';
CREATE DATABASE cassandre_control OWNER cassandre_control_app;

REVOKE ALL ON DATABASE clauseos FROM riskos_app, collateralos_app, cassandre_control_app;
REVOKE ALL ON DATABASE riskos FROM clauseos_app, collateralos_app, cassandre_control_app;
REVOKE ALL ON DATABASE collateralos FROM clauseos_app, riskos_app, cassandre_control_app;
REVOKE ALL ON DATABASE cassandre_control FROM clauseos_app, riskos_app, collateralos_app;
