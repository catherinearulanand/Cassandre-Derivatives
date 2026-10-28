"""SIM-002: synthetic users/roles/accounts/SSIs/calendars, per plan Section
9.1 ("synthetic users and roles", "synthetic custody accounts, SSIs,
calendars, and policy owners") and the role table in plan Section 13.

Eight synthetic users cover all sixteen Section 13 roles, two roles each,
with every maker/checker pair deliberately assigned to *different* users so
self-approval is structurally impossible in the fixture data itself (plan
Section 13: "Security tests must prove: self-approval is impossible").
"""

from __future__ import annotations

from dataclasses import dataclass, field

from simulation_factory.identifiers import require_sim_identifier
from simulation_factory.institution import CASSANDRE_SIM_BANK_IFSC, CASSANDRE_SIM_BANK_NA
from simulation_factory.provenance import SimulationProvenance, make_provenance

SCENARIO_ID = "SIM-SCENARIO-CORPUS-STATIC-DATA"

# The exact sixteen roles from plan Section 13, in table order.
ALL_ROLES: tuple[str, ...] = (
    "clause_maker",
    "clause_checker",
    "risk_terms_maker",
    "risk_terms_checker",
    "risk_operator",
    "risk_method_owner",
    "risk_validator",
    "collateral_policy_maker",
    "collateral_policy_checker",
    "inventory_operator",
    "collateral_maker",
    "collateral_checker",
    "static_data_maker",
    "static_data_checker",
    "auditor",
    "pilot_admin",
)

# Maker/checker (or otherwise-must-not-collide) role pairs, per plan Section
# 13's security-test requirement that self-approval be impossible.
DISTINCT_ACTOR_ROLE_PAIRS: tuple[tuple[str, str], ...] = (
    ("clause_maker", "clause_checker"),
    ("risk_terms_maker", "risk_terms_checker"),
    ("risk_method_owner", "risk_validator"),
    ("collateral_policy_maker", "collateral_policy_checker"),
    ("collateral_maker", "collateral_checker"),
    ("static_data_maker", "static_data_checker"),
)


@dataclass(frozen=True)
class SyntheticUser:
    user_id: str
    display_name: str
    email: str
    roles: tuple[str, ...]
    active: bool
    provenance: SimulationProvenance

    def __post_init__(self) -> None:
        require_sim_identifier(self.user_id, field_name="user_id")
        if not self.display_name.startswith("SIM "):
            raise ValueError("synthetic user display_name must start with 'SIM ' to read as obviously synthetic")
        if "@sim-cassandre-pilot.example" not in self.email:
            raise ValueError("synthetic user email must use the reserved sim-cassandre-pilot.example domain")


@dataclass(frozen=True)
class CustodyAccount:
    account_id: str
    owner_entity_id: str
    currency: str
    account_type: str
    custodian_name: str
    provenance: SimulationProvenance

    def __post_init__(self) -> None:
        require_sim_identifier(self.account_id, field_name="account_id")
        require_sim_identifier(self.owner_entity_id, field_name="owner_entity_id")
        if not self.custodian_name.startswith("SIM "):
            raise ValueError("custodian_name must start with 'SIM ' to read as obviously synthetic")


@dataclass(frozen=True)
class StandingSettlementInstruction:
    ssi_id: str
    account_id: str
    currency: str
    settlement_route: str
    custodian_bic_shaped_id: str
    effective_from: str  # ISO date; kept as str to avoid importing datetime just for this
    provenance: SimulationProvenance

    def __post_init__(self) -> None:
        require_sim_identifier(self.ssi_id, field_name="ssi_id")
        require_sim_identifier(self.account_id, field_name="account_id")
        require_sim_identifier(self.custodian_bic_shaped_id, field_name="custodian_bic_shaped_id")


@dataclass(frozen=True)
class SettlementCalendar:
    calendar_id: str
    currency: str
    weekend_days: tuple[str, ...]
    holiday_dates: tuple[str, ...]
    provenance: SimulationProvenance

    def __post_init__(self) -> None:
        require_sim_identifier(self.calendar_id, field_name="calendar_id")


@dataclass(frozen=True)
class StaticDataCorpus:
    users: tuple[SyntheticUser, ...]
    custody_accounts: tuple[CustodyAccount, ...]
    ssis: tuple[StandingSettlementInstruction, ...]
    calendars: tuple[SettlementCalendar, ...]


def _user(
    seq: int,
    given: str,
    family: str,
    roles: tuple[str, ...],
) -> SyntheticUser:
    user_id = f"SIM-USER-{seq:03d}"
    return SyntheticUser(
        user_id=user_id,
        display_name=f"SIM {given} {family}",
        email=f"sim.user{seq:03d}@sim-cassandre-pilot.example",
        roles=roles,
        active=True,
        provenance=make_provenance(SCENARIO_ID),
    )


def build_users() -> tuple[SyntheticUser, ...]:
    """Eight users, sixteen roles, zero maker/checker collisions -- see
    module docstring and `DISTINCT_ACTOR_ROLE_PAIRS`."""

    users = (
        _user(1, "Ana", "Clausemaker", ("clause_maker", "risk_operator")),
        _user(2, "Beto", "Clausechecker", ("clause_checker", "inventory_operator")),
        _user(3, "Cleo", "Termsmaker", ("risk_terms_maker", "collateral_maker")),
        _user(4, "Deshi", "Termschecker", ("risk_terms_checker", "collateral_checker")),
        _user(5, "Elin", "Methodowner", ("risk_method_owner", "static_data_maker")),
        _user(6, "Faro", "Validator", ("risk_validator", "static_data_checker")),
        _user(7, "Gita", "Policymaker", ("collateral_policy_maker", "auditor")),
        _user(8, "Huan", "Policychecker", ("collateral_policy_checker", "pilot_admin")),
    )
    covered = {role for user in users for role in user.roles}
    missing = set(ALL_ROLES) - covered
    if missing:
        raise ValueError(f"build_users() does not cover every Section 13 role: missing {sorted(missing)}")
    for maker_role, checker_role in DISTINCT_ACTOR_ROLE_PAIRS:
        maker_users = {u.user_id for u in users if maker_role in u.roles}
        checker_users = {u.user_id for u in users if checker_role in u.roles}
        if maker_users & checker_users:
            raise ValueError(
                f"role pair ({maker_role!r}, {checker_role!r}) shares a user id "
                f"{sorted(maker_users & checker_users)} -- self-approval would be possible"
            )
    return users


def build_custody_accounts() -> tuple[CustodyAccount, ...]:
    accounts = []
    house_currencies = ("USD", "EUR", "GBP", "JPY", "INR")
    for idx, ccy in enumerate(house_currencies, start=1):
        accounts.append(
            CustodyAccount(
                account_id=f"SIM-ACCT-HOUSE-{idx:03d}",
                owner_entity_id=CASSANDRE_SIM_BANK_NA.entity_id,
                currency=ccy,
                account_type="HOUSE_CUSTODY",
                custodian_name=f"SIM Custodian Depository {idx:02d}",
                provenance=make_provenance(SCENARIO_ID),
            )
        )
    accounts.append(
        CustodyAccount(
            account_id="SIM-ACCT-AFFILIATE-001",
            owner_entity_id=CASSANDRE_SIM_BANK_IFSC.entity_id,
            currency="USD",
            account_type="AFFILIATE_CUSTODY",
            custodian_name="SIM Custodian Depository 06",
            provenance=make_provenance(SCENARIO_ID),
        )
    )
    return tuple(accounts)


def build_ssis(accounts: tuple[CustodyAccount, ...]) -> tuple[StandingSettlementInstruction, ...]:
    ssis = []
    for idx, account in enumerate(accounts, start=1):
        ssis.append(
            StandingSettlementInstruction(
                ssi_id=f"SIM-SSI-{idx:03d}",
                account_id=account.account_id,
                currency=account.currency,
                settlement_route=f"SIM-ROUTE-{account.currency}-{idx:03d}",
                custodian_bic_shaped_id=f"SIM-BIC-{idx:03d}XXXX",
                effective_from="2026-01-01",
                provenance=make_provenance(SCENARIO_ID),
            )
        )
    return tuple(ssis)


def build_calendars() -> tuple[SettlementCalendar, ...]:
    currencies = ("USD", "EUR", "GBP", "JPY", "INR")
    calendars = []
    for idx, ccy in enumerate(currencies, start=1):
        calendars.append(
            SettlementCalendar(
                calendar_id=f"SIM-CAL-{ccy}",
                currency=ccy,
                weekend_days=("SATURDAY", "SUNDAY"),
                holiday_dates=(f"2026-01-01", f"2026-12-25"),
                provenance=make_provenance(SCENARIO_ID),
            )
        )
    return tuple(calendars)


def build_static_data_corpus() -> StaticDataCorpus:
    accounts = build_custody_accounts()
    return StaticDataCorpus(
        users=build_users(),
        custody_accounts=accounts,
        ssis=build_ssis(accounts),
        calendars=build_calendars(),
    )
