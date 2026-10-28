# ADR-009: Keycloak-compatible OIDC; product-local authorization

## Decision
Keycloak provides identity, MFA capability, users, groups, sessions. Each
product owns its own authorization (object scope, maker/checker distinction,
role matrix from Section 13) — Keycloak is never asked to know about legal
entities, materiality, or approval state.

## S1 implementation and known gap
Keycloak is scaffolded in `deploy/compose/docker-compose.yml` with a realm
import (`deploy/compose/keycloak-realm-cassandre-sim.json`) defining the
Section 13 roles. Each product API validates RS256 JWTs against Keycloak's
JWKS endpoint. **Gap honestly recorded**: end-to-end login through Keycloak's
UI is not exercised in this session's automated tests; tests use locally
signed JWTs shaped like Keycloak output (same claims, same validation code
path) to keep the test suite fast and network-free. Wiring the realm import
and running one real Keycloak-issued-token test is left as explicit follow-up
work, not claimed done.
