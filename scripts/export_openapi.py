"""Exporta el esquema OpenAPI estático:  python scripts/export_openapi.py  ->  docs/openapi.json"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.main import app  # noqa: E402

(ROOT / "docs").mkdir(exist_ok=True)
(ROOT / "docs" / "openapi.json").write_text(json.dumps(app.openapi(), indent=2, ensure_ascii=False))
print("docs/openapi.json escrito")
