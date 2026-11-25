import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def outputs(tmp_path_factory):
    """Corre el pipeline completo UNA vez sobre los datos del repo y devuelve la carpeta de salida."""
    from gapfill import modelo as team
    from gapfill.config import Config
    from gapfill.engine import run
    out = tmp_path_factory.mktemp("out")
    run(ROOT / "data", out, team, Config(), log=lambda *_: None)
    return out
