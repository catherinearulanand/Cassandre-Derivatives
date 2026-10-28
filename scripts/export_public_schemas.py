#!/usr/bin/env python3
"""Export the versioned public JSON schemas (plan Section 8, docs/INTERPRETATION.md
item 6) from their live Pydantic models, so the schema files on disk can never
drift from the code that actually validates against them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from cassandre_contracts.envelope import Envelope  # noqa: E402
from money_types.core import FXConversion, Money, Price, Quantity  # noqa: E402

OUT_DIR = REPO_ROOT / "schemas" / "public"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    exports = {
        "envelope.schema.json": Envelope,
        "money.schema.json": Money,
        "quantity.schema.json": Quantity,
        "price.schema.json": Price,
        "fx_conversion.schema.json": FXConversion,
    }
    for filename, model in exports.items():
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"https://cassandre.sim/schemas/public/{filename}"
        (OUT_DIR / filename).write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {OUT_DIR / filename}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
