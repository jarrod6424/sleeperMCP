"""Put the repo root and tests dir on sys.path so `import server` and
`import cases` work regardless of where pytest is invoked from."""

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

for path in (str(ROOT), str(HERE)):
    if path not in sys.path:
        sys.path.insert(0, path)
