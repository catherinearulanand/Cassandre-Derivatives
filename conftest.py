"""Root conftest: make each product's own tests/ directory importable for
local test-support modules (e.g. `helpers.py`, `fixtures/`) without giving
those modules globally unique names — needed because the workspace uses
--import-mode=importlib (to avoid a different problem: multiple products'
tests/__init__.py colliding under the same top-level "tests" module name).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

for tests_dir in list(ROOT.glob("products/*/tests")) + list(ROOT.glob("apps/*/tests")):
    p = str(tests_dir)
    if p not in sys.path:
        sys.path.insert(0, p)
