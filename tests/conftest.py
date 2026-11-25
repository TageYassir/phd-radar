"""Pytest fixtures providing an isolated temporary data directory and SQLite DB per test."""
import sqlite3
from pathlib import Path
import pytest


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch):
    import backend.config_store as cs

    # Redirect DATA to a fresh temp folder so tests never touch %LOCALAPPDATA%\PhD-Radar
    monkeypatch.setattr(cs, "DATA", tmp_path)
    (tmp_path / "cvs").mkdir(parents=True, exist_ok=True)

    # Fresh in-memory/temp SQLite database per test
    test_db = sqlite3.connect(tmp_path / "test_radar.db", check_same_thread=False)
    test_db.execute("CREATE TABLE IF NOT EXISTS offers(id TEXT PRIMARY KEY, j TEXT)")
    test_db.execute("CREATE TABLE IF NOT EXISTS seen(id TEXT PRIMARY KEY)")
    test_db.execute("CREATE TABLE IF NOT EXISTS contacts(id TEXT PRIMARY KEY, j TEXT)")
    test_db.execute("CREATE TABLE IF NOT EXISTS uni_portals(domain TEXT PRIMARY KEY, j TEXT)")
    test_db.commit()

    monkeypatch.setattr(cs, "DB", test_db)
    monkeypatch.setattr(cs, "PRIVACY_LOG", [])

    # Ensure clean default env vars unless explicitly set in a test
    for var in ("AI_PROVIDER", "OLLAMA_URL", "OLLAMA_MODEL", "GEMINI_API_KEY", "GEMINI_MODEL", "OPENAI_API_KEY", "OPENAI_MODEL"):
        monkeypatch.delenv(var, raising=False)

    yield tmp_path
    test_db.close()
