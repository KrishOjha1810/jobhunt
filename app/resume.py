"""Parse a resume (PDF or DOCX) into text and extract skill/role keywords.

Two-pass extraction so even resumes using skills outside our vocab still produce good keywords:
1. Match a broad curated skills/role vocabulary (high precision).
2. Pull tokens from the resume's Skills/Technologies/Tools section (catches the long tail).
"""
import re
from pathlib import Path

# Broad tech + role vocabulary. Extend freely; multi-word entries are matched as phrases.
SKILL_VOCAB = [
    # languages
    "python", "javascript", "typescript", "java", "rust", "solidity", "go", "golang", "c++", "c#",
    "kotlin", "swift", "scala", "ruby", "php", "sql", "matlab", "perl", "elixir", "haskell",
    "dart", "objective-c", "bash", "shell", "powershell",
    # backend / frameworks
    "node.js", "nodejs", "node", "nestjs", "express", "django", "flask", "fastapi", "spring",
    "spring boot", "rails", "laravel", ".net", "dotnet", "asp.net", "gin", "fiber", "axum", "tokio",
    "tower", "graphql", "rest", "restful", "microservices", "grpc", "websocket", "celery", "rabbitmq",
    # frontend / mobile
    "react", "react native", "next.js", "nextjs", "redux", "vue", "vue.js", "nuxt", "angular",
    "svelte", "tailwind", "html", "css", "sass", "webpack", "vite", "flutter", "android", "ios",
    "jetpack compose", "swiftui",
    # data / ml / ai
    "pandas", "numpy", "pytorch", "tensorflow", "keras", "scikit-learn", "sklearn", "spark",
    "hadoop", "airflow", "dbt", "snowflake", "databricks", "tableau", "power bi", "looker",
    "machine learning", "deep learning", "nlp", "computer vision", "llm", "langchain", "rag",
    "data engineering", "data science", "data analyst", "etl", "data pipeline", "mlops",
    # data stores
    "postgresql", "postgres", "mysql", "mongodb", "redis", "elasticsearch", "dynamodb", "cassandra",
    "sqlite", "neo4j", "clickhouse", "bigquery",
    # infra / devops / cloud
    "docker", "kubernetes", "k8s", "aws", "gcp", "azure", "kafka", "terraform", "ansible", "jenkins",
    "github actions", "gitlab ci", "ci/cd", "git", "linux", "nginx", "prometheus", "grafana",
    "datadog", "helm", "serverless", "lambda", "cloudflare",
    # web3 / blockchain
    "blockchain", "smart contract", "smart contracts", "ethereum", "evm", "defi", "web3", "solana",
    "foundry", "hardhat", "ethers", "ethers.js", "web3.js", "wagmi", "viem", "layerzero", "uniswap",
    "cross-chain", "bridge", "permit2", "erc-4337", "erc20", "erc721", "erc-721", "nft", "mev", "rpc",
    "the graph", "subgraph", "zk", "zero knowledge", "rollup", "cosmos", "substrate", "move", "aptos",
    # security / qa
    "security", "penetration testing", "pentest", "appsec", "owasp", "cryptography", "auditing",
    "qa", "sdet", "automation testing", "selenium", "cypress", "playwright", "jest", "pytest", "junit",
    # roles / domains
    "backend", "frontend", "full-stack", "fullstack", "full stack", "devops", "sre",
    "platform engineer", "protocol engineer", "site reliability", "distributed systems", "api",
    "product manager", "project manager", "ui/ux", "designer", "data analyst", "business analyst",
    "engineering manager", "tech lead", "solutions architect", "cloud architect",
    # methods / tools
    "agile", "scrum", "jira", "rest api", "oauth", "jwt", "system design", "microservice",
    # data-analyst / BI (India-common) , without these, analyst resumes scored ~0
    "excel", "advanced excel", "vlookup", "pivot table", "pivot tables", "google sheets", "vba",
    "power query", "dax", "sas", "spss", "stata", "qlik", "qlikview", "powerpoint", "ms office",
    "statistics", "data visualization", "google data studio", "ab testing", "a/b testing",
    # finance / accounting / ops (India-common)
    "tally", "sap", "sap fico", "quickbooks", "gst", "tds", "zoho", "zoho books", "netsuite",
    "oracle erp", "accounts payable", "accounts receivable", "reconciliation", "bookkeeping",
    "financial modeling", "fp&a", "supply chain", "logistics", "inventory management", "procurement",
    # marketing / growth
    "seo", "sem", "google ads", "meta ads", "facebook ads", "google analytics", "ga4",
    "google tag manager", "hubspot", "mailchimp", "marketo", "content marketing", "content writing",
    "copywriting", "social media", "email marketing", "performance marketing", "canva",
    # design
    "figma", "sketch", "adobe xd", "photoshop", "illustrator", "indesign", "after effects",
    "wireframing", "prototyping", "user research", "design systems",
    # CRM / sales / support
    "salesforce", "zendesk", "freshdesk", "intercom", "crm", "lead generation", "cold calling",
    "account management", "customer success",
    # enterprise dev / qa (India-common)
    "servicenow", "mulesoft", "informatica", "pega", "selenium grid", "appium", "postman",
    "wordpress", "shopify", "power automate", "sharepoint", "vmware",
    # HR / recruiting
    "recruitment", "talent acquisition", "onboarding", "payroll", "hrms", "workday hcm",
]

SENIORITY = ["intern", "junior", "associate", "mid", "senior", "lead", "staff", "principal", "head"]

# Tokens to drop from skills-section extraction (too generic to match jobs usefully).
_STOP_TOKENS = {
    "communication", "teamwork", "leadership", "problem solving", "time management", "skills",
    "technical skills", "tools", "technologies", "languages", "frameworks", "soft skills",
    "team player", "collaboration", "adaptability", "creativity", "english", "hindi", "and", "etc",
}

_SECTION_HEADERS = re.compile(
    r"^\s*(technical skills|core competencies|skills|technologies|tech stack|tools(?:\s*&\s*technologies)?|"
    r"languages|frameworks|programming languages|expertise|proficiencies)\b\s*:?",
    re.I,
)


def extract_text(path: str) -> str:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(path)
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    if suffix in (".docx", ".doc"):
        import docx
        d = docx.Document(path)
        return "\n".join(par.text for par in d.paragraphs)
    if suffix in (".txt", ".md"):
        return p.read_text(errors="ignore")
    raise ValueError(f"Unsupported resume format: {suffix}")


def _vocab_keywords(low: str) -> list:
    found = []
    for skill in SKILL_VOCAB:
        pattern = r"(?<![a-z0-9])" + re.escape(skill) + r"(?![a-z0-9+#.])"
        if re.search(pattern, low):
            found.append(skill)
    return found


def _split_skill_tokens(s: str) -> list:
    parts = re.split(r"[,/|;•·●\t]|\s-\s|\s–\s", s)
    out = []
    for p in parts:
        p = p.strip().strip(".").lower()
        if 2 <= len(p) <= 28 and len(p.split()) <= 3 and re.search(r"[a-z]", p) and p not in _STOP_TOKENS:
            out.append(p)
    return out


def _section_keywords(text: str) -> list:
    """Pull comma/bullet-separated tokens from skills-style sections (the long tail)."""
    tokens, capture = [], 0
    for raw in text.splitlines():
        line = raw.strip()
        m = _SECTION_HEADERS.match(line)
        if m:
            capture = 4  # grab tokens from the next few lines too
            tokens += _split_skill_tokens(line[m.end():])
            continue
        if capture:
            if not line:
                capture -= 1
                continue
            # a new Title-only header likely ends the section
            if re.match(r"^[A-Z][A-Za-z ]{2,30}$", line) and len(line.split()) <= 3 and ":" not in line:
                capture = 0
                continue
            tokens += _split_skill_tokens(line)
            capture -= 1
    return tokens


def extract_keywords(text: str, limit: int = 45) -> list:
    """Return skill/role keywords from the resume: curated vocab first, then section tokens."""
    low = text.lower()
    ordered = _vocab_keywords(low) + _section_keywords(text)
    seen, out = set(), []
    for k in ordered:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
        if len(out) >= limit:
            break
    return out


def years_experience(text: str):
    """Best-effort total years of experience (e.g. '5+ years'), or None."""
    yrs = [int(m) for m in re.findall(r"(\d{1,2})\s*\+?\s*years?", text.lower())]
    return max(yrs) if yrs else None


# Acronym/spelled-out variants so "AWS" matches "Amazon Web Services", "react" matches "reactjs", etc.
SYNONYMS = {
    "aws": ["amazon web services"], "gcp": ["google cloud"], "react": ["reactjs", "react.js"],
    "node": ["node.js", "nodejs"], "ci/cd": ["continuous integration", "cicd", "ci cd"],
    "k8s": ["kubernetes"], "ml": ["machine learning"], "postgres": ["postgresql"],
    "js": ["javascript"], "ts": ["typescript"], "nlp": ["natural language processing"],
    "go": ["golang"], "gcp ": ["google cloud platform"], "power bi": ["powerbi"],
    "sklearn": ["scikit-learn", "scikit learn"], "tf": ["tensorflow"], "ga4": ["google analytics 4"],
    "sem": ["search engine marketing"], "seo": ["search engine optimization"],
    "advanced excel": ["ms excel", "microsoft excel"], "a/b testing": ["ab testing"],
}


def _variants(term):
    out = {term}
    for k, vs in SYNONYMS.items():
        if term == k:
            out.update(vs)
        elif term in vs:
            out.add(k); out.update(vs)
    return out


def _count(term, text):
    return len(re.findall(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9+#.])", text))


def jd_keywords(jd_text: str) -> list:
    """Hard skills the JD asks for, with frequency + tier (core if mentioned 2+ times). Deterministic."""
    low = (jd_text or "").lower()
    found = {}
    for skill in SKILL_VOCAB:
        c = _count(skill, low)
        if c:
            found[skill] = c
    return [{"term": t, "count": c, "tier": "core" if c >= 2 else "nice"}
            for t, c in sorted(found.items(), key=lambda kv: -kv[1])]


def flatten_resume(rj: dict) -> str:
    parts = [rj.get("summary", ""), " ".join(rj.get("skills", []))]
    for e in (rj.get("experience") or []):
        parts.append(e.get("title", ""))
        parts.extend(e.get("bullets") or [])
    for p in (rj.get("projects") or []):  # projects count toward JD coverage too
        parts.append(p.get("name", "")); parts.append(p.get("stack", ""))
        parts.extend(p.get("bullets") or [])
    for s in (rj.get("sections") or []):
        parts.extend(s.get("items") or [])
    return " ".join(p for p in parts if p).lower()


def ats_job_match(resume_json: dict, jd_text: str) -> dict:
    """JD-aware 0-100 ATS score = keyword coverage (50) + writing quality (50). Fully deterministic.
    Returns present/missing skills so the builder can show exactly what to add."""
    from .resume_export import ats_health
    jdk = jd_keywords(jd_text)
    flat = flatten_resume(resume_json)
    present, missing, have, total = [], [], 0, 0
    core_have = core_tot = nice_have = nice_tot = 0
    for k in jdk:
        is_core = k["tier"] == "core"
        w = 2 if is_core else 1
        total += w
        if is_core: core_tot += 1
        else: nice_tot += 1
        hit = any(_count(v, flat) for v in _variants(k["term"]))
        if hit:
            present.append(k["term"]); have += w
            if is_core: core_have += 1
            else: nice_have += 1
        else:
            missing.append({"term": k["term"], "tier": k["tier"]})
    coverage = round(50 * have / total) if total else 30
    health = ats_health(resume_json).get("score", 0)
    quality = round(0.5 * health)
    # credible sub-scores (0-100 each) so the number is explainable, not a vanity metric
    subscores = {
        "must_have": round(100 * core_have / core_tot) if core_tot else 100,
        "nice_to_have": round(100 * nice_have / nice_tot) if nice_tot else 100,
        "resume_quality": int(health),
    }
    return {"score": coverage + quality, "coverage": coverage, "quality": quality,
            "present": present, "missing": missing, "jd_skill_count": total,
            "subscores": subscores}


_SECTION_HEADINGS = {
    "experience": r"(work\s+|professional\s+|relevant\s+)?experience|employment(\s+history)?|work\s+history",
    "education": r"education|academics?|qualifications?",
    "projects": r"projects?|personal\s+projects|side\s+projects|key\s+projects",
    "skills": r"(technical\s+)?skills?|technologies|tech\s+stack|core\s+competencies",
    "summary": r"summary|objective|about(\s+me)?|profile",
}
_BULLET_RE = re.compile(r"^\s*[\-•▪●‣⁃∙*·•▪◦‣·]+\s*")
_DATE_RE = re.compile(r"\b(19|20)\d{2}\b|\b(present|current|ongoing)\b", re.I)
_DEGREE_RE = re.compile(
    r"\b(b\.?\s?tech|m\.?\s?tech|b\.?\s?e\b|m\.?\s?e\b|b\.?\s?sc|m\.?\s?sc|b\.?\s?a\b|m\.?\s?a\b|"
    r"bachelor|master|ph\.?\s?d|mba|diploma|b\.?\s?com|m\.?\s?com|bca|mca|degree)\b", re.I)
_SCHOOL_RE = re.compile(r"(university|college|institute|school|academy|\biit\b|\bnit\b|\biiit\b)", re.I)


def _debullet(s):
    return _BULLET_RE.sub("", s).strip()


def _split_sections(text: str) -> dict:
    """Split a resume into {section_key: [lines]} by detecting common section headings."""
    out = {"header": []}
    cur = "header"
    for ln in (text or "").splitlines():
        s = ln.strip()
        key = None
        if s and len(s) <= 40:
            low = s.lower().strip(" :|-").strip()
            for k, pat in _SECTION_HEADINGS.items():
                if re.fullmatch(pat, low):
                    key = k
                    break
        if key:
            cur = key
            out.setdefault(cur, [])
        else:
            out.setdefault(cur, []).append(ln)
    return out


def _looks_like_role_header(s: str) -> bool:
    if _BULLET_RE.match(s) or len(s) > 100:
        return False
    return bool(_DATE_RE.search(s)) or "|" in s or " at " in f" {s.lower()} "


def _extract_entries(lines: list) -> list:
    """Parse an experience/projects section into [{title, company, dates, bullets[]}]. Splits on
    role-header lines (those carrying a date / pipe / 'at'); collects bullet + substantial lines under
    each. Falls back to a single entry so bullets are never lost (this is what unsticks the score)."""
    entries, cur, bullets = [], None, []

    def flush():
        nonlocal cur, bullets
        if bullets or cur:
            e = cur or {"title": "Experience", "company": "", "dates": "", "bullets": []}
            e["bullets"] = (e.get("bullets") or []) + bullets
            if e["bullets"]:
                entries.append(e)
        cur, bullets = None, []

    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if _looks_like_role_header(s):
            flush()
            dm = re.search(r"((?:[A-Za-z]{3,9}\.?\s*)?(?:19|20)\d{2}\s*(?:[-–to]+\s*"
                           r"(?:present|current|(?:[A-Za-z]{3,9}\.?\s*)?(?:19|20)\d{2}))?)", s, re.I)
            dates = dm.group(1).strip() if dm else ""
            core = (s[:dm.start()] + s[dm.end():]) if dm else s
            parts = [p.strip() for p in re.split(r"\s*[|·]\s*|\s+(?:at|@)\s+", core) if p.strip()]
            cur = {"title": (parts[0] if parts else core).strip(" -,|")[:120],
                   "company": (parts[1][:120] if len(parts) > 1 else ""), "dates": dates[:40], "bullets": []}
        elif _BULLET_RE.match(s):
            b = _debullet(s)
            if len(b) >= 6:
                bullets.append(b[:300])
        elif 25 <= len(s) <= 240 and len(s.split()) >= 4 and not s.endswith(":"):
            bullets.append(s[:300])
    flush()
    return entries[:10]


def _extract_education(lines: list) -> list:
    out, seen = [], set()
    for ln in lines:
        s = _debullet(ln.strip())
        if not s or len(s) > 180:
            continue
        if _DEGREE_RE.search(s) or _SCHOOL_RE.search(s):
            key = s.lower()
            if key in seen:
                continue
            seen.add(key)
            dm = re.search(r"(19|20)\d{2}", s)
            out.append({"degree": s[:160], "school": "", "dates": dm.group(0) if dm else ""})
    return out[:6]


def heuristic_structure(text: str) -> dict:
    """Build a structured resume WITHOUT an LLM (fallback so upload/parse never hard-fails). Now also
    extracts EXPERIENCE bullets, PROJECTS, and EDUCATION from the raw text , without these the quality
    score was stuck low (no bullets to grade) for any resume parsed without the LLM."""
    email = ""
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", text)
    if m:
        email = m.group(0)
    phone = ""
    m = re.search(r"(\+?\d[\d\s\-()]{8,15}\d)", text)
    if m:
        phone = m.group(1).strip()
    links = re.findall(r"(?:https?://)?(?:www\.)?(?:linkedin\.com/in/[\w\-/]+|github\.com/[\w\-]+)", text)
    links = ["https://" + l if not l.startswith("http") else l for l in links][:3]
    name = ""
    for ln in text.splitlines():
        s = ln.strip()
        if s and "@" not in s and not re.search(r"\d{4}", s) and len(s.split()) <= 5 and len(s) < 50:
            name = s
            break
    summary = ""
    ms = re.search(r"(?:summary|objective|about me|profile)\s*:?\s*\n+(.{40,400})", text, re.I)
    if ms:
        summary = " ".join(ms.group(1).split())

    sec = _split_sections(text)
    experience = _extract_entries(sec.get("experience", []))
    education = _extract_education(sec.get("education", []))
    projects = []
    for e in _extract_entries(sec.get("projects", [])):
        projects.append({"name": e.get("title") or "Project", "stack": e.get("company", ""),
                         "dates": e.get("dates", ""), "bullets": e.get("bullets", [])})
    # skills: prefer an explicit skills section, else mine the whole doc
    skill_text = " ".join(sec.get("skills", [])) or text
    skills = extract_keywords(skill_text)[:30] or extract_keywords(text)[:30]
    return {
        "name": name, "email": email, "phone": phone, "links": links,
        "summary": summary, "skills": skills,
        "experience": experience, "projects": projects, "education": education,
    }


def ensure_structure(rj: dict, text: str):
    """Retroactively backfill a stored resume whose experience/projects/education were dropped by an
    earlier LLM-less parse, so the quality score reflects the real resume. Returns (rj, changed)."""
    if not rj or not text:
        return rj, False
    changed = False
    if not (rj.get("experience")):
        h = heuristic_structure(text)
        if h.get("experience"):
            rj["experience"] = h["experience"]; changed = True
        if not rj.get("projects") and h.get("projects"):
            rj["projects"] = h["projects"]; changed = True
        if not rj.get("education") and h.get("education"):
            rj["education"] = h["education"]; changed = True
    return rj, changed


def profile_from_resume(path: str) -> dict:
    text = extract_text(path)
    keywords = extract_keywords(text)
    seniority = [s for s in SENIORITY if re.search(r"(?<![a-z])" + s + r"(?![a-z])", text.lower())]
    return {"keywords": keywords, "seniority": seniority, "years": years_experience(text),
            "text": text, "text_len": len(text)}
