"""Score jobs against a user's keyword profile and location preference."""
import re


def _contains(text: str, term: str) -> bool:
    return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text) is not None


# Synonym-aware matching: a resume keyword should match its real-world spellings in a JD
# ("node" <-> "node.js", "react" <-> "reactjs", "postgres" <-> "postgresql", "ml" <-> "machine
# learning", "k8s" <-> "kubernetes"). We reuse the synonym table already maintained in resume.py
# (cached per term so the hot matching loop stays cheap).
_VARIANT_CACHE = {}


def _variants_of(term: str) -> tuple:
    v = _VARIANT_CACHE.get(term)
    if v is None:
        try:
            from .resume import _variants
            v = tuple(_variants(term))
        except Exception:
            v = (term,)
        _VARIANT_CACHE[term] = v
    return v


def _matches(text: str, term: str) -> bool:
    return any(_contains(text, v) for v in _variants_of(term))


def _occurrences(text: str, term: str) -> int:
    n = 0
    for v in _variants_of(term):
        n += len(re.findall(r"(?<![a-z0-9])" + re.escape(v) + r"(?![a-z0-9])", text))
    return n


REMOTE_TERMS = ("remote", "anywhere", "worldwide", "global", "distributed")

# So "india" as a preference also matches jobs listed in Indian cities / "IN".
INDIA_CITIES = (
    "india", "bengaluru", "bangalore", "mumbai", "delhi", "ncr", "gurgaon", "gurugram",
    "noida", "hyderabad", "pune", "chennai", "kolkata", "ahmedabad", "indore", "jaipur",
    "kochi", "coimbatore", "chandigarh", "nagpur", "lucknow", "surat", "vadodara",
    "trivandrum", "thiruvananthapuram", "mysore", "mysuru", "visakhapatnam", "vizag",
    "bhubaneswar", "remote india", "india remote",
)
COUNTRY_ALIASES = {"india": INDIA_CITIES}

# Locations that signal a role is tied to a foreign country/region (low interview odds for an
# India-based applicant). Used to drop "Remote, Germany" / "Berlin" / "US only" for India users.
FOREIGN_TOKENS = (
    "germany", "deutschland", "berlin", "munich", "münchen", "hamburg", "frankfurt", "cologne",
    "stuttgart", "düsseldorf", "united kingdom", "england", "london", "manchester", "scotland",
    "united states", "u.s.", "usa", "new york", "san francisco", "los angeles", "seattle",
    "austin", "boston", "chicago", "denver", "california", "texas", "canada", "toronto",
    "vancouver", "europe", "european", "eu only", "emea", "france", "paris", "netherlands",
    "amsterdam", "spain", "madrid", "barcelona", "portugal", "lisbon", "poland", "warsaw",
    "ireland", "dublin", "switzerland", "zurich", "sweden", "stockholm", "australia", "sydney",
    "melbourne", "japan", "tokyo", "brazil", "mexico", "nigeria", "kenya", "south africa",
    "us only", "us-only", "us based", "u.s. only", "usa only", "uk only", "eu/uk", "eea",
)


def _has(loc, tokens):
    return any(t and t in loc for t in tokens)


def job_region(job_location: str) -> str:
    """Classify a job's location: 'india' | 'global' (true remote) | 'foreign' | 'unknown'."""
    loc = (job_location or "").lower().strip()
    if _has(loc, INDIA_CITIES):
        return "india"
    if _has(loc, FOREIGN_TOKENS):
        return "foreign"
    if not loc or loc in REMOTE_TERMS or _has(loc, REMOTE_TERMS):
        return "global"
    return "unknown"


def location_ok(job_location: str, locations: list) -> bool:
    """True if the job's location is acceptable. For India-focused users we DROP foreign-located
    roles (incl. 'Remote, Germany') and keep India + genuinely-global-remote, so people get jobs
    they can actually be interviewed for, not EU/US-only postings."""
    if not locations:
        return True
    wants = [w.lower().strip() for w in locations]
    india_user = any(w == "india" or w in INDIA_CITIES for w in wants)
    region = job_region(job_location)
    if india_user:
        # keep India + true global remote + unknown (vaguely-located remote roles); drop only clearly
        # foreign postings. The overlap floor + neutral location weight keep 'unknown' honest.
        return region in ("india", "global", "unknown")
    loc = (job_location or "").lower().strip()
    if region in ("global",):
        return True
    for w in wants:
        aliases = COUNTRY_ALIASES.get(w, (w,))
        if _has(loc, aliases) or (w in ("remote", "anywhere") and region == "global"):
            return True
    return False


def years_required(text: str) -> int:
    """Best-effort: highest 'N+ years' figure mentioned in the JD (0 if none)."""
    nums = re.findall(r"(\d{1,2})\s*\+?\s*(?:years|yrs)", text.lower())
    return max((int(n) for n in nums), default=0)


# Seniority implied by the job TITLE, even when the JD never states "N+ years" (e.g. "Senior Data
# Engineer"). Junior markers win (an Associate/Junior/Intern title is entry-level regardless of other
# words); otherwise the strongest senior marker sets an implied experience bar. Unmarked titles imply
# nothing (0) , we then rely on the stated years.
# Note: "lead" and "head" are NOT here , they're handled by the scoped regexes below, so "Lead
# Generation Specialist" / "Head of Growth" aren't mis-read as 6-9 yr engineering roles.
_TITLE_SENIOR = {"principal": 9, "staff": 8, "architect": 8, "distinguished": 10, "fellow": 10,
                 "director": 10, "vp": 12, "vice president": 12, "senior": 5, "sr": 5}
_TITLE_JUNIOR = {"intern": 0, "internship": 0, "trainee": 0, "apprentice": 0, "fresher": 0,
                 "graduate": 1, "entry": 1, "junior": 1, "jr": 1, "associate": 2}
# "lead" only counts as seniority in a job-level context (tech/team/eng lead, lead engineer...), NOT
# "lead generation"/"lead gen". "head" only in "head of <X>".
_LEAD_RE = re.compile(r"\b(tech|technical|team|engineering|eng|dev|software|squad|project|product|"
                      r"design|data|qa|platform|staff|group)\s+lead\b|"
                      r"\blead\s+(engineer|developer|architect|designer|scientist|analyst|"
                      r"consultant|sre|devops|qa)\b")
_HEAD_RE = re.compile(r"\bhead\s+of\b")


def _tword(text: str, tok: str) -> bool:
    return re.search(r"(?<![a-z])" + re.escape(tok) + r"(?![a-z])", text) is not None


def title_level(title: str) -> int:
    """Implied minimum years of experience from a job TITLE (0 if none/entry-level)."""
    t = (title or "").lower()
    for tok, lvl in _TITLE_JUNIOR.items():
        if _tword(t, tok):
            return lvl  # entry-level marker wins outright
    best = 0
    for tok, lvl in _TITLE_SENIOR.items():
        if _tword(t, tok):
            best = max(best, lvl)
    if _LEAD_RE.search(t):
        best = max(best, 6)
    if _HEAD_RE.search(t):
        best = max(best, 9)
    return best


def required_experience(job: dict) -> int:
    """Experience a role likely wants: max of stated 'N+ years' and the title's implied level."""
    return max(years_required(job.get("description", "") or ""),
               title_level(job.get("title", "") or ""))


_INTERN_RE = re.compile(r"\bintern(ship)?\b|\bco-?op\b")


def is_internship(job: dict) -> bool:
    """True for internship/co-op postings (by source tag or title), so matching can route them to
    early-career users and keep them away from clearly-senior ones."""
    if (job.get("source") or "").startswith("internships") or job.get("category") == "Internship":
        return True
    return bool(_INTERN_RE.search((job.get("title") or "").lower()))


INTERN_MAX_YEARS = 3  # don't show internships to users with more than this much experience


SENIORITY_DROP_GAP = 3  # hard-drop a job that wants >= this many more years than the user has


def over_leveled(req_years, user_years) -> bool:
    """True if a job is materially too senior for the user, so it should be HARD-DROPPED (not just
    penalized). Generalizes the old 'a fresher shouldn't see Senior roles' rule to EVERY level: a
    3-yr user shouldn't get flooded with Staff/Principal/Lead either (the prior `uyears<=2 and req>=5`
    check left mid-level users unprotected). A small gap (a stretch role) is kept and softly
    penalized by the score instead. With gap=3: 0yr drops 3+, 2yr drops 5+ (=old behaviour), 3yr
    drops 6+ (lead/staff), 5yr drops 8+ (staff/principal)."""
    try:
        return (int(req_years) - int(user_years)) >= SENIORITY_DROP_GAP
    except (TypeError, ValueError):
        return False


# Ordered most-specific -> most-generic (first match wins). Title is weighted: a term in the title
# decides the tag even if a more-generic term appears in the body. Keep tags granular (LeetCode-style)
# so "Other" is rare.
CATEGORY_RULES = [
    ("Blockchain", ["solidity", "smart contract", "blockchain", "web3", "defi", "evm", "ethereum",
                    "crypto", "protocol engineer", "zero knowledge", "zk-rollup", "smart contracts"]),
    ("AI / ML", ["machine learning", "ml engineer", "deep learning", "computer vision", "llm",
                 "mlops", "ai engineer", "generative ai", "nlp engineer", "applied scientist"]),
    ("Data Science", ["data scientist", "data science", "quantitative researcher", "statistician"]),
    ("Data Engineering", ["data engineer", "data engineering", "etl developer", "data platform",
                          "data pipeline", "analytics engineer"]),
    ("Data Analyst", ["data analyst", "business analyst", "bi analyst", "business intelligence"]),
    ("Mobile", ["mobile developer", "mobile engineer", "android developer", "ios developer",
                "react native", "flutter developer", "swiftui", "jetpack compose"]),
    ("DevOps / SRE", ["devops", "sre", "site reliability", "platform engineer", "infrastructure engineer",
                      "release engineer"]),
    ("Cloud", ["cloud engineer", "cloud architect", "solutions architect", "cloud infrastructure"]),
    ("Security", ["security engineer", "appsec", "penetration test", "pentest", "infosec",
                  "cybersecurity", "security analyst", "soc analyst"]),
    ("QA / Test", ["qa engineer", "sdet", "quality assurance", "test engineer", "automation tester"]),
    ("Engineering Manager", ["engineering manager", "tech lead", "team lead", "staff engineer",
                             "principal engineer", "director of engineering", "head of engineering"]),
    ("Product", ["product manager", "product owner", "associate product", "product analyst"]),
    ("Design", ["ux designer", "ui designer", "ui/ux", "product designer", "graphic designer",
                "ux researcher"]),
    ("Embedded", ["embedded", "firmware", "rtos", "fpga", "verilog", "device driver"]),
    ("Game Dev", ["game developer", "game engineer", "unity developer", "unreal engine", "gameplay"]),
    ("Sales", ["account executive", "sales development", "sdr", "bdr", "sales manager",
               "business development", "inside sales", "sales representative"]),
    ("Marketing", ["marketing manager", "growth marketer", "content marketer", "seo specialist",
                   "performance marketing", "social media manager", "brand manager", "digital marketing"]),
    ("Finance", ["financial analyst", "accountant", "finance manager", "fp&a", "investment analyst",
                 "controller", "bookkeeper"]),
    ("Operations", ["operations manager", "operations associate", "supply chain", "logistics",
                    "program manager", "project manager"]),
    ("Customer Success", ["customer success", "customer support", "account manager",
                          "support engineer", "technical support"]),
    ("HR / Recruiting", ["recruiter", "talent acquisition", "human resources", "hr manager",
                         "people operations", "hr business partner"]),
    ("Full-Stack", ["full stack", "full-stack", "fullstack", "mern", "mean stack"]),
    ("Frontend", ["frontend", "front-end", "front end", "react developer", "next.js", "vue.js",
                  "angular developer", "ui engineer", "ui developer", "web developer"]),
    ("Backend", ["backend", "back-end", "back end", "api developer", "server-side", "golang",
                 "node.js", "django", "spring boot", "microservices", "ruby on rails"]),
]


def _cat_find(text: str, term: str) -> int:
    """Word-boundary search (so 'crypto' doesn't match 'cryptography'); returns match start or -1."""
    m = re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text)
    return m.start() if m else -1


def categorize(job: dict) -> str:
    """Assign a role category. WORD-BOUNDARY matched (no substring false-hits). If any rule's term is
    in the TITLE, the winner is the one whose term appears EARLIEST in the title (the head-of-title is
    the primary role, e.g. 'Data Scientist - Machine Learning' -> Data Science, not AI/ML). Otherwise
    fall back to the rule with the most BODY hits. Rule order breaks ties."""
    title = (job.get("title", "") or "").lower()
    body = title + " " + (job.get("description", "") or "").lower()
    best_label, best_pos = None, 1 << 30
    for label, terms in CATEGORY_RULES:
        pos = min((p for p in (_cat_find(title, t) for t in terms) if p >= 0), default=-1)
        if pos >= 0 and pos < best_pos:
            best_label, best_pos = label, pos
    if best_label:
        return best_label
    # no title hit -> most body hits wins (rule order breaks ties)
    best_label, best_hits = None, 0
    for label, terms in CATEGORY_RULES:
        hits = sum(1 for t in terms if _cat_find(body, t) >= 0)
        if hits > best_hits:
            best_label, best_hits = label, hits
    return best_label or "Other"


def categories_for_resume(text: str, keywords: list = None, top: int = 4) -> list:
    """Suggest role categories from a resume by counting CATEGORY_RULES term hits (word-boundary, so
    'cryptography' no longer suggests Blockchain). Ranked, capped to `top`. Powers the subscribe
    'upload resume -> roles auto-selected' step; the user edits the result."""
    blob = ((text or "") + " " + " ".join(keywords or [])).lower()
    if not blob.strip():
        return []
    scores = {}
    for label, terms in CATEGORY_RULES:
        s = sum(len(re.findall(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", blob)) for t in terms)
        if s:
            scores[label] = s
    return sorted(scores, key=lambda k: -scores[k])[:top]


def score_job(job: dict, keywords: list) -> tuple:
    """Return (score, matched_keywords). Synonym-aware skill overlap, with two emphases:
    - title hits weighted heavily (a role in the title is a far stronger fit signal than a keyword
      buried in the description), and
    - a "core" bonus for skills the JD mentions repeatedly (2+ times), so a job built around your
      primary stack outranks one that merely brushes a couple of incidental tags.
    Matching expands each keyword to its variants (node<->node.js, react<->reactjs, etc.)."""
    title = (job.get("title", "") or "").lower()
    text = title + " " + (job.get("description", "") or "").lower()
    matched, title_bonus, core = [], 0, 0
    for k in keywords:
        vs = _variants_of(k)
        if any(_contains(text, v) for v in vs):
            matched.append(k)
            if any(_contains(title, v) for v in vs):
                title_bonus += 1  # in the title: strongest signal
            elif sum(len(re.findall(r"(?<![a-z0-9])" + re.escape(v) + r"(?![a-z0-9])", text)) for v in vs) >= 2:
                core += 1  # repeated in the body: central to the role
    core_bonus = min(3, core)  # cap so the score scale stays compatible with MIN_SCORE / cutoffs
    return len(matched) + 2 * title_bonus + core_bonus, matched


def core_overlap(job: dict, matched: list) -> int:
    """How many of the MATCHED skills are CORE to this JD , named in the title or repeated 2+ times
    in the body, i.e. central to the role rather than an incidental tag. This is the importance
    signal Jobscan-style matching needs: a job built around your top 2 skills should rank high even
    with modest breadth, while a job that merely brushes one common keyword should not."""
    if not matched:
        return 0
    title = (job.get("title", "") or "").lower()
    body = title + " " + (job.get("description", "") or "").lower()
    n = 0
    for k in matched:
        if _matches(title, k) or _occurrences(body, k) >= 2:
            n += 1
    return n


import math as _math

# Blended selection-score weights (the prior, before any per-user learning). Content dominates so a
# brand-new user's ranking ~= the old keyword behaviour; learned/global signals only re-sort within.
SCORE_WEIGHTS = {"content": 4.2, "pref": 1.4, "seniority": 1.2, "location": 1.0,
                 "recency": 0.5, "collab": 0.7, "trending": 0.5, "semantic": 1.3, "source": 1.0,
                 "category": 1.5, "prioritize": 1.6}
# Baseline subtracted from the logistic input so a SHALLOW match (one common keyword like "java")
# scores low, not ~50%+. Tuned with content=4.2 so 1 skill ~30%, 3 skills ~70%, 5+ skills ~90%.
SCORE_BIAS = 2.8


def pref_update(theta: dict, phi: dict, reward: float, lr=0.1, l2=1e-3) -> dict:
    """Pure online-logistic SGD step (no IO). Replay events through this to rebuild a user's vector
    idempotently. reward in [-1,1]; label y=1 if reward>0. Returns a new pruned theta."""
    if not phi:
        return theta
    y = 1.0 if reward > 0 else 0.0
    z = sum(theta.get(k, 0.0) * v for k, v in phi.items())
    p = 1.0 / (1.0 + _math.exp(-max(-30, min(30, z))))
    err = (y - p) * abs(reward)
    out = dict(theta)
    for k, v in phi.items():
        out[k] = out.get(k, 0.0) * (1 - lr * l2) + lr * err * v
    return {k: round(w, 4) for k, w in out.items() if abs(w) > 1e-3}


def pref_features(job: dict) -> dict:
    """Sparse, interpretable feature dict phi(j) for the per-user preference model. Category is the
    main learnable signal (we can derive it from events); skills/region add explainability."""
    phi = {}
    cat = job.get("category") or "Other"
    phi["cat:" + cat] = 1.0
    reg = job.get("region")
    if reg:
        phi["region:" + reg] = 1.0
    for k in (job.get("matched") or [])[:8]:
        phi["skill:" + k] = 1.0
    return phi


def _recency_unit(job: dict) -> float:
    """0..1 freshness from posted age (half-life ~14d). Unknown age -> 0.4 for company boards (they
    expire server-side, so unknown != stale), but 0.25 for aggregator feeds (Adzuna/JSearch), where
    unknown age usually means an old, possibly-closed listing."""
    try:
        from .db import posted_age_days
        age = posted_age_days(job.get("posted_at"))
    except Exception:
        age = None
    if age is None:
        src = (job.get("source") or "").split(":")[0]
        return 0.25 if src in ("adzuna", "jsearch") else 0.4
    return _math.exp(-max(0, age) / 14.0)


def blended_score(job: dict, ctx: dict) -> tuple:
    """Probability-of-selection score in [15,100] + a contributions dict for the 'why' string.
    Expects job already through rank_matches (has raw_score, region, category, matched). ctx carries
    the per-user/learned signals: theta, trending, collab, user_top_cats, uyears, india_user."""
    raw = job.get("raw_score") or 0
    n = len(job.get("matched") or [])
    # Content = breadth of overlap WEIGHTED by importance. A single common keyword (e.g. "java") must
    # NOT look like a strong match (breadth term stays small), but a job built around your top skills
    # , the ones named in its title or repeated through the JD (core_overlap) , should score high even
    # with modest breadth. This fixes both over-matching (1 generic tag) and under-matching (2 core skills).
    core = job.get("core_overlap")
    if core is None:
        core = core_overlap(job, job.get("matched") or [])
    # (title presence is captured once via `core`; we don't also fold raw-n back in , that triple-counted it)
    C = min(1.0, n / 6.0 + core * 0.20)
    # learned preference
    theta = ctx.get("theta") or {}
    phi = pref_features(job)
    Pf = _math.tanh(sum(theta.get(k, 0.0) * v for k, v in phi.items())) if theta else 0.0
    # seniority fit (asymmetric: under-qualified hurts; title-aware via req_years from rank_matches)
    yrs = job.get("req_years")
    if yrs is None:
        yrs = required_experience(job)
    gap = max(0, yrs - (ctx.get("uyears") or 0))
    S = 0.3 if gap <= 0 else (-0.3 if gap <= 2 else (-0.8 if gap < 6 else -1.6))
    # location/region
    region = job.get("region") or job_region(job.get("location", ""))
    if ctx.get("india_user"):
        L = 0.5 if region == "india" else (0.2 if region == "global" else 0.0)
    else:
        L = 0.2
    # recency
    R = _recency_unit(job) - 0.3
    # collaborative: how much users-like-you favour this job's category
    cat = job.get("category") or "Other"
    collab = ctx.get("collab") or {}
    top = ctx.get("user_top_cats") or []
    Co = 0.0
    if top and collab:
        vals = [collab.get(t, {}).get(cat, 0.0) for t in top]
        Co = max(vals) if vals else 0.0
    # trending
    Tr = (ctx.get("trending") or {}).get("cat", {}).get(cat, 0.0)
    # semantic: resume<->JD embedding similarity, zero-centered on the batch baseline (cosine sims
    # sit in a narrow high band, so we subtract the candidate-set median and amplify) so it re-orders
    # WITHIN a user's candidates instead of inflating everyone. Neutral (0) if the job isn't embedded.
    sem = job.get("_sem")
    base = ctx.get("sem_baseline")
    Sem = 0.0
    if isinstance(sem, float) and base is not None:
        Sem = max(-1.0, min(1.0, (sem - base) * 4.0))

    # role/domain relevance: + when the job's role is one the user wants; a PENALTY when it's a
    # different domain (this is what stops "blockchain role -> data person" type mismatches, esp. on
    # Browse where roles aren't hard-filtered).
    ucats = ctx.get("user_cats") or ()
    jobcat = job.get("category") or "Other"
    if ucats:
        Cat = 0.6 if jobcat in ucats else -0.5
    else:
        Cat = 0.0
    # prioritize: titles/keywords the user explicitly WANTS surfaced (from the preferences tab) , a
    # boost when they appear in the title (strong) or body (mild). Lets a Java/Python dev say "show me
    # ML/data roles, not Java Developer" and have those rise.
    pri = ctx.get("prioritize") or []
    Pri = 0.0
    if pri:
        _t = (job.get("title", "") or "").lower()
        _d = (job.get("description", "") or "").lower()
        if any(p in _t for p in pri):
            Pri = 1.0
        elif any(p in _d for p in pri):
            Pri = 0.4
    # source quality: a hardcoded PRIOR (company boards freshest + rarely closed; Adzuna stalest),
    # nudged by what we've LEARNED , which sources actually land jobs for users (ctx.source_q, the net
    # positive-action rate per board). So a source that performs gets promoted past its prior, and one
    # that only draws dismissals gets buried, even if the guess said otherwise.
    src = (job.get("source") or "").split(":")[0]
    if src in ("greenhouse", "lever", "ashby", "smartrecruiters", "workday"):
        Sq = 0.5
    elif src == "adzuna":
        Sq = -0.6
    else:
        Sq = 0.0
    adj = (ctx.get("source_q") or {}).get(src)
    if adj is not None:
        Sq = max(-1.2, min(1.2, Sq + adj * 0.6))  # learned net-action rate moves the prior, doesn't replace it

    # Require REAL skill overlap before the circumstantial bonuses (source/location/category) can lift
    # the score. Otherwise one common keyword on a company board in India could fake a strong match.
    # Penalties (Adzuna, domain-mismatch) are kept regardless.
    strong_overlap = (n >= 2) or (core >= 1)
    if not strong_overlap:
        if Sq > 0:
            Sq = 0.0
        if L > 0:
            L = 0.0
        if Cat > 0:
            Cat = 0.0

    w = SCORE_WEIGHTS
    contrib = {"content": w["content"] * C, "pref": w["pref"] * Pf, "seniority": w["seniority"] * S,
               "location": w["location"] * L, "recency": w["recency"] * R,
               "collab": w["collab"] * Co, "trending": w["trending"] * Tr,
               "semantic": w["semantic"] * Sem, "source": w["source"] * Sq,
               "category": w["category"] * Cat, "prioritize": w["prioritize"] * Pri}
    z = sum(contrib.values()) - SCORE_BIAS
    score = int(round(100 / (1 + _math.exp(-max(-30, min(30, z))))))
    score = max(15, min(100, score))
    return score, contrib


_CONTRIB_LABEL = {"content": "skills match", "pref": "roles you favour", "seniority": "seniority fit",
                  "location": "location fit", "recency": "freshly posted", "collab": "popular with similar users",
                  "trending": "trending role", "semantic": "matches your resume",
                  "source": "from a company board", "category": "a role you chose",
                  "prioritize": "a role you want"}


def blended_reason(job: dict, score: int, contrib: dict) -> str:
    tier = "Strong fit" if score >= 75 else ("Good fit" if score >= 50 else "Possible fit")
    pos = sorted([(v, k) for k, v in contrib.items() if v > 0.05], reverse=True)[:3]
    parts = [_CONTRIB_LABEL[k] for _, k in pos]
    top = ", ".join((job.get("matched") or [])[:4])
    msg = f"{tier} ({score}/100)."
    if parts:
        msg += " " + ", ".join(parts) + "."
    if top:
        msg += f" Matches: {top}."
    if contrib.get("seniority", 0) <= -0.5:
        msg += " Note: asks more experience than you list."
    return msg


def rank_matches(jobs: list, keywords: list, locations: list, min_score: int,
                 user_years: int = 0, user_cats: list = None) -> list:
    """Return jobs that clear min_score and pass the location filter, sorted by fit desc.

    user_years (from the resume) drives a seniority-GAP penalty: a job asking far more experience
    than the candidate has is deprioritized, but a senior candidate is NOT penalized for senior roles.

    user_cats (the user's chosen role categories): a job IN one of those roles is relevant by role even
    with thin keyword overlap, so it bypasses the keyword overlap floor (this is why Browse, which
    filters by role, surfaced good jobs the matcher was dropping)."""
    wants = [w.lower().strip() for w in (locations or [])]
    india_user = any(w == "india" or w in INDIA_CITIES for w in wants)
    cats_set = set(user_cats or [])
    results = []
    for job in jobs:
        if not location_ok(job.get("location", ""), locations):
            continue
        score, matched = score_job(job, keywords)
        title_l = (job.get("title", "") or "").lower()
        title_bonus = sum(1 for k in keywords if _matches(title_l, k))
        cat = categorize(job)
        in_cat = bool(cats_set) and cat in cats_set
        # Relevance = real keyword overlap (>=2 or a title-role hit) OR the job is in a role the user
        # explicitly chose. The latter keeps role-relevant jobs the old overlap floor was dropping,
        # while the floor still kills incidental one-keyword noise for users with no role filter.
        if in_cat or (score >= min_score and (len(matched) >= 2 or title_bonus >= 1)):
            req = required_experience(job)  # stated years OR title level (Senior/Staff/Lead/...)
            # Hard-drop roles materially too senior for the user (gap-based, applies at ALL levels):
            # the "I set 0-2 but get Senior Data Engineer" complaint AND the mid-level "I keep getting
            # Staff/Principal" one. Stretch roles (small gap) survive and get the score penalty below.
            if over_leveled(req, user_years):
                continue
            # Internships only for early-career users (don't show a 5-yr engineer an intern role).
            if user_years > INTERN_MAX_YEARS and is_internship(job):
                continue
            job = dict(job)
            yrs = req
            gap = max(0, req - user_years)  # how much more experience the job wants than they have
            penalty = 25 if gap >= 6 else (12 if gap >= 3 else 0)
            region = job_region(job.get("location", ""))
            # For India users, surface India-based roles first (higher interview odds), then global
            # remote; foreign roles are already filtered out by location_ok.
            loc_boost = 12 if (india_user and region == "india") else 0
            # Normalized 0-100 fit: scaled skill+title overlap, + location relevance, - seniority gap.
            fit = max(15, min(100, score * 8 + loc_boost - penalty))
            job["raw_score"] = score
            job["score"] = fit
            job["region"] = region
            job["matched"] = matched
            job["core_overlap"] = core_overlap(job, matched)
            job["category"] = cat
            job["req_years"] = req  # used by blended_score + the browse experience filter
            job["seniority_note"] = f"~{req}+ yrs" if req >= 5 else ""
            tier = "Strong fit" if fit >= 75 else ("Good fit" if fit >= 50 else "Possible fit")
            top = ", ".join(matched[:5])
            gap_note = f". Note: asks {yrs}+ yrs (you list ~{user_years})" if gap >= 3 else ""
            job["reason"] = (f"{tier} ({fit}/100). Matches {len(matched)} of your skills"
                             + (f": {top}" if top else "") + gap_note)
            results.append(job)
    results.sort(key=lambda j: j["score"], reverse=True)
    return results
