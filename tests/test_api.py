"""End-to-end FastAPI endpoint tests using TestClient (no external network calls)."""
from fastapi.testclient import TestClient
from backend.api import app
import backend.config_store as cs
import backend.api as api_mod

client = TestClient(app)


def test_version_and_diagnostics():
    r = client.get("/api/version")
    assert r.status_code == 200
    body = r.json()
    assert body["sources"] >= 15
    assert body["universities"] >= 20

    d = client.get("/api/diagnostics")
    assert d.status_code == 200
    assert "db" in d.json()


def test_offers_score_and_on_demand_explain(monkeypatch):
    cs.put_offer({
        "id": "offer999",
        "title": "PhD in Streaming Data Architectures",
        "institution": "ETH Zurich",
        "country": "Switzerland",
        "program_type": "ETH Zurich Doctoral Positions",
        "description_text": "Distributed stream processing and SQL query engines.",
        "url": "https://jobs.ethz.ch/job/view/999",
        "contact_emails": [],
    })

    monkeypatch.setattr(
        api_mod,
        "score_offer_fast",
        lambda o, prof, cv, provider=None, model=None: {"chance": 81, "match": 86, "requires": ["SQL", "Distributed Systems"]},
    )
    monkeypatch.setattr(
        api_mod,
        "explain_offer_score",
        lambda o, prof, provider=None, model=None: "1. Strong match in distributed SQL.\n2. ETH Zurich funded doctoral salary.",
    )

    # 1. Run fast numeric scoring
    s_res = client.post("/api/score", json={"provider": "ollama", "profile": {"name": "Yassir"}})
    assert s_res.status_code == 200
    assert s_res.json()["scored"] == 1

    # Verify percentage stored as integer 81
    offers_list = client.get("/api/offers").json()
    assert offers_list[0]["chance"] == 81

    # 2. Request on-demand explanation
    e_res = client.post("/api/offers/offer999/explain", json={"provider": "ollama", "profile": {"name": "Yassir"}})
    assert e_res.status_code == 200
    assert "ETH Zurich" in e_res.json()["explanation"]


def test_settings_masks_api_keys_on_get():
    client.put("/api/settings", json={"provider": "gemini", "gemini_key": "SECRET_KEY_XYZ", "gemini_model": "gemini-4-hydrogen"})
    res = client.get("/api/settings").json()
    assert res["provider"] == "gemini"
    assert res["gemini_key_set"] is True
    assert res["gemini_key"] == ""  # Secret must never be sent back in plaintext
