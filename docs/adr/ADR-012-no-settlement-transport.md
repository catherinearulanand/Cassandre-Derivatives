# ADR-012: No settlement transport component

## Decision
No code path, configuration, dependency, or test fixture in this repository
ever connects to a custodian, SWIFT, tri-party, CCP, or payment system. There
is no "future adapter waiting for credentials" — the transport does not exist.

## Enforcement
`scripts/check_import_graph.py` also greps for network-egress libraries
(`paramiko`, `smtplib` used for external transmission, any SWIFT/ISO20022
SDK) in `products/collateralos` and fails the build if found. CollateralOS's
network policy (Section 6.2) is "no internet access" by default-deny.
