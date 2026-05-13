"""Make `src/` importable from tests without an editable install."""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))
