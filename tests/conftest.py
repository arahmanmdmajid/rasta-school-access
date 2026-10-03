import json
from pathlib import Path

import pytest

# `api` reaches sys.path via pyproject.toml's pythonpath setting, so no sys.path hack here.

ROOT = Path(__file__).resolve().parents[1]
DISTRICTS = ROOT / "web" / "districts"


@pytest.fixture(autouse=True)
def no_ai(monkeypatch):
    """
    The whole suite runs offline and makes no network call.

    Deleting the key means rasta.ai.available() is False, so the planner takes its
    keyword path and the writer returns computed text. Resetting the cached client is
    what stops one test's client leaking into the next.
    """
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    import rasta.ai as ai
    monkeypatch.setattr(ai, "_client", None)


@pytest.fixture(scope="session")
def bundle():
    """A real committed district bundle, so tests exercise real shapes, not fixtures."""
    files = sorted(DISTRICTS.glob("PK*.json"))
    if not files:
        pytest.skip("no district bundles built; run scripts/build_bundles.py")
    # Prefer a district with mapped schools so the ordinary path is what gets tested.
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["confidence"]["mapped"] > 0:
            return data
    return json.loads(files[0].read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def empty_bundle():
    """A district with zero mapped schools, if one is built - the awkward edge case."""
    for path in sorted(DISTRICTS.glob("PK*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if data["confidence"]["mapped"] == 0:
            return data
    pytest.skip("no zero-school district bundled")
