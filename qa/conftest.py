from __future__ import annotations

import sys
from pathlib import Path


API_SOURCE = Path(__file__).resolve().parents[1] / "apps/api/src"
if str(API_SOURCE) not in sys.path:
    sys.path.insert(0, str(API_SOURCE))
