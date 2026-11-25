"""External data directory (%LOCALAPPDATA%/PhD-Radar), .env loader, SQLite DB, YAML configs, and CV extraction."""
import datetime, hashlib, json, os, re, shutil, sqlite3, threading, unicodedata
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent
CFG_DIR = ROOT / "config"
SRC = CFG_DIR / "sources.yaml"
DEF = CFG_DIR / "sources.default.yaml"
UNIS_YAML = CFG_DIR / "universities.yaml"
ENV_FILE = ROOT / ".env"


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text("utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv(ENV_FILE)

# Store all runtime data outside the git repo so git stays clean automatically
_default_data = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "PhD-Radar"
_env_data = os.environ.get("PHDRADAR_DATA", "").strip()
DATA = Path(_env_data) if _env_data else _default_data
(DATA / "cvs").mkdir(parents=True, exist_ok=True)

VERSION = "0.6.1"
LOCK = threading.Lock()
PRIVACY_LOG = []

DB = sqlite3.connect(DATA / "radar.db", check_same_thread=False)
DB.execute("CREATE TABLE IF NOT EXISTS offers(id TEXT PRIMARY KEY, j TEXT)")
DB.execute("CREATE TABLE IF NOT EXISTS seen(id TEXT PRIMARY KEY)")
DB.execute("CREATE TABLE IF NOT EXISTS contacts(id TEXT PRIMARY KEY, j TEXT)")
DB.execute("CREATE TABLE IF NOT EXISTS uni_portals(domain TEXT PRIMARY KEY, j TEXT)")
DB.commit()

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


def norm(t: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(t).lower()) if unicodedata.category(c) != "Mn")


def jload(name: str, default):
    p = DATA / name
    return json.loads(p.read_text("utf-8")) if p.exists() else default


def jsave(name: str, obj) -> None:
    (DATA / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2), "utf-8")


def cfg() -> dict:
    return yaml.safe_load(SRC.read_text("utf-8"))


def save_cfg(c: dict) -> None:
    SRC.write_text(yaml.safe_dump(c, allow_unicode=True, sort_keys=False), "utf-8")


def load_universities() -> list[tuple[str, str, str]]:
    if not UNIS_YAML.exists():
        return []
    raw = yaml.safe_load(UNIS_YAML.read_text("utf-8")) or {}
    return [(u["name"], u["country"], u["domain"]) for u in raw.get("universities", [])]


def settings() -> dict:
    saved = jload("settings.json", {})
    base = {
        "provider": os.environ.get("AI_PROVIDER", "ollama"),
        "ollama": os.environ.get("OLLAMA_URL", "http://localhost:11434"),
        "model": os.environ.get("OLLAMA_MODEL", "gemma4:26b"),
        "gemini_key": os.environ.get("GEMINI_API_KEY", ""),
        "gemini_model": os.environ.get("GEMINI_MODEL", "gemini-4-hydrogen"),
        "openai_key": os.environ.get("OPENAI_API_KEY", ""),
        "openai_model": os.environ.get("OPENAI_MODEL", "gpt-6.1-sol"),
        "threshold": 50,
        "privacy": True,
    }
    merged = {**base, **{k: v for k, v in saved.items() if v not in ("", None)}}
    return merged


def log_outbound(url: str, method: str = "GET") -> None:
    if settings().get("privacy", True):
        PRIVACY_LOG.append(f"{datetime.datetime.now().strftime('%H:%M:%S')} [{method}] {url}")
        if len(PRIVACY_LOG) > 500:
            del PRIVACY_LOG[:-500]


def ensure_sources(force: bool = False) -> None:
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
            if str(s.get("id", "")).startswith(("custom-", "uni-")) and s["id"] not in existing_ids
        ]
        save_cfg(d)


ensure_sources()


def classify_heuristic(text: str) -> str:
    t = norm(text)
    if any(k in t for k in ("data engineer", "big data", "lakehouse", "etl", "spark", "hadoop", "kafka", "airflow", "snowflake", "data pipeline", "streaming")):
        return "Data Engineering & Big Data"
    if any(k in t for k in ("cloud", "distributed", "kubernetes", "edge computing", "serverless", "hpc")):
        return "Cloud & Distributed Systems"
    if any(k in t for k in ("knowledge graph", "ontology", "ontologies", "semantic web", "database", "sql", "neo4j")):
        return "Knowledge Graphs & Databases"
    if any(k in t for k in ("data science", "data mining", "data quality", "data governance", "analytics")):
        return "Data Science & Analytics"
    if any(k in t for k in ("machine learning", "deep learning", "llm", "nlp", "computer vision", "artificial intelligence", "rag")):
        return "AI & Machine Learning"
    if any(k in t for k in ("security", "privacy", "cryptography", "cyber", "network", "5g", "iot")):
        return "Cybersecurity & Networks"
    if any(k in t for k in ("software", "compiler", "operating system", "verification")):
        return "Software & Systems Engineering"
    return "Other STEM"


def offer_fingerprint(title: str, inst: str) -> str:
    return hashlib.sha1(f"{norm(title)[:80]}|{norm(inst)[:40]}".encode()).hexdigest()[:12]


def offers() -> list[dict]:
    with LOCK:
        return [json.loads(r[0]) for r in DB.execute("SELECT j FROM offers")]


def put_offer(o: dict) -> None:
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


def extract_cv_text(path: Path) -> str:
    ext = path.suffix.lower()
    try:
        if ext in (".txt", ".md"):
            return path.read_text("utf-8", errors="ignore")
        if ext == ".pdf":
            try:
                from pypdf import PdfReader
                return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
            except ImportError:
                raw = path.read_bytes().decode("latin-1", errors="ignore")
                chunks = re.findall(r"\(([^()]{3,200})\)", raw)
                return " ".join(chunks) if chunks else "[Install pypdf: pip install pypdf]"
        if ext == ".docx":
            try:
                import docx
                return "\n".join(p.text for p in docx.Document(str(path)).paragraphs if p.text.strip())
            except ImportError:
                import zipfile, xml.etree.ElementTree as ET
                with zipfile.ZipFile(path) as z:
                    tree = ET.fromstring(z.read("word/document.xml"))
                return " ".join(elem.text for elem in tree.iter() if elem.text)
    except Exception as e:
        return f"[CV parse error: {e}]"
    return ""


def all_cvs_context(max_chars: int = 9000) -> str:
    cvs_meta = jload("cvs_text.json", {})
    for f in (DATA / "cvs").iterdir():
        if f.is_file() and f.name not in cvs_meta:
            txt = extract_cv_text(f).strip()
            if txt:
                cvs_meta[f.name] = txt
                jsave("cvs_text.json", cvs_meta)
    if not cvs_meta:
        return "No CV uploaded yet."
    return "\n\n".join(f"=== CV: {k} ===\n{v[:4500]}" for k, v in cvs_meta.items())[:max_chars]
