"""Cassandre marketing site — the public brand page for the platform
(ClauseOS / RiskOS / CollateralOS / Cassandre federation). Purely static
content: no business logic, no database, no import of any product or
`cassandre_control_api` package (consistent with ADR-001's independent-
deployability boundary). Exists only to present the architecture and link
out to the real control surfaces; it never itself submits envelopes,
computes margin, or plans collateral.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Cassandre Marketing Site", version="0.1.0")

_STATIC_DIR = Path(__file__).resolve().parents[2] / "static"
if _STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="marketing-site")
