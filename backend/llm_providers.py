"""Unified AI client (Ollama, Google Gemini API, OpenAI API) + fast numeric % scoring & on-demand explanations."""
import json, re
import httpx
from .config_store import CATEGORIES, all_cvs_context, settings

LLM_STATE = {"off": False}


def _extract_json(raw: str) -> dict:
    raw = raw.strip()
    m = re.search(r"\{.*\}", raw, re.S)
    return json.loads(m.group(0) if m else raw)



def _extract_int_percent(val, default: int = 0) -> int:
    if isinstance(val, (int, float)):
        return max(0, min(100, int(round(val))))
    s = str(val).strip().lower()
    m = re.search(r"(-?\d{1,3})", s)
    if m:
        return max(0, min(100, int(m.group(1))))
    # Fallback mapping if a local model still outputs words
    word_map = {"very high": 88, "high": 78, "good": 72, "medium": 55, "mid": 55, "moderate": 52, "low": 28, "very low": 15}
    for k, num in word_map.items():
        if k in s:
            return num
    return default


def call_llm(prompt: str, js: bool = False, timeout: int = 180, provider_override: str = None, model_override: str = None) -> str:
    s = settings()
    provider = (provider_override or s.get("provider") or "ollama").lower()

    if provider == "gemini":
        api_key = s.get("gemini_key", "").strip()
        if not api_key:
            raise RuntimeError("Gemini API key is missing. Add it in Settings.")
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
            raise RuntimeError("OpenAI API key is missing. Add it in Settings.")
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

    # Default: local Ollama
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
        raise RuntimeError(f"Cannot connect to Ollama at {base_url}. Start Ollama or switch provider to Gemini/OpenAI in Settings.")


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
    prompt = (
        "Score acceptance probability (0-100 integer) and skill match (0-100 integer) for a non-EU Big Data Engineering student in Morocco.\n"
        'Reply ONLY compact JSON with integers (NO words like mid/low): {"chance_percent":75,"match_percent":80,"requires":["skill1","skill2"]}\n'
        f"PROFILE: {json.dumps(prof)[:1500]}\n"
        f"CV: {cv_text[:4500]}\n"
        f"OFFER: {o['title']} | {o.get('country','')} | {o.get('program_type','')} | {o['description_text'][:3000]}"
    )
    r = _extract_json(call_llm(prompt, js=True, timeout=90, provider_override=provider, model_override=model))
    return {
        "chance": _extract_int_percent(r.get("chance_percent", r.get("chance", 0))),
        "match": _extract_int_percent(r.get("match_percent", r.get("match", 0))),
        "requires": [str(x)[:40] for x in r.get("requires", [])][:12],
    }


def explain_offer_score(o: dict, prof: dict, provider: str = None, model: str = None) -> str:
    cv_text = all_cvs_context(6000)
    prompt = (
        f"The candidate was given a {o.get('chance', 0)}% acceptance chance (skill match {o.get('match', 0)}%) for this PhD offer.\n"
        "Explain concisely in 4-6 bullet points WHY they received this exact percentage, covering:\n"
        "- Matching technical skills & projects from their CV\n"
        "- Missing requirements or gaps\n"
        "- Non-EU / Morocco visa & funding eligibility factors\n"
        "- Concrete step to raise their chances\n\n"
        f"PROFILE: {json.dumps(prof)[:1800]}\n"
        f"CV:\n{cv_text}\n"
        f"OFFER: {o['title']} ({o.get('institution','')}, {o.get('country','')}, {o.get('program_type','')})\n{o['description_text'][:4000]}"
    )
    return call_llm(prompt, js=False, timeout=120, provider_override=provider, model_override=model)
