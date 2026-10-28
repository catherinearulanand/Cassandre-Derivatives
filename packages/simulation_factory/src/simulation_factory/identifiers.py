"""Reserved synthetic-identifier namespace, per plan Section 9.1:
"Identifiers must use a reserved namespace such as `SIM-*`; never use real
LEIs, BICs, ISINs, account numbers, or company names accidentally."
"""

from __future__ import annotations

import re

SIM_NAMESPACE_PREFIX = "SIM-"

_SIM_ID_RE = re.compile(r"^SIM-[A-Z0-9][A-Z0-9-]*$")


class IdentifierNamespaceError(ValueError):
    pass


def is_sim_identifier(value: str) -> bool:
    return bool(_SIM_ID_RE.match(value))


def require_sim_identifier(value: str, *, field_name: str = "identifier") -> str:
    if not is_sim_identifier(value):
        raise IdentifierNamespaceError(
            f"{field_name} {value!r} is not in the reserved '{SIM_NAMESPACE_PREFIX}*' "
            "synthetic namespace"
        )
    return value
