import os
import sys
from pathlib import Path

import pytest

# Make "import rasta" and "import app" work from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))


@pytest.fixture(autouse=True)
def no_ai(monkeypatch):
    """Tests run offline: no Groq key, so routing uses the keyword router."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    import rasta.ai as ai
    monkeypatch.setattr(ai, "_client", None)
