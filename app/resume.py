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
    return " ".join(p for p in parts if p).lower()


def ats_job_match(resume_json: dict, jd_text: str) -> dict:
    """JD-aware 0-100 ATS score = keyword coverage (50) + writing quality (50). Fully deterministic.
    Returns present/missing skills so the builder can show exactly what to add."""
    from .resume_export import ats_health
    jdk = jd_keywords(jd_text)
    flat = flatten_resume(resume_json)
    present, missing, have, total = [], [], 0, 0
    for k in jdk:
        w = 2 if k["tier"] == "core" else 1
        total += w
        hit = any(_count(v, flat) for v in _variants(k["term"]))
        if hit:
            present.append(k["term"]); have += w
        else:
            missing.append({"term": k["term"], "tier": k["tier"]})
    coverage = round(50 * have / total) if total else 30
    quality = round(0.5 * ats_health(resume_json).get("score", 0))
    return {"score": coverage + quality, "coverage": coverage, "quality": quality,
            "present": present, "missing": missing, "jd_skill_count": total}


def heuristic_structure(text: str) -> dict:
    """Build a structured resume WITHOUT an LLM (fallback so upload/parse never hard-fails).
    Pulls contact + skills reliably; leaves experience for the user (or the LLM) to fill."""
    low = text
    email = ""
    m = re.search(r"[\w.+-]+@[\w-]+\.[\w.-]+", low)
    if m:
        email = m.group(0)
    phone = ""
    m = re.search(r"(\+?\d[\d\s\-()]{8,15}\d)", low)
    if m:
        phone = m.group(1).strip()
    links = re.findall(r"(?:https?://)?(?:www\.)?(?:linkedin\.com/in/[\w\-/]+|github\.com/[\w\-]+)", low)
    links = ["https://" + l if not l.startswith("http") else l for l in links][:3]
    # name: first non-empty line that isn't an email/phone/heading
    name = ""
    for ln in text.splitlines():
        s = ln.strip()
        if s and "@" not in s and not re.search(r"\d{4}", s) and len(s.split()) <= 5 and len(s) < 50:
            name = s
            break
    # summary: text right after a "summary/objective/about" heading, else the first real paragraph
    summary = ""
    ms = re.search(r"(?:summary|objective|about me|profile)\s*:?\s*\n+(.{40,400})", text, re.I)
    if ms:
        summary = " ".join(ms.group(1).split())
    return {
        "name": name, "email": email, "phone": phone, "links": links,
        "summary": summary, "skills": extract_keywords(text)[:30],
        "experience": [], "education": [],
    }


def profile_from_resume(path: str) -> dict:
    text = extract_text(path)
    keywords = extract_keywords(text)
    seniority = [s for s in SENIORITY if re.search(r"(?<![a-z])" + s + r"(?![a-z])", text.lower())]
    return {"keywords": keywords, "seniority": seniority, "years": years_experience(text),
            "text": text, "text_len": len(text)}
