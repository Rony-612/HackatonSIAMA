"""Un solo comando:  python -m gapfill.cli  [--data-dir data_oficial] [--output-dir outputs]"""
import argparse
import time
from pathlib import Path

from . import modelo as spec
from .config import Config
from .engine import run


def main():
    ap = argparse.ArgumentParser(description=f"Pipeline de {spec.meta()['nombre']}")
    ap.add_argument("--data-dir", type=Path, default=Path("data_oficial") if Path("data_oficial").exists() else Path("data"))
    ap.add_argument("--output-dir", type=Path, default=Path("outputs"))
    a = ap.parse_args()
    t0 = time.time()
    run(a.data_dir, a.output_dir, spec, Config())
    print(f"Terminado en {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
