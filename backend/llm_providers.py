from __future__ import annotations
"""Unified AI client (Ollama, Google Gemini API, OpenAI API) + calibrated % scoring & on-demand explanations."""
import json, re
import httpx
from .config_store import CATEGORIES, all_cvs_context, jload, norm, settings

LLM_STATE = {"off": False}

FALLBACK_PROFILE = {
    "name": "Yassir Tagemouati",
    "headline": "5th-year Big Data Engineering student (EIDIA, UEMF, Fès, graduating July 2027)",
    "skills": [
        "Python", "SQL", "PySpark", "Apache Spark", "Hadoop", "Kafka", "Airflow",
        "Snowflake", "Azure Synapse", "Azure Data Factory", "ADLS Gen2", "AWS S3",
        "PostgreSQL", "Neo4j", "Power BI", "Docker", "Machine Learning", "XGBoost",
        "LSTM", "RAG", "LLM", "Data Engineering", "Data Science", "ETL", "Medallion Architecture",
    ],
    "languages": "Arabic (native), French (advanced), English (advanced)",
    "prefs": "Funded or industrial PhD in Data Engineering, Big Data, Data Science, Cloud Systems, Knowledge Graphs, or Applied AI. Non-EU candidate based in Morocco.",
}


def _extract_json(raw: str) -> dict:
    txt = raw.strip()
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt, flags=re.I | re.S).strip()
    m = re.search(r"\{.*\}", txt, re.S)
    return json.loads(m.group(0) if m else txt)


def _extract_int_percent(val, default: int = 0) -> int:
    if isinstance(val, (int, float)):
        return max(0, min(100, int(round(val))))
    s = str(val).strip().lower()
    m = re.search(r"(-?\d{1,3})", s)
    if m:
        return max(0, min(100, int(m.group(1))))
    word_map = {"very high": 88, "high": 78, "good": 72, "medium": 55, "mid": 55, "moderate": 52, "low": 28, "very low": 15}
    for k, num in word_map.items():
        if k in s:
            return num
    return default


def _heuristic_skill_score(o: dict, prof: dict, cv_text: str) -> tuple[int, int, list[str]]:
    """Deterministic skill & domain overlap baseline so relevant Data Science / Big Data offers never score 0%."""
    offer_blob = norm(f"{o.get('title', '')} {o.get('category', '')} {o.get('description_text', '')[:3500]}")
    cand_blob = norm(f"{json.dumps(prof)} {cv_text[:4000]}")

    core_terms = [
        "data science", "data engineering", "big data", "machine learning", "deep learning",
        "python", "sql", "spark", "hadoop", "kafka", "airflow", "snowflake", "azure", "aws",
        "cloud", "distributed", "database", "knowledge graph", "ontology", "nlp", "llm", "rag",
        "analytics", "etl", "pipeline", "mlops", "artificial intelligence", "statistics", "time series",
    ]
    matched = [t for t in core_terms if t in offer_blob and t in cand_blob]
    offer_terms = [t for t in core_terms if t in offer_blob]

    if not offer_terms:
        # Unrelated field (e.g., pure wet-lab biology/chemistry)
        return 12, 15, []

    ratio = len(matched) / max(1, min(len(offer_terms), 6))
    match_pct = int(round(min(94, 42 + ratio * 52)))
    # Non-EU international PhD baseline adjustment (-8% for administrative/visa competition)
    chance_pct = max(25, min(90, match_pct - 7))
    return chance_pct, match_pct, [m.title() for m in matched[:8]]


def call_llm(prompt: str, js: bool = False, timeout: int = 180, provider_override: str = None, model_override: str = None) -> str:
    s = settings()
    provider = (provider_override or s.get("provider") or "ollama").lower()

    if provider == "gemini":
        api_key = s.get("gemini_key", "").strip()
        if not api_key:
            raise RuntimeError("Gemini API key is missing. Add GEMINI_API_KEY in .env or Settings.")
        model = model_override or s.get("gemini_model") or "gemini-4-hydrogen"
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        gen_cfg = {"temperature": 0.1}
        if js:
            gen_cfg["responseMimeType"] = "application/json"
        body = {"contents": [{"parts": [{"text": prompt}]}], "generationConfig": gen_cfg}
        r = httpx.post(url, json=body, timeout=timeout)
        if r.status_code >= 400:
            raise RuntimeError(f"Gemini API error ({r.status_code}): {r.text[:250]}")
        return r.json()["candidates"][0]["content"]["parts"][0]["text"]

    if provider == "openai":
        api_key = s.get("openai_key", "").strip()
        if not api_key:
            raise RuntimeError("OpenAI API key is missing. Add OPENAI_API_KEY in .env or Settings.")
        model = model_override or s.get("openai_model") or "gpt-6.1-sol"
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
        }
        if js:
            body["response_format"] = {"type": "json_object"}
        r = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
            timeout=timeout,
        )
        if r.status_code >= 400:
            raise RuntimeError(f"OpenAI API error ({r.status_code}): {r.text[:250]}")
        return r.json()["choices"][0]["message"]["content"]

    model = model_override or s.get("model") or "gemma4:26b"
    base_url = s.get("ollama", "http://localhost:11434").rstrip("/")
    body = {"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0.1, "seed": 42}}
    if js:
        body["format"] = "json"
    try:
        r = httpx.post(base_url + "/api/generate", json=body, timeout=timeout)
        if r.status_code == 404:
            raise RuntimeError(f"Ollama model '{model}' not found. Run: ollama pull {model}")
        r.raise_for_status()
        LLM_STATE["off"] = False
        return r.json()["response"]
    except httpx.ConnectError:
        LLM_STATE["off"] = True
        raise RuntimeError(f"Cannot connect to Ollama at {base_url}. Start Ollama or switch provider to Gemini/OpenAI.")


def llm_pick_links(links: list[tuple[str, str]]) -> list[tuple[str, str]]:
    if not links:
        return []
    adum = [(t, u) for t, u in links if "proposition.pl" in u.lower()]
    if adum:
        return adum
    rows = "\n".join(f"{i}|{t[:85]}|{u[-85:]}" for i, (t, u) in enumerate(links[:140]))
    raw = call_llm(
        'Return ONLY JSON {"offers":[indices]} for links opening an INDIVIDUAL PhD/doctoral/CIFRE/MSCA offer. Exclude menus/filters/login.\n' + rows,
        js=True,
        timeout=30,
    )
    r = _extract_json(raw)
    return [links[i] for i in r.get("offers", []) if isinstance(i, int) and 0 <= i < len(links[:140])]


def llm_enrich_offer(o: dict) -> None:
    if not settings().get("use_llm", True):
        return
    try:
        raw = call_llm(
            "Extract facts ONLY from this PhD offer. Reply ONLY JSON:\n"
            f'Allowed categories: {json.dumps(CATEGORIES)}\n'
            '{"is_real_phd_offer":true,"category":"","institution":"","industry_partner":"","supervisor":"","city":"","funding":"","deadline":"YYYY-MM-DD or empty","requires":[]}\n'
            f"TITLE: {o['title']}\nTEXT: {o['description_text'][:4000]}",
            js=True,
            timeout=60,
        )
        r = _extract_json(raw)
        if r.get("is_real_phd_offer") is False:
            o["_reject"] = True
            return
        if r.get("category") in CATEGORIES:
            o["category"] = r["category"]
        for k in ("institution", "industry_partner", "supervisor", "city", "funding"):
            v = str(r.get(k) or "").strip()[:120]
            if v:
                o[k] = v
        dl = str(r.get("deadline") or "").strip()
        if not o.get("deadline") and re.match(r"^20\d\d-\d\d-\d\d$", dl):
            o["deadline"] = dl
        o["requires"] = [str(x).strip()[:40] for x in r.get("requires", []) if str(x).strip()][:12]
    except Exception:
        pass


def score_offer_fast(o: dict, prof: dict, cv_text: str, provider: str = None, model: str = None) -> dict:
    """Calibrated numeric % scoring with deterministic skill-overlap floor so relevant offers never return 0%."""
    active_prof = prof if (prof and prof.get("skills")) else (jload("profile.json", None) or FALLBACK_PROFILE)
    base_chance, base_match, base_reqs = _heuristic_skill_score(o, active_prof, cv_text)

    prompt = (
        "Evaluate PhD admission chance (0-100) and technical skill match (0-100) for a 5th-year Big Data & Data Science Engineering student in Morocco.\n"
        "Calibration rubric:\n"
        "- 75-92: Direct fit (Data Science, Big Data, Data Engineering, ML/AI, Cloud, Databases, Knowledge Graphs)\n"
        "- 45-74: Adjacent quantitative/CS/engineering topic where Python/ML/analytics apply\n"
        "- 10-35: Unrelated field (e.g., pure wet-lab biology, literature, organic chemistry)\n"
        'Reply ONLY valid JSON with integers: {"chance_percent": 78, "match_percent": 84, "requires": ["Python", "ML"]}\n'
        f"CANDIDATE PROFILE: {json.dumps(active_prof)[:1600]}\n"
        f"CANDIDATE CV: {cv_text[:4500]}\n"
        f"PHD OFFER: {o['title']} | Category: {o.get('category','')} | Country: {o.get('country','')} | Program: {o.get('program_type','')}\n"
        f"DESCRIPTION: {o.get('description_text','')[:3200]}"
    )
    try:
        r = _extract_json(call_llm(prompt, js=True, timeout=90, provider_override=provider, model_override=model))
        llm_chance = _extract_int_percent(r.get("chance_percent", r.get("chance", 0)))
        llm_match = _extract_int_percent(r.get("match_percent", r.get("match", 0)))
        reqs = [str(x)[:40] for x in r.get("requires", []) if str(x).strip()][:12] or base_reqs

        # Prevent false 0% on matching domain offers if local model under-calibrated
        final_chance = max(llm_chance, base_chance) if base_chance >= 45 else (llm_chance or base_chance)
        final_match = max(llm_match, base_match) if base_match >= 45 else (llm_match or base_match)
        return {"chance": final_chance, "match": final_match, "requires": reqs}
    except Exception:
        return {"chance": base_chance, "match": base_match, "requires": base_reqs}


def explain_offer_score(o: dict, prof: dict, provider: str = None, model: str = None) -> str:
    """Called ONLY when the user clicks 'Why this %?'."""
    active_prof = prof if (prof and prof.get("skills")) else (jload("profile.json", None) or FALLBACK_PROFILE)
    cv_text = all_cvs_context(6000)
    prompt = (
        f"The candidate received a {o.get('chance', 0)}% acceptance chance (skill match {o.get('match', 0)}%) for this PhD offer.\n"
        "Explain concisely in 4-6 bullet points WHY they received this percentage, covering:\n"
        "- Matching technical skills & projects from their CV\n"
        "- Missing requirements or skill gaps\n"
        "- Non-EU / Morocco visa & funding eligibility factors\n"
        "- Concrete step to maximize acceptance odds\n\n"
        f"PROFILE: {json.dumps(active_prof)[:1800]}\n"
        f"CV:\n{cv_text}\n"
        f"OFFER: {o['title']} ({o.get('institution','')}, {o.get('country','')}, {o.get('program_type','')})\n{o.get('description_text','')[:4000]}"
    )
    return call_llm(prompt, js=False, timeout=120, provider_override=provider, model_override=model)