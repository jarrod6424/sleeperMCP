"""Put the repo root, tests dir, and tools dir on sys.path so `import server`,
`import cases`, and `import build_benchmarks` work regardless of where pytest
is invoked from."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TOOLS = ROOT / "tools"

for path in (str(ROOT), str(HERE), str(TOOLS)):
    if path not in sys.path:
        sys.path.insert(0, path)
