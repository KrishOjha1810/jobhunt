"""Score jobs against a user's keyword profile and location preference."""
import functools
import re

from . import classify
from .sources.ats_more import BOARD_PROVIDERS as _BOARD_PROVIDERS


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
    # USD-remote target: the user listed worldwide/international, so a remote role at a US/EU company
    # is exactly what we want. Keep India + global + unknown AND any REMOTE-looking role regardless of
    # country; drop only clearly ONSITE-foreign. The geo-eligibility gate (regex + LLM on the JD body)
    # does the real filtering of US/EU-only remote roles downstream.
    intl_remote = any(w in ("worldwide", "international", "anywhere", "global") for w in wants)
    region = job_region(job_location)
    if intl_remote:
        # Keep India + generic/worldwide remote + unknown. DROP anything tied to a specific foreign
        # country (incl. "Remote - USA" / "Remote, Germany"): a remote role naming one foreign country
        # is almost always restricted to it, and surfacing it is worse than useless. This holds even
        # when the LLM screen is down. The screen (on the JD body) still catches a hidden "US only"
        # buried in a generic "Remote"/"Worldwide" posting.
        return region in ("india", "global", "unknown")
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


# ---- experience parsing (shared by ranking hard-drops, the blended score and the strong guard) ----
# Only REQUIREMENT phrasing counts as years required: "3+ years", "2-4 yrs", "Min 3 Years",
# "Experience: 5-8 years", "4 years of backend experience". A range counts at its LOWER end ("2-5
# years" is reachable with 2). Company-age / company-experience mentions ("founded 10 years ago",
# "20+ years in business", "we have 15 years of experience serving clients") are ignored.
_YRS = r"(?:years?|yrs?)(?:'s)?"
_RANGE_SEP = "(?:-|\u2013|\u2014|to)"
_EXP_YEARS_RES = (
    # "3+ years" / "3 + yrs": the plus sign marks a requirement
    re.compile(r"(\d{1,2})\s*\+\s*" + _YRS + r"(?![a-z])"),
    # bare range "2-4 yrs" / "3 to 5 years" (Naukri-style experience field)
    re.compile(r"(\d{1,2})\s*" + _RANGE_SEP + r"\s*\d{1,2}\s*\+?\s*" + _YRS + r"(?![a-z])"),
    # "between 2 and 5 years"
    re.compile(r"between\s+(\d{1,2})\s+and\s+\d{1,2}\s*" + _YRS + r"(?![a-z])"),
    # "2-5 years of experience", "3 years experience", "4 years of backend experience" (lower end)
    re.compile(r"(\d{1,2})(?:\s*" + _RANGE_SEP + r"\s*\d{1,2})?\s*" + _YRS +
               r"\s+(?:of\s+)?(?:[\w/.#-]+\s+){0,3}?(?:experience|exp)\b"),
    # "minimum 3 years", "at least 2 years", "min. 4 yrs", "Min 3 Years"
    re.compile(r"(?:minimum|min\.?|at\s+least|atleast)\s+(?:of\s+)?(\d{1,2})\s*\+?\s*" + _YRS),
    # "experience: 2-4 years", "experience of 3 years", "Exp: 2-4 yrs", "Experience Required: 5-8 years"
    re.compile(r"\bexp(?:erience)?\.?\s?(?:(?:required|req\.?|needed)\s?)?(?:(?:of|:|-)\s?)?(\d{1,2})"
               r"(?:\s?" + _RANGE_SEP + r"\s?\d{1,2})?\s?\+?\s?" + _YRS),
)
_WORD_NUMS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
              "nine": 9, "ten": 10, "eleven": 11, "twelve": 12}
# "five years", "five (5) years", "five+ years" become "5 years"
_WORD_NUM_RE = re.compile(r"\b(" + "|".join(_WORD_NUMS) + r")\s?(?:\(\s?\d{1,2}\s?\))?(?=\s?\+?\s?(?:years?|yrs?)\b)")
# Context that marks a years-mention as being about the COMPANY, not the candidate.
_COMPANY_AFTER_RE = re.compile(r"\s?(?:ago\b|old\b|young\b|in\s+business|of\s+(?:history|legacy|"
                               r"operations?|excellence|service|serving|trust|innovation|growth)\b|"
                               r",\s?(?:we\b|we've|we\s+have|our\b))")
_COMPANY_BEFORE_RE = re.compile(r"\b(?:founded|established|incorporated|legacy|heritage|track\s+record|"
                                r"(?:we|we've|we\s+have|our\s+\w+|the\s+company|company|firm)\s+"
                                r"(?:has|have|had|brings?|bringing|with|over|of|spanning)|"
                                r"been\s+(?:around|operating|in))\b")
_CANDIDATE_RE = re.compile(r"\b(?:you|your|candidates?|applicants?|engineers?|developers?|someone|person|"
                           r"people|professionals?|hires?|openings?|role|position)\b")
_MAX_PLAUSIBLE_REQ = 15  # nobody posts a 16+ year requirement; such numbers are company boasts


def _squash_ws(text: str) -> str:
    """Lowercase and collapse whitespace runs to one char (keeping line breaks as sentence breaks), so
    no regex below ever sees a long whitespace run (adjacent optional \\s groups backtrack badly)."""
    t = re.sub(r"[^\S\n]+", " ", (text or "").lower())
    return re.sub(r" ?\n\s*", "\n", t)


@functools.lru_cache(maxsize=1024)  # the same JD is parsed once per user per run; parse it once
def experience_years_required(text: str) -> int:
    """Years a JD (or title) clearly REQUIRES: only experience-requirement phrasing counts, a range
    counts at its lower end ("2-5 years" is 2), company age / company experience is ignored, and
    several requirements take the highest one. 0 if none."""
    t = _squash_ws(text)
    if not t:
        return 0
    t = _WORD_NUM_RE.sub(lambda m: str(_WORD_NUMS[m.group(1)]), t)
    vals = []
    for rx in _EXP_YEARS_RES:
        for m in rx.finditer(t):
            n = int(m.group(1))
            if n > _MAX_PLAUSIBLE_REQ:
                continue
            if _COMPANY_AFTER_RE.match(t, m.end()):
                continue
            # look back within the same sentence/line only (at most 60 chars)
            start = max(0, m.start() - 60)
            back = re.split(r"[.!?;\n\u2022]", t[start:m.start()])[-1]
            if _COMPANY_BEFORE_RE.search(back) and not _CANDIDATE_RE.search(back):
                continue
            vals.append(n)
    return max(vals, default=0)


def years_required(text: str) -> int:
    """Years of experience a JD requires (0 if none). Requirement phrasing only, ranges at their lower
    end; see experience_years_required. (Was: the highest 'N years' anywhere, so 'founded 10 years
    ago' or the top of a '2-5 years' range hard-dropped jobs a 2-yr user could get.)"""
    return experience_years_required(text)


# Seniority implied by the job TITLE, even when the JD never states "N+ years" (e.g. "Senior Data
# Engineer"). Junior markers win (an Associate/Junior/Intern title is entry-level) UNLESS a high
# marker sits next to them ("Associate Director", "Senior Associate", "Associate Tech Lead");
# otherwise the strongest senior marker sets an implied experience bar. Unmarked titles imply
# nothing (0), we then rely on the stated years. Only the TITLE is read: "work with senior engineers"
# or "you will lead the migration" in a JD body never raises the bar.
# Note: "lead", "head", "staff", "fellow" and "manager" are NOT plain markers, they're handled by the
# scoped regexes below, so "Lead Generation Specialist", "Head of Growth" (still senior, but via the
# regex), "Staff Accountant", "Member of Technical Staff", "Research Fellow" and "Account Manager"
# aren't mis-read as senior engineering roles.
_TITLE_SENIOR = {"principal": 9, "architect": 8, "distinguished": 10,
                 "director": 10, "vp": 12, "vice president": 12, "senior": 5, "sr": 5, "snr": 5}
_TITLE_JUNIOR = {"intern": 0, "internship": 0, "trainee": 0, "apprentice": 0, "fresher": 0,
                 "graduate": 1, "entry": 1, "junior": 1, "jr": 1, "associate": 2}
_STAFF_LEVEL = 8
# "lead" only counts as seniority in a job-level context (tech/team/eng lead, lead engineer...), NOT
# "lead generation"/"lead gen". "head" only in "head of <X>".
_LEAD_RE = re.compile(r"\b(tech|technical|team|engineering|eng|dev|software|squad|project|product|"
                      r"design|data|qa|platform|staff|group)\s+lead\b|"
                      r"\blead\s+(engineer|developer|architect|designer|scientist|analyst|"
                      r"consultant|sre|devops|qa)\b")
# Wider lead forms: "Lead Software Engineer", "Lead Data Engineer", and a trailing "<tech> Lead"
# ("Frontend Lead", "ML Lead, Payments"). Not "lead-gen". A trailing NON-tech "<X> Lead" ("Growth
# Lead", "Customer Success Lead", "Implementation Lead") is guard-only (_ANY_LEAD_TAIL_RE): those are
# often IC roles and a hard-drop there is the costly mistake.
_LEAD_TAIL = r"\s+lead\s?(?:$|[,(|/:\u2013\u2014]|-\s)"
_LEAD_WIDE_RE = re.compile(r"\blead\s+(?:\w+\s+)?(?:engineer|developer)\b|"
                           r"\b(?:tech|technical|software|engineering|backend|back-end|frontend|front-end|"
                           r"fullstack|full-stack|full stack|data|qa|devops|dev|ml|ai|mobile|android|ios|"
                           r"platform|infra|infrastructure|cloud|sre|security|java|python|js|react|node)"
                           + _LEAD_TAIL)
_ANY_LEAD_TAIL_RE = re.compile(r"\b\w+" + _LEAD_TAIL)
_HEAD_RE = re.compile(r"\bhead\s+of\b")
# "staff" is senior only in a technical ladder (Staff Engineer, "Software Engineer, Staff");
# "Staff Accountant" / "Staff Nurse" are entry-level and "Member of Technical Staff" is a generic
# IC title (its level comes from Senior/Principal/Lead next to it, if any).
_STAFF_RE = re.compile(r"(?<!technical )\bstaff\s+(?:\w+\s+){0,2}?(engineer|engineering|developer|scientist|"
                       r"architect|designer|researcher|analyst|swe|sre|manager)\b|"
                       r"\b(?:engineer|developer|scientist|swe)\s*[,(-]\s*staff\b")
_FELLOW_RE = re.compile(r"\b(?:engineering|technical|distinguished|senior)\s+fellow\b")
# People managers of engineering ("Engineering Manager", "Software Development Manager", "Manager,
# Software Engineering"). "Business / Learning and Development Manager", "Product Manager,
# Technology" and any other "Manager" stay guard-only, as do Indian "Assistant / Deputy Manager"
# grades (often 2-5 yrs): a hard-drop on those would throw away roles a junior can get.
_ENG_MGR_RE = re.compile(r"\b(?:engineering|software\s+development|software|devops|qa|it)\s+manager\b|"
                         r"\bmanager\s?(?:,|-|\u2013|of|\()\s?(?:software\s+)?engineering\b|"
                         r"\bmanager\s?(?:,|-|\u2013|of|\()\s?software\s+development\b")
_JUNIOR_MGR_RE = re.compile(r"\b(?:assistant|asst\.?|deputy|dy\.?)\s+(?:\w+\s+)?manager\b")
# Numbered ladder levels next to a role word: "SDE II", "SDE-3", "Software Engineer 2", "Engineer III".
_LEVEL_RE = re.compile(r"\b(sde|swe|mts|engineer|developer|analyst|scientist|programmer)\s?-?\s?"
                       r"(iv|iii|ii|i|[1-5])(?![a-z0-9+&.])"
                       r"(?!\s?(?:openings?|positions?|vacanc|roles?|posts?|seats?|nos?\b|hires?|"
                       r"years?|yrs?|exp\b|experience|(?:-|to\b|\u2013|\u2014)\s?\d))")
_LEVELS = {"i": 0, "1": 0, "ii": 2, "2": 2, "iii": 4, "3": 4, "iv": 6, "4": 6, "5": 8}
_SDE_LEVELS = {"iii": 5, "3": 5}  # Indian SDE-3 / SDE III is a senior band (~5+ yrs)
# Markers that keep a title senior even next to an entry marker. For RANKING only exec markers count
# ("Associate Director", "Junior Director", "Associate Principal Engineer"); the guard also counts
# Senior / Sr / a tech lead ("Senior Associate", "Associate Tech Lead"), which in Indian IT and
# consulting are often 2-4 yr grades, so they only block a strong badge.
_EXEC_RE = re.compile(r"(?<![a-z])(director|vp|avp|vice president|principal)(?![a-z])|\bhead\s+of\b")
_HIGH_RE = re.compile(r"(?<![a-z])(director|vp|avp|vice president|principal|senior|sr|snr)(?![a-z])|"
                      r"\bhead\s+of\b")


def _tword(text: str, tok: str) -> bool:
    return re.search(r"(?<![a-z])" + re.escape(tok) + r"(?![a-z])", text) is not None


def _has_entry_marker(t: str) -> bool:
    return any(_tword(t, tok) for tok in _TITLE_JUNIOR)


# In a TITLE a bare "5 years" / "3 Years" is a requirement ("Node.js Developer - 3 Years"); in a JD
# body it is not ("for 5 years we have..."), so this is title-only. The upper end of a range ("3-5
# Yrs") is skipped, the range regex already took its lower end.
_TITLE_BARE_YEARS_RE = re.compile(r"(?<![\d.])(?<!\d-)(?<!\d -)(?<!\d- )(?<!\d - )(?<!\d to )(?<!\d\u2013)(?<!\d \u2013)(?<!\d\u2013 )(?<!\d \u2013 )(?<!\d\u2014)(?<!\d \u2014 )(\d{1,2})\s?(?:years?|yrs?)(?![a-z])(?!\s?(?:ago|old)\b)")


def _title_years(t: str) -> int:
    bare = [int(n) for n in _TITLE_BARE_YEARS_RE.findall(t) if int(n) <= _MAX_PLAUSIBLE_REQ]
    return max([experience_years_required(t)] + bare)


def _is_entry_title(title: str) -> bool:
    """Entry-level for the strong GUARD: any high marker or tech lead next to the entry marker wins."""
    t = _squash_ws(title)
    return _has_entry_marker(t) and not _HIGH_RE.search(t) and not _LEAD_RE.search(t)


def _entry_level(t: str) -> int:
    for tok, lvl in _TITLE_JUNIOR.items():
        if _tword(t, tok):
            return lvl
    return 0


def _senior_title_level(t: str) -> int:
    """Implied years from the senior markers in a lowercased title (entry markers ignored)."""
    best = max((lvl for tok, lvl in _TITLE_SENIOR.items() if _tword(t, tok)), default=0)
    if _STAFF_RE.search(t):
        best = max(best, _STAFF_LEVEL)
    if _LEAD_RE.search(t) or _LEAD_WIDE_RE.search(t):
        best = max(best, 6)
    if _HEAD_RE.search(t):
        best = max(best, 9)
    if _FELLOW_RE.search(t):
        best = max(best, 10)
    if _ENG_MGR_RE.search(t) and not _JUNIOR_MGR_RE.search(t):
        best = max(best, 7)
    for role, lv in _LEVEL_RE.findall(t):
        best = max(best, (_SDE_LEVELS.get(lv) if role == "sde" else None) or _LEVELS[lv])
    # a requirement stated in the title itself: "Backend Engineer (5+ years)", "Java Dev | 3-5 Yrs"
    return max(best, _title_years(t))


def title_level(title: str) -> int:
    """Implied minimum years of experience from a job TITLE (0 if none/entry-level)."""
    t = _squash_ws(title)
    if _has_entry_marker(t) and not _EXEC_RE.search(t):
        # entry-level marker wins unless an exec marker sits next to it; years stated in the title
        # ("Junior Developer (3+ years)") still count
        return max(_entry_level(t), _title_years(t))
    return _senior_title_level(t)


def required_experience(job: dict) -> int:
    """Experience a role likely wants: max of the JD's stated requirement and the title's level."""
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


# The user's chosen experience level mapped to approximate years (users.experience drives seniority
# matching). Explicit level wins; unknown falls back to years parsed from the resume.
EXPERIENCE_YEARS = {"fresher": 0, "junior": 1, "mid": 3, "senior": 7, "lead": 11}


def user_years(user: dict):
    """Approximate years of experience for a user, or None when genuinely unknown (no explicit level
    and no years found in the resume). Unknown is NOT 0: treating it as a fresher would cap every
    non-entry role for users who simply never filled the field."""
    user = user or {}
    yrs = EXPERIENCE_YEARS.get(user.get("experience") or "")
    if yrs is not None:
        return yrs
    from . import resume as _resume
    return _resume.years_experience(user.get("resume_text") or "") or None


def ranking_years(user: dict) -> int:
    """The user's years for RANKING (hard-drop + seniority score): the same derivation as user_years
    (explicit level, else years from the resume), with unknown treated as 0. One helper so the digest,
    Browse and the background pre-match can't drift apart (one of them ignored the resume)."""
    return user_years(user) or 0


# ---- strong-verdict guard ------------------------------------------------------------------------
# The guard is stricter than the ranking hard-drop: on top of title_level's markers it also counts a
# generic "Manager" (incl. Assistant / Deputy / Business Development Manager), "AVP", any trailing
# "<X> Lead", Senior/Sr next to an entry marker, and a bare trailing Roman level ("Engineer IV",
# "Data Platform III") at a higher bar. Those stay out of title_level because a ranking HARD-DROP on them would throw away too
# much; a guard false positive only costs a 'maybe' badge.
_STRONG_ONLY_SENIOR = {"manager": 5, "avp": 10}
# Roman-numeral levels only as a standalone trailing level token ("Engineer IV", "Engineer III, Data"),
# never inside words or acronyms like "IV&V Engineer".
_ROMAN_LEVEL_RE = re.compile(r"(?<=\s)(iii|iv)(?=\s*(?:$|[,(|/:-]))")
_ROMAN_LEVELS = {"iii": 5, "iv": 7}
_EXEC_LEVEL = 9  # principal / head of / director / vp: capped even when the user's years are unknown


def _guard_title_level(title: str) -> int:
    """Implied years from a title for the guard: title_level's senior markers ignoring entry markers,
    plus the guard-only markers above."""
    t = _squash_ws(title)
    best = _senior_title_level(t)
    if _ANY_LEAD_TAIL_RE.search(t):
        best = max(best, 6)
    best = max([best] + [lvl for tok, lvl in _STRONG_ONLY_SENIOR.items() if _tword(t, tok)])
    best = max([best] + [_ROMAN_LEVELS[m] for m in _ROMAN_LEVEL_RE.findall(t)])
    return best


def too_senior_for_strong(job: dict, uyears) -> bool:
    """True if a role is above the user's level, so it may be at most a 'maybe', never 'strong'.
    Title markers (Senior/Staff/Lead/Director/Manager/III/'5+ years' in the title) are strict: any
    title level above the user's years blocks 'strong'. Entry-level titles (Junior/Associate/...)
    never block on their own, unless a high marker sits next to them ("Associate Director"). A
    JD-stated requirement gets one year of slack (a 1-yr user can still be a strong fit for '2+
    years'). When the user's years are unknown (None) only exec-level titles (principal, head of,
    director, VP) are capped."""
    title = job.get("title", "") or ""
    entry = _is_entry_title(title)
    level = 0 if entry else _guard_title_level(title)
    try:
        uy = int(uyears)
    except (TypeError, ValueError):
        return level >= _EXEC_LEVEL
    req_title = max(level, experience_years_required(title))
    req_jd = experience_years_required(job.get("description", "") or "")
    return req_title > uy or req_jd > uy + 1


def gate_verdict(job: dict, uyears=None):
    """The ONE rule for what verdict may be stored/shown for a job. 'strong' is only legitimate when a
    real recruiter screen produced a judgment (a non-empty why_fit) AND the role is not above the
    user's level; otherwise it becomes 'maybe' (left for the user to review by hand, never
    auto-applied). uyears=None means the user's experience is unknown (see too_senior_for_strong)."""
    v = job.get("verdict")
    if v != "strong":
        return v
    if too_senior_for_strong(job, uyears):
        return "maybe"
    if not (job.get("why_fit") or "").strip():
        return "maybe"
    return v


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
        vs = _variants_of(k.lower())  # job text is lowercased; lowercase the keyword too so
        #                               Title-case prefs (e.g. "Python", "Backend Engineer") still match
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
                 "category": 1.5, "prioritize": 1.6, "comp": 0.5, "yc": 1.5}
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


def has_salary(job: dict) -> bool:
    """True if the posting discloses pay (a real salary string, not just a stray symbol)."""
    s = (job.get("salary") or "").strip()
    return len(s) >= 3 and any(c.isdigit() for c in s)


def freshness_label(job: dict) -> str:
    """Short human freshness tag for the digest/Browse ('be first to apply' , early applicants get
    seen). Empty when we can't date it confidently, so we never imply false freshness."""
    try:
        from .db import posted_age_days
        age = posted_age_days(job.get("posted_at"))
    except Exception:
        age = None
    if age is None:
        return ""
    if age <= 1:
        return "🆕 Just posted"
    if age <= 3:
        return f"Fresh , {age}d ago"
    if age <= 7:
        return "Posted this week"
    return ""


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


# Curated set of YC-backed / top startups present in our board lists (slugs + names, lowercase).
# High-confidence only , mislabeling would erode trust in the boost. Expand as boards are added.
YC_COMPANIES = {
    "coinbase", "stripe", "airbnb", "dropbox", "reddit", "doordash", "instacart", "gitlab", "brex",
    "faire", "gusto", "razorpay", "checkr", "deel", "twitch", "supabase", "posthog", "resend",
    "inngest", "meesho", "flexport", "razorpaysoftwareprivatelimited",
}


def is_yc(job) -> bool:
    """True if a job is from a YC-backed / top startup (matched by board slug or company name).
    These are the remote, high-paying roles we surface first when they also fit the resume."""
    src = job.get("source") or ""
    slug = src.split(":", 1)[1].lower() if ":" in src else ""
    if slug and slug in YC_COMPANIES:
        return True
    return (job.get("company") or "").strip().lower() in YC_COMPANIES


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
    if src in _BOARD_PROVIDERS:
        Sq = 0.5
    elif src == "adzuna":
        Sq = -0.6
    else:
        Sq = 0.0
    adj = (ctx.get("source_q") or {}).get(src)
    if adj is not None:
        Sq = max(-1.2, min(1.2, Sq + adj * 0.6))  # learned net-action rate moves the prior, doesn't replace it

    # Comp-disclosed bonus (a Wellfound mechanic): a posting that states pay is more applicable and
    # respects the candidate, so nudge it up as a tiebreak. Small weight , never dominates skills.
    Cmp = 1.0 if has_salary(job) else 0.0

    # YC / top-startup boost: these are the remote, high-paying roles worth surfacing first, so when a
    # YC-backed company's job ALSO fits the resume well, float it up. Gated on real overlap below, so
    # it only lifts good-fit YC jobs (not every YC posting).
    Yc = 1.0 if is_yc(job) else 0.0

    # Require REAL skill overlap before the circumstantial bonuses (source/location/category/comp/yc)
    # can lift the score. Otherwise one common keyword on a company board in India could fake a strong
    # match. Penalties (Adzuna, domain-mismatch) are kept regardless.
    strong_overlap = (n >= 2) or (core >= 1)
    if not strong_overlap:
        if Sq > 0:
            Sq = 0.0
        if L > 0:
            L = 0.0
        if Cat > 0:
            Cat = 0.0
        Cmp = 0.0
        Yc = 0.0

    w = SCORE_WEIGHTS
    contrib = {"content": w["content"] * C, "pref": w["pref"] * Pf, "seniority": w["seniority"] * S,
               "location": w["location"] * L, "recency": w["recency"] * R,
               "collab": w["collab"] * Co, "trending": w["trending"] * Tr,
               "semantic": w["semantic"] * Sem, "source": w["source"] * Sq,
               "category": w["category"] * Cat, "prioritize": w["prioritize"] * Pri,
               "comp": w["comp"] * Cmp, "yc": w["yc"] * Yc}
    z = sum(contrib.values()) - SCORE_BIAS
    score = int(round(100 / (1 + _math.exp(-max(-30, min(30, z))))))
    score = max(15, min(100, score))
    return score, contrib


_CONTRIB_LABEL = {"content": "skills match", "pref": "roles you favour", "seniority": "seniority fit",
                  "location": "location fit", "recency": "freshly posted", "collab": "popular with similar users",
                  "trending": "trending role", "semantic": "matches your resume",
                  "source": "from a company board", "category": "a role you chose",
                  "prioritize": "a role you want", "comp": "salary disclosed",
                  "yc": "YC / top startup"}


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
                 user_years: int = 0, user_cats: list = None, fresher: dict = None,
                 dream_companies: list = None) -> list:
    """Return jobs that clear min_score and pass the location filter, sorted by fit desc.

    user_years (from the resume) drives a seniority-GAP penalty: a job asking far more experience
    than the candidate has is deprioritized, but a senior candidate is NOT penalized for senior roles.

    user_cats (the user's chosen role categories): a job IN one of those roles is relevant by role even
    with thin keyword overlap, so it bypasses the keyword overlap floor (this is why Browse, which
    filters by role, surfaced good jobs the matcher was dropping)."""
    wants = [w.lower().strip() for w in (locations or [])]
    india_user = any(w == "india" or w in INDIA_CITIES for w in wants)
    cats_set = set(user_cats or [])
    dream = [d.strip().lower() for d in (dream_companies or []) if str(d).strip()]
    results = []
    for job in jobs:
        if not location_ok(job.get("location", ""), locations):
            continue
        score, matched = score_job(job, keywords)
        title_l = (job.get("title", "") or "").lower()
        title_bonus = sum(1 for k in keywords if _matches(title_l, k))
        cat = job.get("category") or categorize(job)  # pool is pre-categorized once; don't redo it per user
        in_cat = bool(cats_set) and cat in cats_set
        comp_l = (job.get("company", "") or "").lower()
        is_dream = bool(dream) and any(d in comp_l for d in dream)  # a role at a company you specifically want
        # Relevance = real keyword overlap (>=2 or a title-role hit) OR the job is in a role the user
        # explicitly chose. The latter keeps role-relevant jobs the old overlap floor was dropping,
        # while the floor still kills incidental one-keyword noise for users with no role filter.
        if in_cat or is_dream or (score >= min_score and (len(matched) >= 2 or title_bonus >= 1)):
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
            if is_dream:  # a role at a company you specifically want , push it to the top and tag it
                fit = min(100, fit + 20)
                job["is_target"] = True
            # Employment-type + fresher signals (internship / PPO / trainee / probation), tagged on
            # EVERY job so the UI can badge "converts to full-time". In fresher mode these also boost
            # fresher-friendly and PPO roles, and can hard-filter to only convertible ones.
            einfo = classify.classify_employment(job.get("title", ""), job.get("description", ""))
            job["employment_type"] = einfo["employment_type"]
            job["has_ppo"] = einfo["has_ppo"]
            job["converts_fulltime"] = classify.converts_to_fulltime(einfo)
            job["is_fresher_friendly"] = einfo["is_fresher_friendly"]
            if fresher and fresher.get("fresher_mode"):
                if fresher.get("only_convertible") and not job["converts_fulltime"]:
                    continue  # student wants only roles that can become a full-time job
                fit = min(100, fit + (10 if job["converts_fulltime"]
                                      else (6 if job["is_fresher_friendly"] else 0)))
            elif fresher and fresher.get("open_to_internships") is False and is_internship(job):
                continue  # user explicitly does not want internships
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
