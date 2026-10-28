"""SIM-002: maker/checker self-approval must be structurally impossible in
the fixture data itself, per plan Section 13."""

from __future__ import annotations

from simulation_factory.corpus.users import (
    DISTINCT_ACTOR_ROLE_PAIRS,
    build_custody_accounts,
    build_static_data_corpus,
    build_users,
)


def test_no_user_holds_both_sides_of_a_maker_checker_pair():
    users = build_users()
    for maker_role, checker_role in DISTINCT_ACTOR_ROLE_PAIRS:
        for user in users:
            assert not (maker_role in user.roles and checker_role in user.roles), (
                f"user {user.user_id} holds both {maker_role!r} and {checker_role!r}"
            )


def test_custody_accounts_span_both_entities():
    accounts = build_custody_accounts()
    from simulation_factory.institution import CASSANDRE_SIM_BANK_IFSC, CASSANDRE_SIM_BANK_NA

    owners = {a.owner_entity_id for a in accounts}
    assert CASSANDRE_SIM_BANK_NA.entity_id in owners
    assert CASSANDRE_SIM_BANK_IFSC.entity_id in owners


def test_ssis_reference_real_accounts():
    corpus = build_static_data_corpus()
    account_ids = {a.account_id for a in corpus.custody_accounts}
    for ssi in corpus.ssis:
        assert ssi.account_id in account_ids


def test_all_users_and_accounts_carry_simulation_provenance():
    corpus = build_static_data_corpus()
    for user in corpus.users:
        assert user.provenance.is_simulated is True
    for account in corpus.custody_accounts:
        assert account.provenance.is_simulated is True
