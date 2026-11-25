"""Tests for YAML source/university configs, .env loading, CV parsing, and SQLite persistence."""
from pathlib import Path
import yaml
import backend.config_store as cs


def test_sources_default_yaml_valid():
    assert cs.DEF.exists(), "config/sources.default.yaml is missing"
    data = yaml.safe_load(cs.DEF.read_text("utf-8"))
    assert isinstance(data.get("version"), int)
    assert len(data.get("keywords", [])) >= 5
    sources = data.get("sources", [])
    assert len(sources) >= 15

    seen_ids = set()
    for s in sources:
        assert s["id"] not in seen_ids, f"Duplicate source id: {s['id']}"
        seen_ids.add(s["id"])
        assert s["url"].startswith("http"), f"Invalid URL in {s['id']}"
        assert s["kind"] in ("listing", "watch")
        assert s.get("fetch", "auto") in ("auto", "http", "browser")


def test_universities_yaml_valid():
    unis = cs.load_universities()
    assert len(unis) >= 20, "config/universities.yaml should contain your target universities"
    for name, country, domain in unis:
        assert name and country and "." in domain


def test_dotenv_loading_and_settings_merge(tmp_path: Path, monkeypatch):
    env_path = tmp_path / ".env"
    env_path.write_text(
        'AI_PROVIDER=gemini\n'
        'GEMINI_API_KEY="AIzaTestKey123"\n'
        'GEMINI_MODEL=gemini-4-hydrogen\n'
        'OPENAI_API_KEY=sk-test-456\n',
        encoding="utf-8",
    )
    cs._load_dotenv(env_path)
    s = cs.settings()
    assert s["provider"] == "gemini"
    assert s["gemini_key"] == "AIzaTestKey123"
    assert s["gemini_model"] == "gemini-4-hydrogen"
    assert s["openai_key"] == "sk-test-456"


def test_put_offer_persists_offer_and_extracts_contacts():
    offer = {
        "id": "abc1234567",
        "title": "CIFRE PhD in Distributed Lakehouse Systems",
        "institution": "INRIA",
        "industry_partner": "Schneider Electric",
        "supervisor": "Dr. Jean Dupont",
        "country": "France",
        "program_type": "ANRT CIFRE offers",
        "description_text": "Big data Spark and Snowflake architecture.",
        "contact_emails": ["jean.dupont@inria.fr"],
        "url": "https://example.org/offer/1",
    }
    cs.put_offer(offer)

    stored = cs.offers()
    assert len(stored) == 1
    assert stored[0]["title"] == offer["title"]
    assert stored[0]["category"] == "Data Engineering & Big Data"

    rows = cs.DB.execute("SELECT j FROM contacts").fetchall()
    assert len(rows) == 1
    assert "jean.dupont@inria.fr" in rows[0][0]


def test_cv_text_extraction_txt(tmp_path: Path):
    cv_file = cs.DATA / "cvs" / "yassir_cv.txt"
    cv_file.write_text("Yassir Tagemouati - Big Data Engineer - Spark, Airflow, Azure Synapse", encoding="utf-8")
    ctx = cs.all_cvs_context()
    assert "Yassir Tagemouati" in ctx
    assert "Azure Synapse" in ctx
