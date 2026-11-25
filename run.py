"""PhD-Radar local backend v0.5.0. Run: .\.venv\Scripts\python.exe run.py"""
import datetime, hashlib, json, queue, re, shutil, sqlite3, threading, time, traceback, unicodedata, webbrowser
import urllib.robotparser as rp
from pathlib import Path
from urllib.parse import quote_plus, urldefrag, urljoin, urlparse

import httpx, uvicorn, yaml
from bs4 import BeautifulSoup
from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

ROOT = Path(__file__).parent
DATA = ROOT / "data"
(DATA / "cvs").mkdir(parents=True, exist_ok=True)
SRC = ROOT / "config" / "sources.yaml"
DEF = ROOT / "config" / "sources.default.yaml"

VERSION = "0.5.0"
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
    r"^(?:login|sign\s*in|register|cookie|privacy|terms|contact|about|home|menu|search|filter|next|previous|page\s*\d+|skip\s*to|connexion|accueil|mentions\s+l[ée]gales)$",
    re.I,
)
EMAIL_RX = re.compile(r"[\w.+-]+\s*(?:@|\[at\]|\(at\))\s*[\w-]+(?:\.[\w-]+)+", re.I)
SUPERVISOR_RX = re.compile(
    r"(?:directeur\s+de\s+th[eè]se|encadrant|supervisor|contact|responsable|prof\.?|dr\.?)\s*[:\-]?\s*([A-ZÀ-Ý][a-zà-ÿ'-]+(?:\s+[A-ZÀ-Ý][a-zà-ÿ'-]+){1,3})",
    re.I,
)
FUNDING_RX = re.compile(
    r"(?:€|EUR|CHF|GBP|£|DKK|NOK|SEK)\s*[\d,.\s]+(?:/\s*(?:year|yr|month|mo|annum|an|mois))?|[\d,.\s]+\s*(?:€|EUR|CHF|GBP|DKK|NOK|SEK)\s*(?:/\s*(?:year|yr|month|mo|annum|an|mois)|gross|brut|net)?|fully\s+funded|contrat\s+doctoral|cifre|financement\s+acquis|tuition\s+fee\s+waiver|stipend|salary",
    re.I,
)

CATEGORIES = [
    "Data Engineering & Big Data",
    "Cloud & Distributed Systems",
    "AI & Machine Learning",
    "Data Science & Analytics",
    "Knowledge Graphs & Databases",
    "Software & Systems Engineering",
    "Cybersecurity & Networks",
    "Other STEM",
]

# 150 Top European & International Research Universities for AI Portal Discovery
UNIVERSITY_DIRECTORY = [
    # France (30)
    ("Université Paris-Saclay", "France", "universite-paris-saclay.fr"),
    ("Sorbonne Université", "France", "sorbonne-universite.fr"),
    ("Université PSL (Paris Sciences & Lettres)", "France", "psl.eu"),
    ("Institut Polytechnique de Paris", "France", "ip-paris.fr"),
    ("Université Paris Cité", "France", "u-paris.fr"),
    ("Université Grenoble Alpes", "France", "univ-grenoble-alpes.fr"),
    ("Université de Lyon (Claude Bernard Lyon 1)", "France", "univ-lyon1.fr"),
    ("INSA Lyon", "France", "insa-lyon.fr"),
    ("Université de Toulouse (Paul Sabatier)", "France", "univ-tlse3.fr"),
    ("INP Toulouse", "France", "inp-toulouse.fr"),
    ("Université de Bordeaux", "France", "u-bordeaux.fr"),
    ("Aix-Marseille Université", "France", "univ-amu.fr"),
    ("Université de Strasbourg", "France", "unistra.fr"),
    ("Université de Lille", "France", "univ-lille.fr"),
    ("Université de Rennes", "France", "univ-rennes.fr"),
    ("Nantes Université", "France", "univ-nantes.fr"),
    ("Université de Montpellier", "France", "umontpellier.fr"),
    ("Université Côte d'Azur", "France", "univ-cotedazur.fr"),
    ("Université de Lorraine", "France", "univ-lorraine.fr"),
    ("CentraleSupélec", "France", "centralesupelec.fr"),
    ("Télécom Paris", "France", "telecom-paris.fr"),
    ("ENSTA Paris", "France", "ensta-paris.fr"),
    ("Mines Paris - PSL", "France", "minesparis.psl.eu"),
    ("École des Ponts ParisTech", "France", "ecoledesponts.fr"),
    ("INRIA (National Research Institute)", "France", "inria.fr"),
    ("CNRS (National Research Center)", "France", "cnrs.fr"),
    ("EURECOM", "France", "eurecom.fr"),
    ("IMT Atlantique", "France", "imt-atlantique.fr"),
    ("Université de Technologie de Compiègne (UTC)", "France", "utc.fr"),
    ("Université Gustave Eiffel", "France", "univ-gustave-eiffel.fr"),
    # Germany (25)
    ("Technical University of Munich (TUM)", "Germany", "tum.de"),
    ("Ludwig-Maximilians-Universität München (LMU)", "Germany", "lmu.de"),
    ("RWTH Aachen University", "Germany", "rwth-aachen.de"),
    ("Karlsruhe Institute of Technology (KIT)", "Germany", "kit.edu"),
    ("TU Berlin", "Germany", "tu.berlin"),
    ("Humboldt-Universität zu Berlin", "Germany", "hu-berlin.de"),
    ("Freie Universität Berlin", "Germany", "fu-berlin.de"),
    ("TU Dresden", "Germany", "tu-dresden.de"),
    ("Heidelberg University", "Germany", "uni-heidelberg.de"),
    ("University of Freiburg", "Germany", "uni-freiburg.de"),
    ("University of Tübingen", "Germany", "uni-tuebingen.de"),
    ("University of Stuttgart", "Germany", "uni-stuttgart.de"),
    ("TU Darmstadt", "Germany", "tu-darmstadt.de"),
    ("University of Bonn", "Germany", "uni-bonn.de"),
    ("FAU Erlangen-Nürnberg", "Germany", "fau.de"),
    ("University of Hamburg", "Germany", "uni-hamburg.de"),
    ("University of Göttingen", "Germany", "uni-goettingen.de"),
    ("University of Münster", "Germany", "uni-muenster.de"),
    ("University of Cologne", "Germany", "uni-koeln.de"),
    ("Goethe University Frankfurt", "Germany", "uni-frankfurt.de"),
    ("Ruhr University Bochum", "Germany", "ruhr-uni-bochum.de"),
    ("TU Dortmund University", "Germany", "tu-dortmund.de"),
    ("Saarland University", "Germany", "uni-saarland.de"),
    ("Max Planck Society", "Germany", "mpg.de"),
    ("Helmholtz Association", "Germany", "helmholtz.de"),
    # Switzerland (10)
    ("ETH Zurich", "Switzerland", "ethz.ch"),
    ("EPFL (École Polytechnique Fédérale de Lausanne)", "Switzerland", "epfl.ch"),
    ("University of Zurich", "Switzerland", "uzh.ch"),
    ("University of Basel", "Switzerland", "unibas.ch"),
    ("University of Bern", "Switzerland", "unibe.ch"),
    ("University of Geneva", "Switzerland", "unige.ch"),
    ("University of Lausanne", "Switzerland", "unil.ch"),
    ("University of Neuchâtel", "Switzerland", "unine.ch"),
    ("University of Fribourg", "Switzerland", "unifr.ch"),
    ("USI Università della Svizzera italiana", "Switzerland", "usi.ch"),
    # United Kingdom (20)
    ("University of Oxford", "United Kingdom", "ox.ac.uk"),
    ("University of Cambridge", "United Kingdom", "cam.ac.uk"),
    ("Imperial College London", "United Kingdom", "imperial.ac.uk"),
    ("University College London (UCL)", "United Kingdom", "ucl.ac.uk"),
    ("University of Edinburgh", "United Kingdom", "ed.ac.uk"),
    ("University of Manchester", "United Kingdom", "manchester.ac.uk"),
    ("King's College London", "United Kingdom", "kcl.ac.uk"),
    ("University of Bristol", "United Kingdom", "bristol.ac.uk"),
    ("University of Warwick", "United Kingdom", "warwick.ac.uk"),
    ("University of Glasgow", "United Kingdom", "gla.ac.uk"),
    ("University of Southampton", "United Kingdom", "southampton.ac.uk"),
    ("University of Birmingham", "United Kingdom", "birmingham.ac.uk"),
    ("University of Leeds", "United Kingdom", "leeds.ac.uk"),
    ("University of Sheffield", "United Kingdom", "sheffield.ac.uk"),
    ("University of Nottingham", "United Kingdom", "nottingham.ac.uk"),
    ("Durham University", "United Kingdom", "durham.ac.uk"),
    ("Queen Mary University of London", "United Kingdom", "qmul.ac.uk"),
    ("Lancaster University", "United Kingdom", "lancaster.ac.uk"),
    ("University of York", "United Kingdom", "york.ac.uk"),
    ("The Alan Turing Institute", "United Kingdom", "turing.ac.uk"),
    # Netherlands & Belgium (16)
    ("TU Delft", "Netherlands", "tudelft.nl"),
    ("University of Amsterdam", "Netherlands", "uva.nl"),
    ("Vrije Universiteit Amsterdam", "Netherlands", "vu.nl"),
    ("Eindhoven University of Technology (TU/e)", "Netherlands", "tue.nl"),
    ("Utrecht University", "Netherlands", "uu.nl"),
    ("Leiden University", "Netherlands", "universiteitleiden.nl"),
    ("University of Groningen", "Netherlands", "rug.nl"),
    ("University of Twente", "Netherlands", "utwente.nl"),
    ("Radboud University", "Netherlands", "ru.nl"),
    ("Wageningen University & Research", "Netherlands", "wur.nl"),
    ("KU Leuven", "Belgium", "kuleuven.be"),
    ("Ghent University", "Belgium", "ugent.be"),
    ("UCLouvain", "Belgium", "uclouvain.be"),
    ("Université Libre de Bruxelles (ULB)", "Belgium", "ulb.be"),
    ("Vrije Universiteit Brussel (VUB)", "Belgium", "vub.be"),
    ("University of Antwerp", "Belgium", "uantwerpen.be"),
    # Nordics: Denmark, Norway, Sweden, Finland (20)
    ("Technical University of Denmark (DTU)", "Denmark", "dtu.dk"),
    ("University of Copenhagen", "Denmark", "ku.dk"),
    ("Aarhus University", "Denmark", "au.dk"),
    ("Aalborg University", "Denmark", "aau.dk"),
    ("IT University of Copenhagen", "Denmark", "itu.dk"),
    ("NTNU (Norwegian Univ. of Science & Technology)", "Norway", "ntnu.edu"),
    ("University of Oslo", "Norway", "uio.no"),
    ("University of Bergen", "Norway", "uib.no"),
    ("UiT The Arctic University of Norway", "Norway", "uit.no"),
    ("SINTEF", "Norway", "sintef.no"),
    ("KTH Royal Institute of Technology", "Sweden", "kth.se"),
    ("Chalmers University of Technology", "Sweden", "chalmers.se"),
    ("Lund University", "Sweden", "lu.se"),
    ("Uppsala University", "Sweden", "uu.se"),
    ("Stockholm University", "Sweden", "su.se"),
    ("Linköping University", "Sweden", "liu.se"),
    ("Aalto University", "Finland", "aalto.fi"),
    ("University of Helsinki", "Finland", "helsinki.fi"),
    ("Tampere University", "Finland", "tuni.fi"),
    ("University of Oulu", "Finland", "oulu.fi"),
    # Spain, Italy, Austria, Ireland, Gulf (29)
    ("Universitat Politècnica de Catalunya (UPC)", "Spain", "upc.edu"),
    ("Universidad Politécnica de Madrid (UPM)", "Spain", "upm.es"),
    ("Universitat Pompeu Fabra (UPF)", "Spain", "upf.edu"),
    ("Universitat de Barcelona", "Spain", "ub.edu"),
    ("Universidad Autónoma de Madrid", "Spain", "uam.es"),
    ("Universidad Carlos III de Madrid (UC3M)", "Spain", "uc3m.es"),
    ("Barcelona Supercomputing Center (BSC)", "Spain", "bsc.es"),
    ("Politecnico di Milano", "Italy", "polimi.it"),
    ("Politecnico di Torino", "Italy", "polito.it"),
    ("Sapienza University of Rome", "Italy", "uniroma1.it"),
    ("University of Bologna", "Italy", "unibo.it"),
    ("University of Padua", "Italy", "unipd.it"),
    ("University of Trento", "Italy", "unitn.it"),
    ("TU Wien (Vienna University of Technology)", "Austria", "tuwien.at"),
    ("University of Vienna", "Austria", "univie.ac.at"),
    ("TU Graz", "Austria", "tugraz.at"),
    ("IST Austria", "Austria", "ista.ac.at"),
    ("Trinity College Dublin", "Ireland", "tcd.ie"),
    ("University College Dublin", "Ireland", "ucd.ie"),
    ("University of Galway", "Ireland", "universityofgalway.ie"),
    ("University of Luxembourg", "Luxembourg", "uni.lu"),
    ("University of Lisbon (Instituto Superior Técnico)", "Portugal", "tecnico.ulisboa.pt"),
    ("University of Porto", "Portugal", "up.pt"),
    ("Charles University Prague", "Czechia", "cuni.cz"),
    ("Czech Technical University in Prague", "Czechia", "cvut.cz"),
    ("University of Warsaw", "Poland", "uw.edu.pl"),
    ("KAUST (King Abdullah Univ. of Science & Tech)", "Saudi Arabia", "kaust.edu.sa"),
    ("MBZUAI (Mohamed bin Zayed Univ. of AI)", "UAE", "mbzuai.ac.ae"),
    ("UM6P (Mohammed VI Polytechnic University)", "Morocco", "um6p.ma"),
]

DB = sqlite3.connect(DATA / "radar.db", check_same_thread=False)
DB.execute("CREATE TABLE IF NOT EXISTS offers(id TEXT PRIMARY KEY, j TEXT)")
DB.execute("CREATE TABLE IF NOT EXISTS seen(id TEXT PRIMARY KEY)")
DB.execute("CREATE TABLE IF NOT EXISTS contacts(id TEXT PRIMARY KEY, j TEXT)")
DB.execute("CREATE TABLE IF NOT EXISTS uni_portals(domain TEXT PRIMARY KEY, j TEXT)")
DB.commit()

LOCK = threading.Lock()
BLOCK = threading.Lock()
RUN = {"msg": "", "running": False, "done": 0, "total": 0, "new": 0, "stop": False, "sources": []}
DISC = {"running": False, "done": 0, "total": 0, "found": 0, "current": "", "log": []}
HEALTH, ROBOTS = {}, {}
LLM = {"off": False}
PRIVACY_LOG = []


def jload(name, default):
    p = DATA / name
    return json.loads(p.read_text("utf-8")) if p.exists() else default


def jsave(name, obj):
    (DATA / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), "utf-8")


def cfg():
    return yaml.safe_load(SRC.read_text("utf-8"))


def settings():
    return {
        "ollama": "http://localhost:11434",
        "model": "gemma4:26b",
        "embed": "nomic-embed-text",
        "threshold": 50,
        "privacy": True,
        "paid": False,
        **jload("settings.json", {}),
    }


def log_outbound(url, method="GET"):
    if settings().get("privacy", True):
        PRIVACY_LOG.append(f"{datetime.datetime.now().strftime('%H:%M:%S')} [{method}] {url}")
        if len(PRIVACY_LOG) > 500:
            del PRIVACY_LOG[:-500]


def extract_cv_text(path: Path) -> str:
    ext = path.suffix.lower()
    try:
        if ext in (".txt", ".md"):
            return path.read_text("utf-8", errors="ignore")
        if ext == ".pdf":
            try:
                from pypdf import PdfReader
                reader = PdfReader(str(path))
                return "\n".join(page.extract_text() or "" for page in reader.pages)
            except ImportError:
                raw = path.read_bytes().decode("latin-1", errors="ignore")
                chunks = re.findall(r"\(([^()]{3,200})\)", raw)
                return " ".join(chunks) if chunks else "[Install pypdf: pip install pypdf]"
        if ext == ".docx":
            try:
                import docx
                doc = docx.Document(str(path))
                return "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            except ImportError:
                import zipfile, xml.etree.ElementTree as ET
                with zipfile.ZipFile(path) as z:
                    xml_content = z.read("word/document.xml")
                tree = ET.fromstring(xml_content)
                return " ".join(elem.text for elem in tree.iter() if elem.text)
    except Exception as e:
        return f"[CV parse error for {path.name}: {e}]"
    return ""


def all_cvs_context(max_chars=9000) -> str:
    cvs_meta = jload("cvs_text.json", {})
    for f in (DATA / "cvs").iterdir():
        if f.is_file() and f.name not in cvs_meta:
            txt = extract_cv_text(f).strip()
            if txt:
                cvs_meta[f.name] = txt
                jsave("cvs_text.json", cvs_meta)
    if not cvs_meta:
        return "No CV uploaded yet."
    combined = [f"=== CV FILE: {fname} ===\n{txt[:4500]}" for fname, txt in cvs_meta.items()]
    return "\n\n".join(combined)[:max_chars]


def offers():
    with LOCK:
        return [json.loads(r[0]) for r in DB.execute("SELECT j FROM offers")]


def offer_fingerprint(title, inst):
    return hashlib.sha1(f"{norm(title)[:80]}|{norm(inst)[:40]}".encode()).hexdigest()[:12]


def put_offer(o):
    o["v"] = 2
    o["verified"] = True
    o.setdefault("category", classify_heuristic(o.get("title", "") + " " + o.get("description_text", "")[:1000]))
    with LOCK:
        DB.execute("INSERT OR REPLACE INTO offers VALUES(?,?)", (o["id"], json.dumps(o, ensure_ascii=False)))
        DB.commit()
    sup = o.get("supervisor", "")
    for em in o.get("contact_emails", []):
        cid = hashlib.sha1(f"{em}|{o['id']}".encode()).hexdigest()[:12]
        cobj = {
            "id": cid,
            "name": sup or em.split("@")[0].replace(".", " ").title(),
            "email": em,
            "role": "Supervisor / Contact" if sup else "Listed Contact",
            "org": o.get("industry_partner") or o.get("institution") or o.get("source", ""),
            "offer_id": o["id"],
            "confidence": 0.9 if sup else 0.8,
            "date_found": datetime.date.today().isoformat(),
        }
        with LOCK:
            DB.execute("INSERT OR IGNORE INTO contacts VALUES(?,?)", (cid, json.dumps(cobj, ensure_ascii=False)))
            DB.commit()


def ensure_sources(force=False):
    if not DEF.exists():
        return
    d = yaml.safe_load(DEF.read_text("utf-8"))
    cur = yaml.safe_load(SRC.read_text("utf-8")) if SRC.exists() else {}
    if force or (cur or {}).get("version", 0) < d["version"]:
        if SRC.exists():
            shutil.copy(SRC, SRC.with_name("sources.backup.yaml"))
        existing_ids = {s["id"] for s in d["sources"]}
        d["sources"] += [
            s for s in (cur or {}).get("sources", [])
            if (str(s.get("id", "")).startswith(("custom-", "uni-"))) and s["id"] not in existing_ids
        ]
        SRC.write_text(yaml.safe_dump(d, allow_unicode=True, sort_keys=False), "utf-8")


ensure_sources()


def allowed(url):
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


def bget(url, wait_extra_ms=2500):
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
                pg.wait_for_load_state("networkidle", timeout=7000)
            except Exception:
                pg.wait_for_timeout(wait_extra_ms)
            return pg.content()
        finally:
            ctx.close()


def get(url, mode="auto"):
    if RUN["stop"]:
        raise RuntimeError("Stopped by user")
    if not allowed(url):
        raise PermissionError("blocked by robots.txt (use the Assisted browser for this site)")
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


MONTHS = {
    "jan": 1, "feb": 2, "fev": 2, "mar": 3, "apr": 4, "avr": 4, "abr": 4,
    "may": 5, "mai": 5, "mei": 5, "jun": 6, "jul": 7, "aug": 8, "aou": 8,
    "ago": 8, "sep": 9, "oct": 10, "okt": 10, "nov": 11, "dec": 12, "dez": 12,
}
DLKEY = r"(?:deadline|closes?|closing|apply\s*by|apply\s*before|date\s*limite|limite\s*de\s*candidature|cloture|fecha\s*limite|plazo|bewerbungsfrist|frist|sluitingsdatum|uiterlijk|jusqu'au|valid\s*through|application\s*deadline)"


def norm(t):
    return "".join(c for c in unicodedata.normalize("NFD", str(t).lower()) if unicodedata.category(c) != "Mn")


def classify_heuristic(text):
    t = norm(text)
    if any(k in t for k in ("data engineer", "big data", "lakehouse", "etl", "spark", "hadoop", "kafka", "airflow", "snowflake", "data pipeline", "streaming")):
        return "Data Engineering & Big Data"
    if any(k in t for k in ("cloud", "distributed", "kubernetes", "edge computing", "serverless", "high performance computing", "hpc")):
        return "Cloud & Distributed Systems"
    if any(k in t for k in ("knowledge graph", "ontology", "ontologies", "semantic web", "database", "sql", "neo4j", "graph database")):
        return "Knowledge Graphs & Databases"
    if any(k in t for k in ("data science", "data mining", "data quality", "data governance", "analytics", "business intelligence")):
        return "Data Science & Analytics"
    if any(k in t for k in ("machine learning", "deep learning", "llm", "large language", "nlp", "computer vision", "artificial intelligence", "neural", "rag")):
        return "AI & Machine Learning"
    if any(k in t for k in ("security", "privacy", "cryptography", "cyber", "network", "5g", "iot")):
        return "Cybersecurity & Networks"
    if any(k in t for k in ("software", "compiler", "operating system", "verification", "formal methods")):
        return "Software & Systems Engineering"
    return "Other STEM"


def find_deadline(text):
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
        items = d if isinstance(d, list) else d.get("@graph", [d])
        for x in items:
            if isinstance(x, dict) and "JobPosting" in str(x.get("@type")):
                return x
    return None


def links_of(html, base, minlen=4):
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


def detail(url, mode="auto", html=None):
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
    f_match = FUNDING_RX.search(desc[:3500])
    sup_match = SUPERVISOR_RX.search(desc[:3500])
    emails = sorted(
        {
            re.sub(r"\s*(\[at\]|\(at\))\s*", "@", e).lower()
            for e in EMAIL_RX.findall(text)
            if not e.lower().endswith((".png", ".jpg", ".svg", ".webp", "example.com"))
        }
    )
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


def oid(u):
    return hashlib.sha1(u.encode()).hexdigest()[:10]


def make_offer(s, url, d, kws):
    low = norm(d["title"] + " " + d["description_text"])
    matched_kws = [k for k in kws if re.search(r"\b" + re.escape(norm(k)), low)]
    return {
        "v": 2,
        "verified": True,
        "id": oid(url),
        **d,
        "industry_partner": "",
        "country": s.get("country", ""),
        "program_type": s.get("program", ""),
        "category": classify_heuristic(d["title"] + " " + d["description_text"][:1000]),
        "keywords": matched_kws,
        "chance": 0,
        "match": 0,
        "source": s.get("program", ""),
        "url": url,
        "requires": [],
    }


def llm(prompt, js=False, timeout=240, model_override=None):
    if RUN["stop"]:
        raise RuntimeError("Stopped by user")
    s = settings()
    model_name = model_override or s["model"]
    base_url = s["ollama"].rstrip("/")
    body = {"model": model_name, "prompt": prompt, "stream": False, "options": {"temperature": 0.1, "seed": 42}}
    if js:
        body["format"] = "json"
    try:
        r = httpx.post(base_url + "/api/generate", json=body, timeout=timeout)
        if r.status_code == 404:
            raise RuntimeError(f"Ollama model '{model_name}' not found (HTTP 404). Run: ollama pull {model_name}")
        r.raise_for_status()
        LLM["off"] = False
        return r.json()["response"]
    except httpx.ConnectError:
        LLM["off"] = True
        raise RuntimeError(f"Cannot connect to Ollama at {base_url}. Start Ollama first.")
    except httpx.ReadTimeout:
        raise RuntimeError(f"Ollama timed out after {timeout}s using {model_name}.")


def llm_pick(links):
    if not links:
        return []
    # Fast path for ADUM proposal links (proposition.pl?matricule_prop=...)
    adum_props = [(t, u) for t, u in links if "proposition.pl" in u.lower()]
    if adum_props:
        return adum_props

    heuristic = [
        (t, u)
        for t, u in links
        if PHD_RX.search(t + " " + u)
        or any(k in u.lower() for k in ("/job/", "/jobs/", "/offre/", "id_offre", "/ad/", "/project/", "/vacancy/", "proposition.pl"))
    ][:60]
    if LLM["off"]:
        return heuristic
    rows = "\n".join(f"{i}|{t[:90]}|{u[-90:]}" for i, (t, u) in enumerate(links[:160]))
    try:
        raw = llm(
            'You are filtering links scraped from a university or academic PhD portal.\n'
            'Input format: index|anchor_text|url_tail\n'
            'Return ONLY valid JSON: {"offers":[indices]}\n'
            'Include ONLY links that point to an INDIVIDUAL PhD, doctoral, thesis proposal, CIFRE, Industrial PhD, or MSCA offer.\n'
            'Strictly EXCLUDE navigation menus, category filters, pagination, cookie policies, login links, general guides, or news.\n'
            + rows,
            True,
            timeout=30,
        )
        r = json.loads(raw)
        picked = [links[i] for i in r.get("offers", []) if isinstance(i, int) and 0 <= i < len(links[:160])]
        return picked or heuristic
    except Exception:
        return heuristic


def llm_enrich(o):
    if LLM["off"] or not settings().get("use_llm", True):
        return
    try:
        raw = llm(
            "Extract structured facts ONLY from this PhD proposal/offer text. Do not invent facts.\n"
            f'Allowed categories: {json.dumps(CATEGORIES)}\n'
            'Reply ONLY JSON: {"is_real_phd_offer":true,"category":"one of allowed categories","institution":"","industry_partner":"","supervisor":"","city":"","funding":"",'
            '"deadline":"YYYY-MM-DD or empty","requires":["skill or degree"],"visa_note":"EU/non-EU restriction if stated or empty"}\n'
            f"TITLE: {o['title']}\nTEXT: {o['description_text'][:4500]}",
            True,
            timeout=60,
        )
        r = json.loads(raw)
        if r.get("is_real_phd_offer") is False:
            o["_reject"] = True
            return
        if r.get("category") in CATEGORIES:
            o["category"] = r["category"]
        for k in ("institution", "industry_partner", "supervisor", "city", "funding"):
            val = str(r.get(k) or "").strip()[:120]
            if val:
                o[k] = val
        dl_val = str(r.get("deadline") or "").strip()
        if not o.get("deadline") and re.match(r"^20\d\d-\d\d-\d\d$", dl_val):
            o["deadline"] = dl_val
        o["requires"] = [str(x).strip()[:40] for x in r.get("requires", []) if str(x).strip()][:12]
        if r.get("visa_note"):
            o["visa_note"] = str(r["visa_note"])[:200]
    except Exception:
        pass


def process(s, urls, kws, known, mode="auto", limit=100, dry=False, html_fetcher=None):
    n = 0
    existing_fps = {offer_fingerprint(x.get("title", ""), x.get("institution", "")) for x in offers()}
    for u in [u for u in urls if oid(u) not in known][:limit]:
        if RUN["stop"]:
            break
        known.add(oid(u))
        time.sleep(0.8)
        try:
            raw_html = html_fetcher(u) if html_fetcher else None
            d = detail(u, mode, html=raw_html)
        except Exception:
            continue
        o = make_offer(s, u, d, kws)
        fp = offer_fingerprint(o["title"], o["institution"])
        has_phd_signal = bool(PHD_RX.search(norm(o["title"] + " " + o["description_text"][:1800]))) or ("proposition.pl" in u.lower())
        # Collect ALL real PhD proposals (do not drop non-matching keywords; user filters by category in UI!)
        if o["title"] and len(o["title"]) > 8 and has_phd_signal and fp not in existing_fps:
            llm_enrich(o)
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


def listing(s, kws, known, dry=False):
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
            for _, h in llm_pick(page_links):
                cands.setdefault(h, 1)
        time.sleep(0.8)
    return process(s, list(cands), kws, known, mode, s.get("max_detail", 100), dry=dry), len(cands)


def ingest_html(url, html):
    """Captures either a single offer page OR an entire directory page (like ADUM proposition_Setab.pl with 100+ proposals)."""
    host = urlparse(url).netloc
    s = {"country": "France" if "adum.fr" in host else "", "program": f"Assisted ({host})"}
    kws = cfg().get("keywords", [])
    known = {o["id"] for o in offers()}
    soup = BeautifulSoup(html, "html.parser")
    jp = jobposting(soup)

    # Special handling for ADUM proposition_Setab.pl or multi-offer directory pages
    all_links = links_of(html, url, 4)
    adum_links = [h for _, h in all_links if "proposition.pl" in h.lower()]
    if adum_links:
        # Fetch each ADUM proposal using the active Playwright session (bypasses robots.txt since user initiated capture)
        def fetch_via_active_browser(target_url):
            if BR["t"] and BR["t"].is_alive():
                box = {"ev": threading.Event()}
                BR["q"].put(("goto_and_get", (target_url, box)))
                if box["ev"].wait(20):
                    return box.get("html")
            return None

        return process(s, adum_links, kws, known, "browser", limit=120, html_fetcher=fetch_via_active_browser)

    picks = [] if jp else llm_pick(all_links)
    if len(picks) >= 2:
        return process(s, [h for _, h in picks], kws, known, "auto", limit=100)
    o = make_offer(s, url, detail(url, html=html), kws)
    llm_enrich(o)
    put_offer(o)
    return 1


def watch(s):
    html = get(s["url"], s.get("fetch", "auto"))
    txt = norm(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
    h = hashlib.sha1(txt.encode()).hexdigest()
    prev = jload("watch.json", {})
    note = "first visit" if s["id"] not in prev else ("changed since last visit" if prev[s["id"]] != h else "unchanged")
    prev[s["id"]] = h
    jsave("watch.json", prev)
    return note


def _crawl_job(dry=False):
    c = cfg()
    en = [s for s in c["sources"] if s.get("enabled", True)]
    known = {o["id"] for o in offers()} | {r[0] for r in DB.execute("SELECT id FROM seen")}
    LLM["off"] = False
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
                st.update(status="ok", found=n, note=f"{seen} candidate links" if seen else "0 matching links")
                RUN["new"] += n
        except Exception as e:
            err = str(e)
            if "needs playwright" in err.lower():
                st.update(status="skipped", note="Install Playwright or use Assisted Browser")
            elif "blocked by robots.txt" in err.lower():
                st.update(status="skipped", note="Blocked by robots.txt -> open in Assisted Browser & Capture")
            elif "stopped by user" in err.lower():
                st.update(status="skipped", note="Stopped")
            else:
                st.update(status="error", error=f"{type(e).__name__}: {err}"[:200])
        RUN["done"] += 1
        time.sleep(0.4)
    RUN["running"] = False


def crawl_job(dry=False):
    try:
        _crawl_job(dry)
    except Exception as e:
        RUN["msg"] = f"{type(e).__name__}: {e}"[:300]
    finally:
        RUN["running"] = False


# --- Autonomous University PhD Portal Discovery Agent ---
PORTAL_PATHS = [
    "/phd", "/doctorate", "/doctoral", "/jobs", "/vacancies", "/careers",
    "/en/phd", "/en/jobs", "/en/vacancies", "/fr/doctorat", "/recherche/doctorat",
    "/karriere/promotion", "/stellenangebote", "/propositions-de-these",
]


def discover_university_portal(name: str, country: str, domain: str):
    """Probes a university domain + DuckDuckGo HTML search + Ollama to find its PhD proposal portal."""
    candidates = []
    # 1. Search DuckDuckGo HTML for site:domain PhD positions / propositions de thèse
    q = quote_plus(f"site:{domain} (PhD OR doctoral OR \"propositions de thèse\" OR \"offres de thèse\" OR Stellenangebote)")
    search_url = f"https://html.duckduckgo.com/html/?q={q}"
    try:
        log_outbound(search_url, "DISCOVER_SEARCH")
        r = httpx.get(search_url, headers=HDR, timeout=12, follow_redirects=True)
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select("a.result__a"):
            href = a.get("href", "")
            tx = a.get_text(" ", strip=True)
            if domain in href and href.startswith("http"):
                candidates.append((tx, href))
    except Exception:
        pass

    # 2. Fallback direct common paths on https://www.<domain>
    if not candidates:
        base = f"https://www.{domain}"
        for p in PORTAL_PATHS[:6]:
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
    # Ask Ollama to pick the best PhD listing portal among search candidates
    if len(candidates) > 1 and not LLM["off"]:
        try:
            rows = "\n".join(f"{i}|{t[:80]}|{u}" for i, (t, u) in enumerate(candidates[:10]))
            ans = json.loads(
                llm(
                    f"Pick the single best URL that lists open PhD positions or thesis proposals for {name} ({domain}).\n"
                    'Reply ONLY JSON: {"index": 0}\n' + rows,
                    True,
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

    # Dynamically append to sources.yaml if not already present
    c = cfg()
    if not any(s.get("id") == entry["id"] or s.get("url") == best_url for s in c["sources"]):
        c["sources"].append(entry)
        SRC.write_text(yaml.safe_dump(c, allow_unicode=True, sort_keys=False), "utf-8")
    return entry


def _discovery_worker(limit: int, country_filter: str):
    unis = [
        u for u in UNIVERSITY_DIRECTORY
        if not country_filter or u[1].lower() == country_filter.lower()
    ][:limit]
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
                DISC["log"].insert(0, f"SKIP: {name} (no public portal indexed)")
        except Exception as e:
            DISC["log"].insert(0, f"ERR: {name} -> {e}")
        DISC["done"] += 1
        time.sleep(1.0)
    DISC["running"] = False
    DISC["current"] = "Done"


def page_text(url):
    if not str(url).startswith("http"):
        return ""
    return BeautifulSoup(get(url, "auto"), "html.parser").get_text(" ", strip=True)[:12000]


app = FastAPI()


@app.get("/")
def index():
    return FileResponse(ROOT / "index.html")


@app.get("/api/version")
def api_version():
    return {"version": VERSION, "sources": len(cfg()["sources"]), "config": str(SRC), "universities": len(UNIVERSITY_DIRECTORY)}


@app.get("/api/diagnostics")
def api_diagnostics():
    s = settings()
    ollama_status = {"ok": False, "url": s["ollama"], "configured_model": s["model"], "available_models": [], "error": ""}
    try:
        r = httpx.get(s["ollama"].rstrip("/") + "/api/tags", timeout=5)
        r.raise_for_status()
        models = [m["name"] for m in r.json().get("models", [])]
        ollama_status["ok"] = True
        ollama_status["available_models"] = models
        if s["model"] not in models and f"{s['model']}:latest" not in models:
            ollama_status["error"] = f"Model '{s['model']}' not found in Ollama tags."
    except Exception as e:
        ollama_status["error"] = str(e)

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
        uni_count = DB.execute("SELECT COUNT(*) FROM uni_portals").fetchone()[0]

    return {
        "version": VERSION,
        "ollama": ollama_status,
        "playwright_installed": pw_ok,
        "db": {"offers": offer_count, "contacts": contact_count, "seen_urls": seen_count, "discovered_unis": uni_count},
        "cvs": [{"file": k, "chars": len(v)} for k, v in cvs_meta.items()],
        "crawl_state": RUN,
        "discovery_state": DISC,
        "recent_outbound": PRIVACY_LOG[-25:],
    }


@app.get("/api/universities")
def api_universities():
    with LOCK:
        discovered = {r[0]: json.loads(r[1]) for r in DB.execute("SELECT domain, j FROM uni_portals")}
    return [
        {"name": n, "country": c, "domain": d, "portal": discovered.get(d)}
        for n, c, d in UNIVERSITY_DIRECTORY
    ]


@app.post("/api/discover/start")
async def api_discover_start(req: Request):
    if DISC["running"]:
        return JSONResponse({"error": "Discovery agent already running"}, 409)
    b = await req.json() if req.headers.get("content-type", "").startswith("application/json") else {}
    limit = int(b.get("limit", 25))
    country = str(b.get("country", "")).strip()
    threading.Thread(target=_discovery_worker, args=(limit, country), daemon=True).start()
    return {"started": True, "limit": limit}


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
    SRC.write_text(yaml.safe_dump(c, allow_unicode=True, sort_keys=False), "utf-8")
    return {"ok": True}


@app.post("/api/sources")
async def src_add(req: Request):
    url = (await req.json())["url"]
    c = cfg()
    c["sources"].append(
        {
            "id": "custom-" + hashlib.sha1(url.encode()).hexdigest()[:6],
            "country": "Custom",
            "program": urlparse(url).netloc,
            "url": url,
            "kind": "listing",
            "fetch": "auto",
            "enabled": True,
        }
    )
    SRC.write_text(yaml.safe_dump(c, allow_unicode=True, sort_keys=False), "utf-8")
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
        llm_enrich(o)
        put_offer(o)
        return o
    except Exception as e:
        return JSONResponse({"error": str(e)}, 400)


BR = {"q": queue.Queue(), "t": None}


def browser_session(url):
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


@app.put("/api/settings")
async def set_put(req: Request):
    jsave("settings.json", await req.json())
    return {"ok": True}


@app.get("/api/cvs")
def cv_list():
    cvs_meta = jload("cvs_text.json", {})
    return [{"name": k, "chars": len(v), "preview": v[:200]} for k, v in cvs_meta.items()]


@app.post("/api/cvs")
async def cv_up(file: UploadFile = File(...)):
    fname = Path(file.filename).name
    dest = DATA / "cvs" / fname
    dest.write_bytes(await file.read())
    extracted = extract_cv_text(dest).strip()
    cvs_meta = jload("cvs_text.json", {})
    cvs_meta[fname] = extracted
    jsave("cvs_text.json", cvs_meta)
    return {"ok": True, "name": fname, "chars": len(extracted), "preview": extracted[:250]}


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
    model_override = body.get("model")
    cv_text = all_cvs_context(7000)
    scored, errors = 0, []
    for o in offers():
        try:
            r = json.loads(
                llm(
                    f"""Evaluate this PhD position for the candidate below using BOTH their profile and full CV text.
Candidate context: Yassir Tagemouati, Big Data Engineering student (EIDIA, UEMF, Fès, Morocco, graduating 2027), non-EU citizen based in Morocco (requires visa sponsorship; check MSCA mobility rules, UK home-fee restrictions, CIFRE eligibility, or employer work-permit sponsorship).
Reply ONLY valid JSON:
{{"match_percent":0-100,"chance_percent":0-100,"matched_skills":[],"missing_skills":[],"red_flags":[],"reason":""}}
Only use facts stated in the offer and candidate CV/profile.
CANDIDATE PROFILE: {json.dumps(prof)[:2000]}
CANDIDATE FULL CV(S):
{cv_text}
OFFER: {o['title']} | {o['program_type']} | {o.get('funding','')} | {o['description_text'][:4000]}""",
                    True,
                    timeout=120,
                    model_override=model_override,
                )
            )
            o.update(
                chance=max(0, min(100, int(r.get("chance_percent", 0)))),
                match=max(0, min(100, int(r.get("match_percent", 0)))),
                requires=r.get("matched_skills", []) + r.get("missing_skills", []),
            )
            put_offer(o)
            scored += 1
        except Exception as e:
            errors.append(f"{o['id']}: {e}")
    return {"ok": True, "scored": scored, "errors": errors[:5]}


@app.post("/api/offers/{oid}/report")
async def report(oid: str, req: Request):
    try:
        body = await req.json()
        prof = body.get("profile", {})
        model_override = body.get("model")
        o = next((x for x in offers() if x["id"] == oid), None)
        if not o:
            return JSONResponse({"error": "Offer not found in backend SQLite database. Re-crawl or add the offer first."}, 404)
        text = ""
        try:
            text = page_text(o["url"])
        except Exception:
            text = ""
        if len(text) < 200:
            text = o.get("description_text", "")
        cv_text = all_cvs_context(8000)
        md = llm(
            f"""Write a structured Markdown report for this PhD offer comparing it against the candidate's profile and full CV(s):
1. Summary & Research Topic
2. Fit Analysis & Chance % (matching Yassir's Big Data / Cloud / Spark / Azure / AWS / Snowflake / Smartovate / UEMF background from the CV)
3. Strengths & Skill Gaps
4. Visa & Eligibility Assessment (non-EU candidate based in Morocco)
5. Funding Quality & Contract Type
6. Required Documents & Application Steps
7. Red Flags, Go / No-Go Verdict, and Next Actions

Rule: Every factual claim MUST include a short direct quote from the offer text or CV. If a detail is missing, write "not stated".
CANDIDATE PROFILE: {json.dumps(prof)[:2000]}
CANDIDATE FULL CV(S):
{cv_text}
OFFER TITLE: {o.get('title','')}
OFFER URL: {o.get('url','')}
PAGE TEXT: {text[:9000]}""",
            False,
            timeout=240,
            model_override=model_override,
        )
        return {"markdown": md}
    except Exception as e:
        traceback.print_exc()
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, 502)


@app.post("/api/send")
def send():
    return JSONResponse({"error": "Use Export .eml to review and send via your mail client"}, 501)


if __name__ == "__main__":
    print(f"PhD-Radar backend v{VERSION}: {len(cfg()['sources'])} sources | {len(UNIVERSITY_DIRECTORY)} universities in directory")
    threading.Timer(1.5, lambda: webbrowser.open("http://127.0.0.1:8765")).start()
    uvicorn.run(app, host="127.0.0.1", port=8765)