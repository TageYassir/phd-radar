from __future__ import annotations
"""Autonomous University PhD Portal Discovery Agent."""
import hashlib, json, re, threading, time
from urllib.parse import urljoin, urlparse
import httpx
from bs4 import BeautifulSoup
from .config_store import DB, LOCK, cfg, load_universities, log_outbound, save_cfg
from .llm_providers import LLM_STATE, call_llm
from .scraper import HDR

DISC = {"running": False, "done": 0, "total": 0, "found": 0, "current": "", "log": [], "stop": False}

PORTAL_PATHS = [
    "/phd",
    "/doctorate",
    "/doctoral",
    "/en/phd",
    "/en/doctorate",
    "/en/doctoral",
    "/jobs",
    "/vacancies",
    "/en/jobs",
    "/en/vacancies",
    "/careers",
    "/work-with-us",
    "/fr/doctorat",
    "/doctorat",
    "/karriere",
    "/stellenangebote",
    "/research/phd",
    "/study/phd",
    "/admissions/phd",
]

PORTAL_LINK_RX = re.compile(
    r"\b(?:ph\.?d|doctorat|doctoral|doctorate|vacanc|jobs|career|open\s+positions|stellenangebote|karriere|recrutement|th[eè]se)\b",
    re.I,
)


def _http_get(url: str, timeout: int = 10) -> httpx.Response | None:
    log_outbound(url, "DISCOVER")
    for verify_ssl in (True, False):
        try:
            r = httpx.get(url, headers=HDR, timeout=timeout, follow_redirects=True, verify=verify_ssl)
            if r.status_code < 400 and len(r.text) > 400:
                return r
        except Exception:
            continue
    return None


def discover_university_portal(name: str, country: str, domain: str):
    candidates = []
    seen_urls = set()

    # 1. Scan university homepage links for PhD / Doctoral / Jobs portals
    for prefix in (f"https://www.{domain}", f"https://{domain}"):
        r = _http_get(prefix, timeout=10)
        if r is not None:
            soup = BeautifulSoup(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                tx = a.get_text(" ", strip=True)
                href = urljoin(str(r.url), a["href"]).split("#")[0]
                if domain in urlparse(href).netloc and PORTAL_LINK_RX.search(tx + " " + href):
                    if href not in seen_urls:
                        seen_urls.add(href)
                        candidates.append((tx or href, href))
            break

    # 2. Probe common direct university doctoral/jobs paths
    if len(candidates) < 2:
        for base in (f"https://www.{domain}", f"https://{domain}"):
            for p in PORTAL_PATHS:
                if DISC["stop"]:
                    break
                r = _http_get(base + p, timeout=7)
                if r is not None:
                    final_u = str(r.url)
                    if final_u not in seen_urls:
                        seen_urls.add(final_u)
                        candidates.append((f"{name} ({p})", final_u))
                        if len(candidates) >= 3:
                            break
            if candidates:
                break

    if not candidates:
        return None

    best_url = candidates[0][1]
    if len(candidates) > 1 and not LLM_STATE["off"]:
        try:
            rows = "\n".join(f"{i}|{t[:80]}|{u}" for i, (t, u) in enumerate(candidates[:12]))
            ans = json.loads(
                call_llm(
                    f'Pick the single best URL listing open PhD / doctoral positions or jobs for {name} ({domain}). Reply ONLY JSON {{"index":0}}:\n' + rows,
                    js=True,
                    timeout=20,
                )
            )
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


def start_discovery_thread(limit: int = 150, country_filter: str = "") -> bool:
    if DISC["running"]:
        return False

    def _worker():
        try:
            unis = [u for u in load_universities() if not country_filter or u[1].lower() == country_filter.lower()][:limit]
            DISC.update(running=True, done=0, total=len(unis), found=0, current="Starting...", log=[], stop=False)
            for name, country, domain in unis:
                if DISC["stop"]:
                    break
                DISC["current"] = f"{name} ({domain})"
                try:
                    res = discover_university_portal(name, country, domain)
                    if res:
                        DISC["found"] += 1
                        DISC["log"].insert(0, f"FOUND: {name} -> {res['url']}")
                    else:
                        DISC["log"].insert(0, f"NO PORTAL MATCH: {name} ({domain})")
                except Exception as e:
                    DISC["log"].insert(0, f"ERR: {name} -> {type(e).__name__}: {e}")
                DISC["done"] += 1
                time.sleep(0.15)
        finally:
            DISC["running"] = False
            DISC["current"] = "Finished"

    DISC["running"] = True
    threading.Thread(target=_worker, daemon=True).start()
    return True