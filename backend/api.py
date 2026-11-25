"""FastAPI routes for PhD-Radar (no application tracker, .env aware)."""
import hashlib, json, threading, traceback
from pathlib import Path
from urllib.parse import urlparse
import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from .config_store import (
    DATA, DB, ENV_FILE, LOCK, PRIVACY_LOG, ROOT, VERSION,
    all_cvs_context, cfg, ensure_sources, extract_cv_text,
    jload, jsave, load_universities, offers, put_offer, save_cfg, settings
)
from .discovery import DISC, start_discovery_thread
from .llm_providers import call_llm, explain_offer_score, llm_enrich_offer, score_offer_fast
from .scraper import (
    BR, EMAIL_RX, RUN, browser_session, crawl_job, detail,
    find_deadline, get, ingest_html, make_offer
)

app = FastAPI()
HEALTH = {}


@app.get("/")
def index():
    return FileResponse(ROOT / "index.html")


@app.get("/api/version")
def api_version():
    return {
        "version": VERSION,
        "sources": len(cfg()["sources"]),
        "data_dir": str(DATA),
        "env_loaded": ENV_FILE.exists(),
        "universities": len(load_universities()),
    }


@app.get("/api/diagnostics")
def api_diagnostics():
    s = settings()
    prov = s.get("provider", "ollama")
    ai_status = {"provider": prov, "ok": False, "model": "", "error": "", "env_file": ENV_FILE.exists()}
    if prov == "ollama":
        ai_status["model"] = s.get("model", "gemma4:26b")
        try:
            r = httpx.get(s["ollama"].rstrip("/") + "/api/tags", timeout=4)
            r.raise_for_status()
            models = [m["name"] for m in r.json().get("models", [])]
            ai_status["ok"] = True
            if ai_status["model"] not in models and f"{ai_status['model']}:latest" not in models:
                ai_status["error"] = f"Model '{ai_status['model']}' not pulled in Ollama."
        except Exception as e:
            ai_status["error"] = str(e)
    elif prov == "gemini":
        ai_status["model"] = s.get("gemini_model", "gemini-4-hydrogen")
        ai_status["ok"] = bool(s.get("gemini_key"))
        if not ai_status["ok"]:
            ai_status["error"] = "GEMINI_API_KEY missing in .env or Settings"
    elif prov == "openai":
        ai_status["model"] = s.get("openai_model", "gpt-6.1-sol")
        ai_status["ok"] = bool(s.get("openai_key"))
        if not ai_status["ok"]:
            ai_status["error"] = "OPENAI_API_KEY missing in .env or Settings"

    pw_ok = False
    try:
        import playwright  # noqa: F401
        pw_ok = True
    except ImportError:
        pw_ok = False

    cvs_meta = jload("cvs_text.json", {})
    with LOCK:
        offer_count = DB.execute("SELECT COUNT(*) FROM offers").fetchone()[0]
        contact_count = DB.execute("SELECT COUNT(*) FROM contacts").fetchone()[0]
        seen_count = DB.execute("SELECT COUNT(*) FROM seen").fetchone()[0]

    return {
        "version": VERSION,
        "data_dir": str(DATA),
        "env_loaded": ENV_FILE.exists(),
        "ai": ai_status,
        "playwright_installed": pw_ok,
        "db": {"offers": offer_count, "contacts": contact_count, "seen_urls": seen_count},
        "cvs": [{"file": k, "chars": len(v)} for k, v in cvs_meta.items()],
        "recent_outbound": PRIVACY_LOG[-25:],
    }


@app.post("/api/discover/start")
async def api_discover_start(req: Request):
    b = await req.json() if req.headers.get("content-type", "").startswith("application/json") else {}
    if not start_discovery_thread(int(b.get("limit", 15)), str(b.get("country", ""))):
        return JSONResponse({"error": "Discovery already running"}, 409)
    return {"started": True}


@app.get("/api/discover/status")
def api_discover_status():
    return DISC


@app.get("/api/offers")
def api_offers():
    return offers()


@app.post("/api/offers/reset")
def offers_reset():
    with LOCK:
        DB.execute("DELETE FROM offers")
        DB.execute("DELETE FROM seen")
        DB.commit()
    return {"ok": True}


@app.get("/api/contacts")
def api_contacts():
    with LOCK:
        return [json.loads(r[0]) for r in DB.execute("SELECT j FROM contacts")]


@app.post("/api/crawl/start")
def crawl_start(dry: int = 0):
    if RUN["running"]:
        return JSONResponse({"error": "already running"}, 409)
    RUN.update(running=True, done=0, total=0, new=0, stop=False, sources=[], msg="")
    threading.Thread(target=crawl_job, args=(bool(dry),), daemon=True).start()
    return {"started": True, "new": 0}


@app.get("/api/crawl/status")
def crawl_status():
    return RUN


@app.post("/api/crawl/stop")
def crawl_stop():
    RUN["stop"] = True
    return {"ok": True}


@app.post("/api/sources/reset")
def src_reset():
    ensure_sources(True)
    return api_sources()


@app.get("/api/sources")
def api_sources():
    return [{**s, "health": HEALTH.get(s["id"], "unchecked")} for s in cfg()["sources"]]


@app.post("/api/sources/health")
def api_health():
    for s in cfg()["sources"]:
        try:
            get(s["url"], s.get("fetch", "auto"))
            HEALTH[s["id"]] = "ok"
        except Exception as e:
            HEALTH[s["id"]] = f"broken: {type(e).__name__}"
    return api_sources()


@app.put("/api/sources/{sid}")
async def src_put(sid: str, req: Request):
    c = cfg()
    body = await req.json()
    for s in c["sources"]:
        if s["id"] == sid:
            s["enabled"] = bool(body.get("enabled", True))
    save_cfg(c)
    return {"ok": True}


@app.post("/api/sources")
async def src_add(req: Request):
    url = (await req.json())["url"]
    c = cfg()
    c["sources"].append({
        "id": "custom-" + hashlib.sha1(url.encode()).hexdigest()[:6],
        "country": "Custom",
        "program": urlparse(url).netloc,
        "url": url,
        "kind": "listing",
        "fetch": "auto",
        "enabled": True,
    })
    save_cfg(c)
    return {"ok": True}


@app.post("/api/offers/add")
async def offer_add(req: Request):
    b = await req.json()
    s = {"country": "", "program": "Manual"}
    kws = cfg().get("keywords", [])
    try:
        if b.get("url"):
            url = b["url"]
            d = detail(url)
        else:
            txt = b["text"]
            url = "text:" + hashlib.sha1(txt.encode()).hexdigest()[:12]
            d = {
                "title": txt.strip().splitlines()[0][:140],
                "institution": "",
                "city": "",
                "deadline": find_deadline(txt),
                "funding": "",
                "description_text": txt[:6500],
                "contact_emails": sorted({re.sub(r"\s*(\[at\]|\(at\))\s*", "@", e).lower() for e in EMAIL_RX.findall(txt)}),
            }
        o = make_offer(s, url, d, kws)
        llm_enrich_offer(o)
        put_offer(o)
        return o
    except Exception as e:
        return JSONResponse({"error": str(e)}, 400)


@app.post("/api/browser/open")
async def br_open(req: Request):
    if BR["t"] and BR["t"].is_alive():
        return {"ok": True}
    try:
        import playwright  # noqa: F401
    except ImportError:
        return JSONResponse({"error": "pip install playwright && playwright install chromium"}, 400)
    url = (await req.json()).get("url") or "https://adum.fr/as/ed/proposition_Setab.pl"
    BR["t"] = threading.Thread(target=browser_session, args=(url,), daemon=True)
    BR["t"].start()
    return {"ok": True}


@app.post("/api/browser/capture")
def br_capture():
    if not (BR["t"] and BR["t"].is_alive()):
        return JSONResponse({"error": "open the browser first"}, 400)
    box = {"ev": threading.Event()}
    BR["q"].put(("capture", box))
    if not box["ev"].wait(30):
        return JSONResponse({"error": "timeout"}, 504)
    try:
        return {"added": ingest_html(box["url"], box["html"]), "url": box["url"]}
    except Exception as e:
        return JSONResponse({"error": str(e)}, 400)


@app.post("/api/browser/close")
def br_close():
    BR["q"].put(("close", {}))
    return {"ok": True}


@app.get("/api/profile")
def prof_get():
    return jload("profile.json", None)


@app.put("/api/profile")
async def prof_put(req: Request):
    jsave("profile.json", await req.json())
    return {"ok": True}


@app.get("/api/settings")
def set_get():
    s = settings()
    return {
        **s,
        "gemini_key_set": bool(s.get("gemini_key")),
        "openai_key_set": bool(s.get("openai_key")),
        "gemini_key": "",
        "openai_key": "",
    }


@app.put("/api/settings")
async def set_put(req: Request):
    incoming = await req.json()
    existing = jload("settings.json", {})
    if not incoming.get("gemini_key") and existing.get("gemini_key"):
        incoming["gemini_key"] = existing["gemini_key"]
    if not incoming.get("openai_key") and existing.get("openai_key"):
        incoming["openai_key"] = existing["openai_key"]
    jsave("settings.json", incoming)
    return {"ok": True}


@app.post("/api/cvs")
async def cv_up(file: UploadFile = File(...)):
    fname = Path(file.filename).name
    dest = DATA / "cvs" / fname
    dest.write_bytes(await file.read())
    extracted = extract_cv_text(dest).strip()
    cvs_meta = jload("cvs_text.json", {})
    cvs_meta[fname] = extracted
    jsave("cvs_text.json", cvs_meta)
    return {"ok": True, "name": fname, "chars": len(extracted)}


@app.delete("/api/cvs/{fname}")
def cv_del(fname: str):
    p = DATA / "cvs" / Path(fname).name
    if p.exists():
        p.unlink()
    cvs_meta = jload("cvs_text.json", {})
    cvs_meta.pop(Path(fname).name, None)
    jsave("cvs_text.json", cvs_meta)
    return {"ok": True}


@app.post("/api/score")
async def score(req: Request):
    body = await req.json()
    prof = body.get("profile", {})
    provider = body.get("provider")
    model = body.get("model")
    cv_text = all_cvs_context(5000)
    scored, errors = 0, []
    for o in offers():
        try:
            res = score_offer_fast(o, prof, cv_text, provider=provider, model=model)
            o.update(res)
            put_offer(o)
            scored += 1
        except Exception as e:
            errors.append(f"{o['id']}: {e}")
    return {"ok": True, "scored": scored, "errors": errors[:5]}


@app.post("/api/offers/{oid}/explain")
async def explain(oid: str, req: Request):
    try:
        body = await req.json()
        prof = body.get("profile", {})
        o = next((x for x in offers() if x["id"] == oid), None)
        if not o:
            return JSONResponse({"error": "Offer not found in backend DB."}, 404)
        text = explain_offer_score(o, prof, provider=body.get("provider"), model=body.get("model"))
        o["score_explanation"] = text
        put_offer(o)
        return {"explanation": text}
    except Exception as e:
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, 502)


@app.post("/api/offers/{oid}/report")
async def report(oid: str, req: Request):
    try:
        body = await req.json()
        prof = body.get("profile", {})
        o = next((x for x in offers() if x["id"] == oid), None)
        if not o:
            return JSONResponse({"error": "Offer not found in backend DB."}, 404)
        text = ""
        if str(o.get("url", "")).startswith("http"):
            try:
                text = BeautifulSoup(get(o["url"], "auto"), "html.parser").get_text(" ", strip=True)[:12000]
            except Exception:
                text = ""
        if len(text) < 200:
            text = o.get("description_text", "")
        cv_text = all_cvs_context(8000)
        md = call_llm(
            "Write a structured Markdown report for this PhD offer comparing it against the candidate's profile and CV(s):\n"
            "1. Summary & Research Topic\n2. Fit Analysis & Chance %\n3. Strengths & Skill Gaps\n"
            "4. Visa & Eligibility (non-EU candidate in Morocco)\n5. Funding & Contract\n6. Go / No-Go & Next Actions\n"
            "Cite short direct quotes from the offer or CV.\n"
            f"PROFILE: {json.dumps(prof)[:2000]}\nCV:\n{cv_text}\nOFFER: {o.get('title','')}\nPAGE:\n{text[:9000]}",
            js=False,
            timeout=240,
            provider_override=body.get("provider"),
            model_override=body.get("model"),
        )
        return {"markdown": md}
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, 502)
