"""Tests for Ollama, Gemini, and OpenAI client routing, integer percentage coercion, and on-demand explanation."""
import json
import backend.llm_providers as lp


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_extract_int_percent_never_returns_words():
    assert lp._extract_int_percent(82) == 82
    assert lp._extract_int_percent("76%") == 76
    assert lp._extract_int_percent("120") == 100
    assert lp._extract_int_percent("-5") == 0
    assert lp._extract_int_percent("mid") == 55
    assert lp._extract_int_percent("low") == 28
    assert lp._extract_int_percent("high") == 78


def test_call_llm_routes_to_gemini(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None, **kwargs):
        captured["url"] = url
        captured["body"] = json
        return _FakeResponse({"candidates": [{"content": {"parts": [{"text": '{"chance_percent":84,"match_percent":88,"requires":["Spark"]}'}]}}]})

    monkeypatch.setenv("AI_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-gem-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-4-hydrogen")
    monkeypatch.setattr(lp.httpx, "post", fake_post)

    res = lp.score_offer_fast(
        {"title": "PhD Data Engineering", "description_text": "Apache Spark and Kafka"},
        {"name": "Yassir"},
        "Spark, Airflow, Python",
    )
    assert "generativelanguage.googleapis.com" in captured["url"]
    assert "gemini-4-hydrogen" in captured["url"]
    assert res["chance"] >= 84
    assert res["match"] >= 88
    assert res["requires"] == ["Spark"]


def test_call_llm_routes_to_openai(monkeypatch):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json
        return _FakeResponse({"choices": [{"message": {"content": '{"chance_percent":79,"match_percent":81,"requires":["Python","SQL"]}'}}]})

    monkeypatch.setenv("AI_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-6.1-sol")
    monkeypatch.setattr(lp.httpx, "post", fake_post)

    res = lp.score_offer_fast(
        {"title": "PhD Cloud Databases", "description_text": "SQL and distributed storage"},
        {"name": "Yassir"},
        "PostgreSQL, Snowflake",
    )
    assert captured["url"] == "https://api.openai.com/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-test-openai"
    assert captured["body"]["model"] == "gpt-6.1-sol"
    assert res["chance"] >= 79
    assert res["match"] >= 81


def test_explain_offer_score_on_demand(monkeypatch):
    monkeypatch.setattr(lp, "call_llm", lambda *args, **kwargs: "- Strong match on Spark and Azure Synapse\n- Non-EU visa eligible under CIFRE")
    explanation = lp.explain_offer_score(
        {"title": "CIFRE PhD", "chance": 82, "match": 85, "description_text": "Azure & Spark"},
        {"name": "Yassir"},
    )
    assert "Spark" in explanation
    assert "CIFRE" in explanation