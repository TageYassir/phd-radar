"""HTTP & Playwright fetching, robots.txt enforcement, HTML detail extraction, and Assisted Browser session."""
import hashlib, json, queue, re, threading, time
import urllib.robotparser as rp
from urllib.parse import urldefrag, urljoin, urlparse
import httpx
from bs4 import BeautifulSoup
from .config_store import (
    DB, DATA, LOCK, cfg, classify_heuristic, jload, jsave,
    log_outbound, norm, offer_fingerprint, offers, put_offer
)
from .llm_providers import LLM_STATE, llm_enrich_offer, llm_pick_links

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
HDR = {
    "User-Agent": UA,
    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8,de;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

PHD_RX = re.compile(
    r"\b(?:ph\.?d|doctorat|doctoral|doctorate|cifre|erhvervsphd|n[æa]rings-?ph\.?d|baekeland|win4doc|msca|marie\s+sk[łl]odowska|studentship|th[eè]se|doktorand|proposition\s+de\s+th[eè]se|sujet\s+de\s+th[eè]se)\b",
    re.I,
)
MENU_NOISE_RX = re.compile(
    r"^(?:login|sign\s*in|register|cookie|privacy|terms|contact|about|home|menu|search|filter|next|previous|page\s*\d+|skip\s*to|connexion|accueil)$",
    re.I,
)
EMAIL_RX = re.compile(r"[\w.+-]+\s*(?:@|\[at\]|\(at\))\s*[\w-]+(?:\.[\w-]+)+", re.I)
SUPERVISOR_RX = re.compile(
    r"(?:directeur\s+de\s+th[eè]se|encadrant|supervisor|contact|responsable|prof\.?|dr\.?)\s*[:\-]?\s*([A-ZÀ-Ý][a-zà-ÿ'-]+(?:\s+[A-ZÀ-Ý][a-zà-ÿ'-]+){1,3})",
    re.I,
)
AMT_RX = re.compile(
    r"(?:€|EUR|CHF|GBP|£|DKK|NOK|SEK)\s*[\d,.\s]+(?:/\s*(?:year|yr|month|mo|annum|an|mois))?|[\d,.\s]+\s*(?:€|EUR|CHF|GBP|DKK|NOK|SEK)\s*(?:/\s*(?:year|yr|month|mo|annum|an|mois)|gross|brut|net)?",
    re.I,
)
FUNDING_RX = re.compile(
    r"(?:(?:€|EUR|CHF|GBP|£|DKK|NOK|SEK)\s*[\d,.\s]+(?:/\s*(?:year|yr|month|mo|annum|an|mois))?|[\d,.\s]+\s*(?:€|EUR|CHF|GBP|DKK|NOK|SEK)\s*(?:/\s*(?:year|yr|month|mo|annum|an|mois)|gross|brut|net)?)"
    r"|(?:\b(?:fully\s+funded|contrat\s+doctoral|cifre|financement\s+acquis|stipend|salary)\b)",
    re.I,
)
MONTHS = {
    "jan": 1, "feb": 2, "fev": 2, "mar": 3, "apr": 4, "avr": 4, "abr": 4,
    "may": 5, "mai": 5, "mei": 5, "jun": 6, "jul": 7, "aug": 8, "aou": 8,
    "ago": 8, "sep": 9, "oct": 10, "okt": 10, "nov": 11, "dec": 12, "dez": 12,
}
DLKEY = r"(?:deadline|closes?|closing|apply\s*by|apply\s*before|date\s*limite|limite\s*de\s*candidature|cloture|fecha\s*limite|plazo|bewerbungsfrist|frist|sluitingsdatum|uiterlijk|jusqu'au|valid\s*through)"

BLOCK = threading.Lock()
ROBOTS = {}
RUN = {"msg": "", "running": False, "done": 0, "total": 0, "new": 0, "stop": False, "sources": []}
BR = {"q": queue.Queue(), "t": None}


def oid(u: str) -> str:
    return hashlib.sha1(u.encode()).hexdigest()[:10]


def allowed(url: str) -> bool:
    u = urlparse(url)
    base = f"{u.scheme}://{u.netloc}"
    if base not in ROBOTS:
        r = rp.RobotFileParser()
        try:
            log_outbound(base + "/robots.txt", "ROBOTS")
            txt = httpx.get(base + "/robots.txt", headers=HDR, timeout=8, follow_redirects=True).text
            r.parse(txt.splitlines())
        except Exception:
            r.parse([])
        ROBOTS[base] = r
    return ROBOTS[base].can_fetch("*", url) and ROBOTS[base].can_fetch("PhD-Radar", url)


def bget(url: str) -> str:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("needs Playwright (pip install playwright && playwright install chromium)")
    log_outbound(url, "BROWSER")
    with BLOCK, sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            str(DATA / "browser"),
            headless=True,
            user_agent=UA,
            viewport={"width": 1366, "height": 900},
        )
        try:
            pg = ctx.new_page()
            pg.goto(url, wait_until="domcontentloaded", timeout=25000)
            try:
                pg.wait_for_load_state("networkidle", timeout=6000)
            except Exception:
                pg.wait_for_timeout(2500)
            return pg.content()
        finally:
            ctx.close()


def get(url: str, mode: str = "auto") -> str:
    if RUN["stop"]:
        raise RuntimeError("Stopped by user")
    if not allowed(url):
        raise PermissionError("blocked by robots.txt (use Assisted browser)")
    if mode != "browser":
        for i in range(2):
            if RUN["stop"]:
                raise RuntimeError("Stopped by user")
            try:
                log_outbound(url, "HTTP")
                r = httpx.get(url, headers=HDR, timeout=15, follow_redirects=True)
                r.raise_for_status()
                html = r.text
                if mode == "auto" and len(BeautifulSoup(html, "html.parser").find_all("a", href=True)) < 6:
                    return bget(url)
                return html
            except httpx.HTTPStatusError as e:
                if e.response.status_code in (403, 406, 429, 503) and mode == "auto":
                    break
                raise
            except Exception:
                if i == 1:
                    if mode == "auto":
                        break
                    raise
                time.sleep(1)
    return bget(url)


def find_deadline(text: str):
    import datetime
    t = norm(text)
    for m in re.finditer(DLKEY, t):
        w = t[m.end() : m.end() + 80]
        x = re.search(r"(20\d\d)-(\d\d)-(\d\d)", w)
        if x:
            return x.group(0)
        x = re.search(r"\b(\d{1,2})[/.](\d{1,2})[/.](20\d\d)\b", w)
        if x:
            return f"{x.group(3)}-{int(x.group(2)):02d}-{int(x.group(1)):02d}"
        x = re.search(r"\b(\d{1,2})(?:st|nd|rd|th|er)?\s+(?:de\s+)?([a-z]{3})[a-z]*\.?(?:\s+(?:de\s+)?(20\d\d))?", w)
        if x and x.group(2) in MONTHS:
            d, mo, y = int(x.group(1)), MONTHS[x.group(2)], x.group(3)
            now = datetime.date.today()
            try:
                dt = datetime.date(int(y) if y else now.year, mo, d)
                if not y and (now - dt).days > 30:
                    dt = dt.replace(year=now.year + 1)
                return dt.isoformat()
            except ValueError:
                pass
    return None


def jobposting(soup):
    for sc in soup.find_all("script", type="application/ld+json"):
        try:
            d = json.loads(sc.string or "")
        except Exception:
            continue
        for x in (d if isinstance(d, list) else d.get("@graph", [d])):
            if isinstance(x, dict) and "JobPosting" in str(x.get("@type")):
                return x
    return None


def links_of(html: str, base: str, minlen: int = 4) -> list[tuple[str, str]]:
    seen, out = set(), []
    for a in BeautifulSoup(html, "html.parser").find_all("a", href=True):
        h = urldefrag(urljoin(base, a["href"]))[0]
        tx = a.get_text(" ", strip=True)
        if not h.startswith("http") or h in seen or len(tx) < minlen:
            continue
        if MENU_NOISE_RX.match(tx) or any(x in h.lower() for x in ("login", "signin", "signup", "cookie", "privacy", "mailto:", "javascript:")):
            continue
        seen.add(h)
        out.append((tx, h))
    return out


def detail(url: str, mode: str = "auto", html: str = None) -> dict:
    soup = BeautifulSoup(html or get(url, mode), "html.parser")
    jp = jobposting(soup) or {}
    for tg in soup(["script", "style", "nav", "footer", "aside"]):
        tg.decompose()
    text = soup.get_text(" ", strip=True)
    h1 = soup.find("h1") or soup.find("h2")
    org = jp.get("hiringOrganization")
    org = org.get("name", "") if isinstance(org, dict) else str(org or "")
    loc = jp.get("jobLocation")
    loc = loc[0] if isinstance(loc, list) and loc else loc
    addr = loc.get("address", {}) if isinstance(loc, dict) else {}
    desc = BeautifulSoup(jp.get("description", "") or "", "html.parser").get_text(" ", strip=True) or text
    vt = str(jp.get("validThrough") or "")[:10]
    f_match = AMT_RX.search(desc[:3500]) or FUNDING_RX.search(desc[:3500])
    sup_match = SUPERVISOR_RX.search(desc[:3500])
    emails = sorted({
        re.sub(r"\s*(\[at\]|\(at\))\s*", "@", e).lower()
        for e in EMAIL_RX.findall(text + " " + desc)
        if not e.lower().endswith((".png", ".jpg", ".svg", ".webp", "example.com"))
    })
    return {
        "title": (jp.get("title") or (h1.get_text(" ", strip=True) if h1 else "")).strip(),
        "institution": org.strip(),
        "city": addr.get("addressLocality", "").strip() if isinstance(addr, dict) else "",
        "deadline": vt if re.match(r"20\d\d-\d\d-\d\d", vt) else find_deadline(text),
        "funding": f_match.group(0).strip() if f_match else "",
        "supervisor": sup_match.group(1).strip() if sup_match else "",
        "description_text": desc[:6500],
        "contact_emails": emails,
    }


def make_offer(s: dict, url: str, d: dict, kws: list[str]) -> dict:
    low = norm(d["title"] + " " + d["description_text"])
    return {
        "v": 2,
        "verified": True,
        "id": oid(url),
        **d,
        "industry_partner": "",
        "country": s.get("country", ""),
        "program_type": s.get("program", ""),
        "category": classify_heuristic(d["title"] + " " + d["description_text"][:1000]),
        "keywords": [k for k in kws if re.search(r"\b" + re.escape(norm(k)), low)],
        "chance": 0,
        "match": 0,
        "source": s.get("program", ""),
        "url": url,
        "requires": [],
    }


def pick_links(links: list[tuple[str, str]]) -> list[tuple[str, str]]:
    heuristic = [
        (t, u)
        for t, u in links
        if PHD_RX.search(t + " " + u)
        or any(k in u.lower() for k in ("/job/", "/jobs/", "/offre/", "id_offre", "/ad/", "/project/", "/vacancy/", "proposition.pl"))
    ][:60]
    if LLM_STATE["off"]:
        return heuristic
    try:
        picked = llm_pick_links(links)
        return picked or heuristic
    except Exception:
        return heuristic


def process(s: dict, urls: list[str], kws: list[str], known: set[str], mode: str = "auto", limit: int = 100, dry: bool = False, html_fetcher=None) -> int:
    n = 0
    existing_fps = {offer_fingerprint(x.get("title", ""), x.get("institution", "")) for x in offers()}
    for u in [u for u in urls if oid(u) not in known][:limit]:
        if RUN["stop"]:
            break
        known.add(oid(u))
        time.sleep(0.7)
        try:
            raw_html = html_fetcher(u) if html_fetcher else None
            d = detail(u, mode, html=raw_html)
        except Exception:
            continue
        o = make_offer(s, u, d, kws)
        fp = offer_fingerprint(o["title"], o["institution"])
        has_phd_signal = bool(PHD_RX.search(norm(o["title"] + " " + o["description_text"][:1800]))) or ("proposition.pl" in u.lower())
        if o["title"] and len(o["title"]) > 8 and has_phd_signal and fp not in existing_fps:
            llm_enrich_offer(o)
            if o.pop("_reject", False):
                with LOCK:
                    DB.execute("INSERT OR IGNORE INTO seen VALUES(?)", (oid(u),))
                    DB.commit()
                continue
            existing_fps.add(fp)
            if not dry:
                put_offer(o)
            n += 1
        else:
            with LOCK:
                DB.execute("INSERT OR IGNORE INTO seen VALUES(?)", (oid(u),))
                DB.commit()
    return n


def listing(s: dict, kws: list[str], known: set[str], dry: bool = False) -> tuple[int, int]:
    rx = re.compile(s["detail"]) if s.get("detail") else None
    cands = {}
    mode = s.get("fetch", "auto")
    for u in s.get("urls") or [s["url"]]:
        if RUN["stop"]:
            break
        html = get(u, mode)
        page_links = links_of(html, u, 4)
        if rx:
            for _, h in page_links:
                m = rx.search(h)
                if m:
                    cands.setdefault(h[: m.end()], 1)
        if not cands:
            for _, h in pick_links(page_links):
                cands.setdefault(h, 1)
        time.sleep(0.7)
    return process(s, list(cands), kws, known, mode, s.get("max_detail", 100), dry=dry), len(cands)


def watch(s: dict) -> str:
    html = get(s["url"], s.get("fetch", "auto"))
    txt = norm(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
    h = hashlib.sha1(txt.encode()).hexdigest()
    prev = jload("watch.json", {})
    note = "first visit" if s["id"] not in prev else ("changed since last visit" if prev[s["id"]] != h else "unchanged")
    prev[s["id"]] = h
    jsave("watch.json", prev)
    return note


def _crawl_job(dry: bool = False) -> None:
    c = cfg()
    en = [s for s in c["sources"] if s.get("enabled", True)]
    known = {o["id"] for o in offers()} | {r[0] for r in DB.execute("SELECT id FROM seen")}
    LLM_STATE["off"] = False
    RUN.update(
        running=True,
        done=0,
        total=len(en),
        new=0,
        stop=False,
        sources=[{"id": s["id"], "status": "waiting"} for s in en],
    )
    for i, s in enumerate(en):
        if RUN["stop"]:
            break
        st = RUN["sources"][i]
        st["status"] = "running"
        try:
            if s.get("kind", "watch") == "watch":
                st.update(status="ok", found=0, note="guide/call: " + watch(s))
            else:
                n, seen = listing(s, c.get("keywords", []), known, dry=dry)
                st.update(status="ok", found=n, note=f"{seen} links" if seen else "0 matching links")
                RUN["new"] += n
        except Exception as e:
            err = str(e)
            if "needs playwright" in err.lower():
                st.update(status="skipped", note="Install Playwright or use Assisted Browser")
            elif "blocked by robots.txt" in err.lower():
                st.update(status="skipped", note="Blocked by robots.txt -> open in Assisted Browser")
            elif "stopped by user" in err.lower():
                st.update(status="skipped", note="Stopped")
            else:
                st.update(status="error", error=f"{type(e).__name__}: {err}"[:200])
        RUN["done"] += 1
        time.sleep(0.3)
    RUN["running"] = False


def crawl_job(dry: bool = False) -> None:
    try:
        _crawl_job(dry)
    except Exception as e:
        RUN["msg"] = f"{type(e).__name__}: {e}"[:300]
    finally:
        RUN["running"] = False


def browser_session(url: str) -> None:
    from playwright.sync_api import sync_playwright
    with BLOCK, sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(DATA / "browser"), headless=False, viewport={"width": 1366, "height": 900})
        (ctx.pages[0] if ctx.pages else ctx.new_page()).goto(url)
        while ctx.pages:
            try:
                cmd, payload = BR["q"].get(timeout=1)
            except queue.Empty:
                continue
            if cmd == "close":
                break
            if cmd == "goto_and_get":
                target_url, box = payload
                pg = ctx.pages[-1]
                try:
                    pg.goto(target_url, wait_until="domcontentloaded", timeout=15000)
                    box.update(url=pg.url, html=pg.content())
                except Exception as e:
                    box.update(error=str(e))
                box["ev"].set()
            else:
                pg = ctx.pages[-1]
                payload.update(url=pg.url, html=pg.content())
                payload["ev"].set()
        ctx.close()


def ingest_html(url: str, html: str) -> int:
    host = urlparse(url).netloc
    s = {"country": "France" if "adum.fr" in host else "", "program": f"Assisted ({host})"}
    kws = cfg().get("keywords", [])
    known = {o["id"] for o in offers()}
    soup = BeautifulSoup(html, "html.parser")
    all_links = links_of(html, url, 4)

    adum_links = [h for _, h in all_links if "proposition.pl" in h.lower()]
    if adum_links:
        def fetch_via_active_browser(target_url: str):
            if BR["t"] and BR["t"].is_alive():
                box = {"ev": threading.Event()}
                BR["q"].put(("goto_and_get", (target_url, box)))
                if box["ev"].wait(20):
                    return box.get("html")
            return None
        return process(s, adum_links, kws, known, "browser", limit=150, html_fetcher=fetch_via_active_browser)

    picks = [] if jobposting(soup) else pick_links(all_links)
    if len(picks) >= 2:
        return process(s, [h for _, h in picks], kws, known, "auto", limit=100)
    o = make_offer(s, url, detail(url, html=html), kws)
    llm_enrich_offer(o)
    put_offer(o)
    return 1
