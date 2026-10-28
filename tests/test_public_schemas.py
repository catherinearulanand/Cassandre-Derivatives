import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_export_script_produces_valid_schemas(tmp_path):
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "export_public_schemas.py")],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    schemas_dir = REPO_ROOT / "schemas" / "public"
    for name in ["envelope.schema.json", "money.schema.json", "quantity.schema.json",
                 "price.schema.json", "fx_conversion.schema.json"]:
        content = json.loads((schemas_dir / name).read_text(encoding="utf-8"))
        assert content["$schema"]
        assert content["$id"].endswith(name)


def test_envelope_schema_requires_simulation_provenance():
    schema = json.loads((REPO_ROOT / "schemas" / "public" / "envelope.schema.json").read_text(encoding="utf-8"))
    assert "simulation_provenance" in schema["required"]
