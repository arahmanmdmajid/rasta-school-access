"""End-to-end checks of the web API (offline: featured data, keyword routing)."""
import pytest
from fastapi.testclient import TestClient

import app as api_app


@pytest.fixture()
def client():
    api_app._hits.clear()
    return TestClient(api_app.app)


def load(client, key="ghirnatah"):
    r = client.post("/area", json={"featured": key})
    assert r.status_code == 200
    return r.json()


def test_health_and_featured(client):
    assert client.get("/health").json()["ok"] is True
    keys = {f["key"] for f in client.get("/featured").json()}
    assert keys == {"ghirnatah", "gulberg", "clifton"}


def test_area_payload(client):
    a = load(client)
    assert a["counts"] == {"schools": 8, "facilities": 16, "hospitals": 11}
    assert len(a["schools"]["features"]) == 8 and a["suggestions"]


def test_question_before_area_gets_guidance(client):
    r = client.post("/ask", json={"question": "Which areas have poor access to healthcare?"}).json()
    assert r["needs_area"] is True


def test_near_me_asks_the_page_for_location(client):
    r = client.post("/ask", json={"question": "Which clinics are near me?"}).json()
    assert r == {"needs_location": True}


def test_coverage_answer_draws_gap(client):
    a = load(client)
    r = client.post("/ask", json={"area_id": a["area_id"], "question": "Which areas have poor access to healthcare?"}).json()
    roles = [layer["role"] for layer in r["draw"]["layers"]]
    assert "gap" in roles and r["tag"].startswith("coverage")
    assert "39%" in r["facts"]


def test_named_featured_district_switches_area(client):
    a = load(client, "ghirnatah")
    r = client.post("/ask", json={"area_id": a["area_id"], "question": "How many schools are in Gulberg, Lahore?"}).json()
    assert r["area"]["place"] == "Gulberg, Lahore"
    assert "79 schools" in r["facts"]


def test_off_topic_is_refused(client):
    a = load(client)
    r = client.post("/ask", json={"area_id": a["area_id"], "question": "What's the weather today?"}).json()
    assert r["tag"] == "unsupported()"


def test_denied_location_falls_back_to_centre(client):
    a = load(client)
    r = client.post("/ask", json={"area_id": a["area_id"], "question": "schools near me", "me_error": "denied"}).json()
    assert "couldn't get your location" in r["facts"]


def test_cors_only_for_our_pages(client):
    ok = client.options("/ask", headers={"Origin": "https://arahmanmdmajid-rasta-school-access.static.hf.space",
                                         "Access-Control-Request-Method": "POST"})
    bad = client.options("/ask", headers={"Origin": "https://evil.example.com",
                                          "Access-Control-Request-Method": "POST"})
    assert ok.headers.get("access-control-allow-origin") == "https://arahmanmdmajid-rasta-school-access.static.hf.space"
    assert "access-control-allow-origin" not in bad.headers
