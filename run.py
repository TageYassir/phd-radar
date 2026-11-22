"""PhD-Radar backend (Phase 1). Start: python run.py"""
import hashlib, json, re, threading, time, webbrowser, urllib.robotparser as rp
import sqlite3
from pathlib import Path
from urllib.parse import urljoin, urlparse, urldefrag
import httpx, yaml, feedparser, uvicorn
from bs4 import BeautifulSoup
from fastapi import FastAPI, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse

ROOT = Path(__file__).parent
DATA = ROOT / "data"; (DATA / "cvs").mkdir(parents=True, exist_ok=True)
SRC = ROOT / "config" / "sources.yaml"
UA = "PhD-Radar/0.1 (personal research tool; respects robots.txt)"
PHD = re.compile(r"ph\.?d|doctor|doctorat|doctoral|cifre|promotion|th[eè]se|thesis|doktor|studentship|baekeland|msca", re.I)
DB = sqlite3.connect(DATA / "radar.db", check_same_thread=False)
DB.execute("create table if not exists offers(id text primary key, j text)")
LOCK = threading.Lock()
RUN = {"running": False, "done": 0, "total": 0, "new": 0, "stop": False, "sources": []}
HEALTH, ROBOTS = {}, {}

def offers(): return [json.loads(r[0]) for r in DB.execute("select j from offers")]
def put(o):
    with LOCK:
        DB.execute("insert or replace into offers values(?,?)", (o["id"], json.dumps(o))); DB.commit()
def jload(name, default):
    p = DATA / name
    return json.loads(p.read_text("utf-8")) if p.exists() else default
def jsave(name, obj): (DATA / name).write_text(json.dumps(obj, ensure_ascii=False, indent=1), "utf-8")
def cfg(): return yaml.safe_load(SRC.read_text("utf-8"))
def settings():
    return {"ollama": "http://localhost:11434", "model": "qwen2.5:14b", **jload("settings.json", {})}

# ---------- fetching (robots.txt, retries, user-agent) ----------
def allowed(url):
    u = urlparse(url); base = f"{u.scheme}://{u.netloc}"
    if base not in ROBOTS:
        r = rp.RobotFileParser()
        try: r.parse(httpx.get(base + "/robots.txt", headers={"User-Agent": UA}, timeout=10).text.splitlines())
        except Exception: r.parse([])
        ROBOTS[base] = r
    return ROBOTS[base].can_fetch(UA, url)

def get(url):
    if not allowed(url): raise PermissionError("blocked by robots.txt")
    for i in range(3):
        try:
            r = httpx.get(url, headers={"User-Agent": UA}, timeout=25, follow_redirects=True)
            r.raise_for_status(); return r.text
        except Exception:
            if i == 2: raise
            time.sleep(2 ** i)

# ---------- parsing ----------
def find_date(text):
    m = re.search(r"(20\d\d)-(\d\d)-(\d\d)", text)
    if m: return m.group(0)
    m = re.search(r"\b(\d{1,2})[/.](\d{1,2})[/.](20\d\d)\b", text)
    return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}" if m else None

def parse(s, html, kws):
    items = []
    if s.get("parser") == "rss":
        for e in feedparser.parse(html).entries:
            items.append((e.get("title", ""), e.get("link", ""), re.sub("<[^>]+>", " ", e.get("summary", ""))))
    else:
        for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
            t = a.get_text(" ", strip=True)
            if len(t) > 25:
                ctx = a.parent.get_text(" ", strip=True)[:600] if a.parent else t
                items.append((t, urljoin(s["url"], a["href"]), ctx))
    out = []
    for t, u, d in items:
        u = urldefrag(u)[0].rstrip("/")
        if not u.startswith("http") or not PHD.search(t + " " + d): continue
        low = (t + " " + d).lower()
        out.append({"id": hashlib.sha1(u.encode()).hexdigest()[:10], "title": t, "institution": "", "industry_partner": "",
                    "country": s["country"], "city": "", "program_type": s["program"], "funding": "", "deadline": find_date(d),
                    "keywords": [k for k in kws if k.lower() in low], "chance": 0, "match": 0, "source": s["program"],
                    "url": u, "description_text": d, "requires": []})
    return out

# ---------- crawl job ----------
def crawl_job(dry):
    c = cfg(); en = [s for s in c["sources"] if s.get("enabled", True)]
    known = {o["id"] for o in offers()}
    RUN.update(running=True, done=0, total=len(en), new=0, stop=False,
               sources=[{"id": s["id"], "status": "waiting"} for s in en])
    for i, s in enumerate(en):
        if RUN["stop"]: break
        st = RUN["sources"][i]; st["status"] = "running"
        try:
            n = 0
            for o in parse(s, get(s["url"]), c.get("keywords", [])):
                if o["id"] in known: continue
                known.add(o["id"]); n += 1
                if not dry: put(o)
            st.update(status="ok", found=n); RUN["new"] += n
        except Exception as e:
            st.update(status="error", error=f"{type(e).__name__}: {e}"[:200])
        RUN["done"] += 1; time.sleep(1)
    RUN["running"] = False

# ---------- Ollama ----------
def llm(prompt, js=False):
    s = settings()
    body = {"model": s["model"], "prompt": prompt, "stream": False, "options": {"temperature": 0, "seed": 42}}
    if js: body["format"] = "json"
    return httpx.post(s["ollama"] + "/api/generate", json=body, timeout=600).json()["response"]

def page_text(url):
    return BeautifulSoup(get(url), "html.parser").get_text(" ", strip=True)[:12000]

# ---------- API ----------
app = FastAPI()
@app.get("/")
def index(): return FileResponse(ROOT / "index.html")
@app.get("/api/offers")
def api_offers(): return offers()

@app.post("/api/crawl/start")
def crawl_start(dry: int = 0):
    if RUN["running"]: return JSONResponse({"error": "already running"}, 409)
    threading.Thread(target=crawl_job, args=(bool(dry),), daemon=True).start()
    return {"started": True}
@app.get("/api/crawl/status")
def crawl_status(): return RUN
@app.post("/api/crawl/stop")
def crawl_stop(): RUN["stop"] = True; return {"ok": True}

@app.get("/api/sources")
def api_sources():
    return [{**s, "health": HEALTH.get(s["id"], "unchecked")} for s in cfg()["sources"]]
@app.post("/api/sources/health")
def api_health():
    for s in cfg()["sources"]:
        try: get(s["url"]); HEALTH[s["id"]] = "ok"
        except Exception: HEALTH[s["id"]] = "broken"
    return api_sources()
@app.put("/api/sources/{sid}")
async def src_put(sid: str, req: Request):
    c = cfg(); body = await req.json()
    for s in c["sources"]:
        if s["id"] == sid: s["enabled"] = bool(body.get("enabled", True))
    SRC.write_text(yaml.safe_dump(c, allow_unicode=True, sort_keys=False), "utf-8"); return {"ok": True}
@app.post("/api/sources")
async def src_add(req: Request):
    url = (await req.json())["url"]; c = cfg()
    c["sources"].append({"id": "custom-" + hashlib.sha1(url.encode()).hexdigest()[:6], "country": "Custom",
                         "program": urlparse(url).netloc, "url": url, "parser": "static", "enabled": True})
    SRC.write_text(yaml.safe_dump(c, allow_unicode=True, sort_keys=False), "utf-8"); return {"ok": True}

@app.get("/api/profile")
def prof_get(): return jload("profile.json", None)
@app.put("/api/profile")
async def prof_put(req: Request): jsave("profile.json", await req.json()); return {"ok": True}
@app.put("/api/settings")
async def set_put(req: Request): jsave("settings.json", await req.json()); return {"ok": True}
@app.post("/api/cvs")
async def cv_up(file: UploadFile = File(...)):
    (DATA / "cvs" / Path(file.filename).name).write_bytes(await file.read()); return {"ok": True}

@app.post("/api/score")
async def score(req: Request):
    prof = (await req.json()).get("profile", {})
    for o in offers():
        try:
            r = json.loads(llm(f"""Compare this PhD offer with the candidate. Reply ONLY JSON:
{{"match_percent":0-100,"chance_percent":0-100,"matched_skills":[],"missing_skills":[],"red_flags":[],"reason":""}}
Candidate is non-EU, based in Morocco. Only use facts present below.
CANDIDATE: {json.dumps(prof)[:3000]}
OFFER: {o['title']} | {o['program_type']} | {o['description_text']}""", True))
            o.update(chance=int(r["chance_percent"]), match=int(r["match_percent"]),
                     requires=r.get("matched_skills", []) + r.get("missing_skills", []))
            put(o)
        except Exception: continue
    return {"ok": True}

@app.post("/api/offers/{oid}/report")
async def report(oid: str, req: Request):
    prof = (await req.json()).get("profile", {})
    o = next((x for x in offers() if x["id"] == oid), None)
    if not o: return JSONResponse({"error": "unknown offer"}, 404)
    try: text = page_text(o["url"])
    except Exception: text = o["description_text"]
    md = llm(f"""Write a Markdown report for this PhD offer: summary, fit analysis, chance %, strengths, gaps,
eligibility/visa notes for a Morocco-based non-EU candidate, funding quality, required documents, risks, go/no-go, next actions.
Every claim must cite a quote from the page. If something is missing, write "not stated".
CANDIDATE: {json.dumps(prof)[:3000]}
PAGE: {text}""")
    return {"markdown": md}

@app.post("/api/send")
def send(): return JSONResponse({"error": "email sending arrives in Phase 4"}, 501)

if __name__ == "__main__":
    threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:8765")).start()
    uvicorn.run(app, host="127.0.0.1", port=8765)