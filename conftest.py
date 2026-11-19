"""Make the service libraries importable in tests the same way the command line does."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for service in sorted(path.name for path in (ROOT / "services").iterdir() if path.is_dir()):
    sys.path.insert(0, str(ROOT / "services" / service))
sys.path.insert(0, str(ROOT))
