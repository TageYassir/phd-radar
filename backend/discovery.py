"""Autonomous University PhD Portal Discovery Agent reading config/universities.yaml."""
import hashlib, json, threading, time
from urllib.parse import quote_plus
import httpx
from bs4 import BeautifulSoup
from .config_store import DB, LOCK, cfg, load_universities, log_outbound, save_cfg
from .llm_providers import LLM_STATE, call_llm
from .scraper import HDR, RUN

DISC = {"running": False, "done": 0, "total": 0, "found": 0, "current": "", "log": []}
PORTAL_PATHS = ["/phd", "/doctorate", "/doctoral", "/jobs", "/vacancies", "/en/phd", "/en/jobs", "/fr/doctorat", "/karriere/promotion"]


def discover_university_portal(name: str, country: str, domain: str):
    candidates = []
    q = quote_plus(f'site:{domain} (PhD OR doctoral OR "propositions de thèse" OR "offres de thèse" OR Stellenangebote)')
    search_url = f"https://html.duckduckgo.com/html/?q={q}"
    try:
        log_outbound(search_url, "DISCOVER")
        r = httpx.get(search_url, headers=HDR, timeout=12, follow_redirects=True)
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a.result__a"):
            href = a.get("href", "")
            tx = a.get_text(" ", strip=True)
            if domain in href and href.startswith("http"):
                candidates.append((tx, href))
    except Exception:
        pass

    if not candidates:
        base = f"https://www.{domain}"
        for p in PORTAL_PATHS[:5]:
            u = base + p
            try:
                log_outbound(u, "DISCOVER_PROBE")
                resp = httpx.head(u, headers=HDR, timeout=6, follow_redirects=True)
                if resp.status_code < 400:
                    candidates.append((f"{name} ({p})", str(resp.url)))
                    break
            except Exception:
                continue

    if not candidates:
        return None

    best_url = candidates[0][1]
    if len(candidates) > 1 and not LLM_STATE["off"]:
        try:
            rows = "\n".join(f"{i}|{t[:80]}|{u}" for i, (t, u) in enumerate(candidates[:10]))
            ans = json.loads(call_llm(f'Pick the single best URL listing open PhD positions for {name} ({domain}). Reply ONLY JSON {{"index":0}}:\n' + rows, js=True, timeout=20))
            idx = int(ans.get("index", 0))
            if 0 <= idx < len(candidates):
                best_url = candidates[idx][1]
        except Exception:
            pass

    entry = {
        "id": "uni-" + hashlib.sha1(domain.encode()).hexdigest()[:6],
        "kind": "listing",
        "fetch": "auto",
        "country": country,
        "program": f"{name} PhD Portal",
        "url": best_url,
        "max_detail": 30,
        "enabled": True,
    }
    with LOCK:
        DB.execute("INSERT OR REPLACE INTO uni_portals VALUES(?,?)", (domain, json.dumps(entry, ensure_ascii=False)))
        DB.commit()

    c = cfg()
    if not any(s.get("id") == entry["id"] or s.get("url") == best_url for s in c["sources"]):
        c["sources"].append(entry)
        save_cfg(c)
    return entry


def start_discovery_thread(limit: int = 20, country_filter: str = "") -> bool:
    if DISC["running"]:
        return False

    def _worker():
        unis = [u for u in load_universities() if not country_filter or u[1].lower() == country_filter.lower()][:limit]
        DISC.update(running=True, done=0, total=len(unis), found=0, current="", log=[])
        for name, country, domain in unis:
            if RUN["stop"]:
                break
            DISC["current"] = f"{name} ({domain})"
            try:
                res = discover_university_portal(name, country, domain)
                if res:
                    DISC["found"] += 1
                    DISC["log"].insert(0, f"FOUND: {name} -> {res['url']}")
                else:
                    DISC["log"].insert(0, f"SKIP: {name}")
            except Exception as e:
                DISC["log"].insert(0, f"ERR: {name} -> {e}")
            DISC["done"] += 1
            time.sleep(0.8)
        DISC["running"] = False
        DISC["current"] = "Done"

    threading.Thread(target=_worker, daemon=True).start()
    return True
