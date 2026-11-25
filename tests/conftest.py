"""Pytest fixtures providing an isolated temporary data directory, .env, and SQLite DB per test."""
import sqlite3
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch):
    import backend.config_store as cs
    import backend.api as api_mod
    import backend.scraper as sc
    import backend.discovery as disc

    monkeypatch.setattr(cs, "DATA", tmp_path)
    monkeypatch.setattr(cs, "ENV_FILE", tmp_path / ".env")
    (tmp_path / "cvs").mkdir(parents=True, exist_ok=True)

    test_db = sqlite3.connect(tmp_path / "test_radar.db", check_same_thread=False)
    test_db.execute("CREATE TABLE IF NOT EXISTS offers(id TEXT PRIMARY KEY, j TEXT)")
    test_db.execute("CREATE TABLE IF NOT EXISTS seen(id TEXT PRIMARY KEY)")
    test_db.execute("CREATE TABLE IF NOT EXISTS contacts(id TEXT PRIMARY KEY, j TEXT)")
    test_db.execute("CREATE TABLE IF NOT EXISTS uni_portals(domain TEXT PRIMARY KEY, j TEXT)")
    test_db.commit()

    # Patch DB across all modules that imported DB directly
    for mod in (cs, api_mod, sc, disc):
        if hasattr(mod, "DB"):
            monkeypatch.setattr(mod, "DB", test_db)

    monkeypatch.setattr(cs, "PRIVACY_LOG", [])

    for var in (
        "AI_PROVIDER",
        "OLLAMA_URL",
        "OLLAMA_MODEL",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "ACCEPTANCE_THRESHOLD",
    ):
        monkeypatch.delenv(var, raising=False)

    yield tmp_path
    test_db.close()