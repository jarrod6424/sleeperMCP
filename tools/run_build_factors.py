"""Run build_factors with Windows trust store for SSL."""
import sys
from pathlib import Path

import truststore

truststore.inject_into_ssl()

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import build_factors  # noqa: E402

sys.exit(build_factors.main())
