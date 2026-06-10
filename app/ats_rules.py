"""Deterministic, ATS-grade resume analysis , the engine Jobscan/ResumeWorded run under the hood,
reimplemented so it works with zero LLM calls (and feeds the LLM the exact weak lines to rewrite).

Two products, like the real tools:
  - quality_report(rj): JD-independent resume quality, scored across Impact / Brevity / Style /
    Sections (ResumeWorded model), with the specific failing checks named.
  - bullet_diagnostics(rj, jd): per-bullet findings (weak opener, no metric, buzzword, passive,
    too long, missing JD keyword) + a concrete suggested rewrite scaffold for EACH weak bullet, so
    experience lines always get actionable feedback even when the AI is offline/quota'd.
"""
import re

# Strong action verbs (Jobscan + university career lists), grouped for variety, flattened to a set.
STRONG_VERBS = {
    # lead / manage
    "led", "managed", "directed", "supervised", "oversaw", "coordinated", "delegated", "spearheaded",
    "orchestrated", "headed", "chaired", "mentored", "coached", "guided", "facilitated", "recruited",
    # achievement
    "achieved", "accomplished", "attained", "exceeded", "outperformed", "surpassed", "delivered", "won",
    # built / created
    "built", "created", "designed", "developed", "launched", "founded", "established", "engineered",
    "architected", "authored", "constructed", "produced", "revamped", "shipped", "prototyped", "rebuilt",
    "implemented", "deployed", "integrated", "automated", "migrated", "refactored", "containerized",
    # improved / increased
    "improved", "increased", "boosted", "enhanced", "expanded", "grew", "optimized", "strengthened",
    "upgraded", "accelerated", "doubled", "tripled", "scaled", "heightened",
    # reduced / saved
    "reduced", "cut", "decreased", "eliminated", "lowered", "minimized", "saved", "streamlined",
    "trimmed", "consolidated",
    # drove / influenced
    "drove", "championed", "negotiated", "secured", "generated", "captured", "owned",
    # analysis
    "analyzed", "assessed", "audited", "evaluated", "identified", "investigated", "researched",
    "modeled", "forecasted", "diagnosed", "benchmarked",
    # communication
    "presented", "communicated", "articulated", "briefed", "instructed", "advised", "published",
}

# Weak / filler openers (Jobscan). Phrases first so the longer ones match before the short ones.
WEAK_OPENERS = [
    "was responsible for", "were responsible for", "responsible for", "duties included",
    "duty included", "worked on", "helped to", "helped with", "helped", "assisted with",
    "assisted in", "assisted", "participated in", "involved in", "in charge of", "tasked with",
    "handled", "dealt with",
]

# Gerund/base -> past tense, for converting a weak opener's verb into a strong opener.
_IRREGULAR = {
    "building": "Built", "leading": "Led", "running": "Ran", "writing": "Wrote", "making": "Made",
    "driving": "Drove", "setting": "Set", "getting": "Secured", "bringing": "Drove",
    "managing": "Managed", "creating": "Created", "developing": "Developed", "designing": "Designed",
    "building": "Built", "testing": "Tested", "deploying": "Deployed", "maintaining": "Maintained",
    "supporting": "Supported", "improving": "Improved", "handling": "Managed", "coordinating": "Coordinated",
}
# When the weak opener has no usable following verb, fall back to a sensible strong verb.
_WEAK_FALLBACK = {
    "worked on": "Built", "helped": "Drove", "helped to": "Drove", "helped with": "Supported",
    "assisted": "Supported", "assisted with": "Supported", "assisted in": "Supported",
    "participated in": "Contributed to", "involved in": "Drove", "in charge of": "Led",
    "tasked with": "Led", "handled": "Managed", "dealt with": "Managed",
    "responsible for": "Owned", "was responsible for": "Owned", "were responsible for": "Owned",
    "duties included": "Owned", "duty included": "Owned",
}

# Buzzwords / cliches recruiters and ResumeWorded flag (filler that says nothing).
BUZZWORDS = {
    "results-driven", "results driven", "passionate", "dynamic", "proactive", "highly motivated",
    "motivated", "top performer", "think outside the box", "value add", "synergy", "go-to person",
    "thought leadership", "industry expert", "team player", "detail-oriented", "detail oriented",
    "self-starter", "self starter", "go-getter", "hard worker", "hardworking", "hard-working",
    "strong work ethic", "fast-paced", "fast paced", "track record", "seasoned", "cutting-edge",
    "cutting edge", "game-changer", "game changer", "rockstar", "ninja", "guru", "world-class",
    "world class", "best-of-breed", "out of the box", "well-rounded", "people person",
    "good communication skills", "problem solver", "strategic thinker",
}

_PRONOUNS = re.compile(r"\b(i|me|my|mine|myself|we|our|ours)\b", re.I)
_PASSIVE = re.compile(r"\b(was|were|been|being|is|are|be)\s+\w+(ed|en)\b", re.I)
_NUMBER_WORD = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|dozen|hundred|thousand|million|billion)\b", re.I)


def _first_word(text):
    m = re.match(r"\s*([A-Za-z][A-Za-z'-]*)", text or "")
    return m.group(1).lower() if m else ""


def has_metric(text):
    """A bullet 'quantifies impact' if it has a digit, %, $, or a spelled-out number."""
    t = text or ""
    return bool(re.search(r"\d", t)) or "%" in t or "$" in t or bool(_NUMBER_WORD.search(t))


def _leading_weak(text):
    """Return the weak opener phrase at the start of the bullet, or '' if it opens strong."""
    low = (text or "").strip().lower()
    for w in WEAK_OPENERS:
        if low.startswith(w + " ") or low == w:
            return w
    return ""


def _strongify(text):
    """Best-effort deterministic rewrite of a weak opener into a strong-verb opener.
    Honest: only restructures the candidate's own words, never adds facts."""
    weak = _leading_weak(text)
    if not weak:
        return text.strip()
    rest = text.strip()[len(weak):].strip()
    rest = re.sub(r"^(the|a|an|to)\s+", "", rest, flags=re.I)
    nxt = _first_word(rest)
    # "responsible for managing X" -> "Managed X"
    if nxt.endswith("ing") and nxt in _IRREGULAR:
        verb = _IRREGULAR[nxt]
        rest = rest[len(nxt):].strip()
        return f"{verb} {rest}".strip()
    if nxt.endswith("ing"):
        base = nxt[:-3]
        if base.endswith(("iz", "at", "ut", "in", "or", "er", "iv", "rt", "pt", "ct", "nt")):
            verb = (base + "ed").capitalize()
        elif base.endswith("e"):
            verb = (base + "d").capitalize()
        else:
            verb = (base + "ed").capitalize()
        rest = rest[len(nxt):].strip()
        return f"{verb} {rest}".strip()
    # no usable verb after the opener -> prepend a sensible strong verb
    verb = _WEAK_FALLBACK.get(weak, "Drove")
    return f"{verb} {rest}".strip()


def analyze_bullet(text, missing_kw=None):
    """Findings for one bullet + a concrete suggested rewrite scaffold. Returns {} if the bullet is
    already strong. missing_kw: JD skills not yet on the resume that this bullet could carry."""
    t = (text or "").strip()
    if not t:
        return {}
    low = t.lower()
    issues = []
    first = _first_word(t)
    weak = _leading_weak(t)
    if weak:
        issues.append({"code": "weak_opener", "msg": f"Opens with filler (\"{weak}\") instead of a result"})
    elif first and first not in STRONG_VERBS:
        issues.append({"code": "soft_verb", "msg": "Doesn't open with a strong action verb"})
    if not has_metric(t):
        issues.append({"code": "no_metric", "msg": "No measurable result (add a %, $, count, or time saved)"})
    hits = sorted({b for b in BUZZWORDS if b in low})
    if hits:
        issues.append({"code": "buzzword", "msg": "Cliche/buzzword: " + ", ".join(hits[:3])})
    if _PASSIVE.search(t):
        issues.append({"code": "passive", "msg": "Passive voice , make it active"})
    if _PRONOUNS.search(t):
        issues.append({"code": "pronoun", "msg": "Drop personal pronouns (I/we/my)"})
    if len(t) > 240 or len(t.split()) > 34:
        issues.append({"code": "too_long", "msg": "Runs past two lines , tighten it"})
    carry = [k for k in (missing_kw or []) if k.lower() in low][:3]
    if not issues and not carry:
        return {}
    # build a deterministic suggestion (a scaffold the user/AI finishes; never fabricates facts)
    suggestion = _strongify(t)
    if not has_metric(suggestion):
        suggestion = suggestion.rstrip(". ") + " , quantify the impact (e.g. by ~X% / $Y / N users / Z hours saved)"
    return {"original": t, "issues": issues, "suggestion": suggestion,
            "carry_keywords": carry, "severity": len(issues)}


def _iter_bullets(rj):
    """Yield (section_label, list_ref, index, text) for every editable bullet in the resume."""
    if not isinstance(rj, dict):
        return
    for e in (rj.get("experience") or []):
        label = (e.get("title") or e.get("company") or "Experience")
        for j, b in enumerate(e.get("bullets") or []):
            yield ("experience", label, e.get("bullets"), j, b)
    for p in (rj.get("projects") or []):
        label = (p.get("name") or "Project")
        for j, b in enumerate(p.get("bullets") or []):
            yield ("projects", label, p.get("bullets"), j, b)


def bullet_diagnostics(rj, jd_text="", missing_kw=None):
    """Every weak bullet across experience + projects, worst first. The deterministic backbone of
    tailoring , this is what guarantees experience lines get concrete, specific suggestions."""
    out = []
    for kind, label, _ref, idx, text in _iter_bullets(rj):
        a = analyze_bullet(text, missing_kw=missing_kw)
        if a:
            a.update({"section": kind, "where": label, "index": idx})
            out.append(a)
    out.sort(key=lambda d: -d["severity"])
    return out


def _ratio(n, d):
    return (n / d) if d else 1.0


# Experience-aware length targets (words + total bullets), from Jobscan/ResumeWorded/Indeed research:
# (max_years, word_min, word_max, bullets_min, bullets_max, label)
_LEN_TIERS = [
    (2,   300, 450,  6, 12, "0-2 yrs"),
    (5,   400, 600, 10, 18, "2-5 yrs"),
    (10,  500, 700, 15, 22, "5-10 yrs"),
    (999, 700, 1000, 18, 28, "10+ yrs"),
]


def _len_targets(years):
    """Return (word_min, word_max, bullets_min, bullets_max, label) for the user's experience. When
    years is unknown, a wide, lenient generic band (so we never penalize for an unknown level)."""
    if years is None:
        return (300, 1000, 8, 24, "")
    for ymax, wmin, wmax, bmin, bmax, label in _LEN_TIERS:
        if years <= ymax:
            return (wmin, wmax, bmin, bmax, label)
    return (700, 1000, 18, 28, "10+ yrs")


def _resume_words(rj, bullets, skills):
    """Approximate total resume word count (bullets + summary + skills + titles/companies + education)."""
    parts = list(bullets) + [rj.get("summary") or ""] + list(skills)
    for e in (rj.get("experience") or []):
        parts += [e.get("title") or "", e.get("company") or ""]
    for p in (rj.get("projects") or []):
        parts += [p.get("name") or "", p.get("stack") or ""]
    for ed in (rj.get("education") or []):
        parts += [ed.get("degree") or "", ed.get("school") or ""]
    return len(" ".join(parts).split())


def quality_report(rj, years=None):
    """ResumeWorded-style quality score (0-100), JD-independent, split into Impact / Brevity / Style /
    Sections so the number is explainable. `years` (experience) makes the length targets tier-aware.

    Crucially CONTENT-GATED: 'no problems' checks (no buzzwords, active voice, etc.) only earn points
    when there's real content to judge (>= 4 bullets). Without this, a near-empty resume scored ~31 by
    passing every 'absence' check , an empty resume must score near zero."""
    rj = rj if isinstance(rj, dict) else {}  # tolerate None / a stray list body without crashing
    exp = rj.get("experience") or []
    bullets = [b for e in exp for b in (e.get("bullets") or [])]
    bullets += [b for p in (rj.get("projects") or []) for b in (p.get("bullets") or [])]
    nb = len(bullets)
    skills = rj.get("skills") or []
    sub = nb >= 4  # "substantial enough to judge writing quality" gate , below this, absence != quality

    quantified = sum(1 for b in bullets if has_metric(b))
    strong = sum(1 for b in bullets if _first_word(b) in STRONG_VERBS)
    weak = sum(1 for b in bullets if _leading_weak(b))
    verbs = [_first_word(b) for b in bullets if _first_word(b)]
    distinct_verbs = len(set(verbs))
    buzz = sum(1 for b in bullets if any(z in b.lower() for z in BUZZWORDS))
    buzz += 1 if any(z in (rj.get("summary") or "").lower() for z in BUZZWORDS) else 0
    passive = sum(1 for b in bullets if _PASSIVE.search(b))
    pronouns = sum(1 for b in bullets if _PRONOUNS.search(b))
    longish = sum(1 for b in bullets if len(b) > 240 or len(b.split()) > 34)
    words = _resume_words(rj, bullets, skills)
    wmin, wmax, bmin, bmax, tier = _len_targets(years)
    tier_sfx = f" for {tier}" if tier else ""

    cats, checks = [], []

    def cat(name, items):
        got = sum(p for ok, p, _ in items if ok)
        tot = sum(p for _, p, _ in items)
        for ok, _p, label in items:
            checks.append({"ok": bool(ok), "label": label})
        cats.append({"name": name, "score": round(100 * _ratio(got, tot)),
                     "fails": [label for ok, _p, label in items if not ok]})
        return got, tot

    # IMPACT (most weight). Absence-checks gated on `sub` so an empty resume earns nothing here.
    impact = [
        (nb and _ratio(quantified, nb) >= 0.5, 16, "Half your bullets quantify impact (numbers/%/$)"),
        (nb and _ratio(strong, nb) >= 0.7, 10, "Bullets open with strong action verbs"),
        (sub and weak == 0, 8, "No filler openers (\"responsible for\", \"worked on\")"),
        (sub and buzz == 0, 6, "No buzzwords/cliches"),
    ]
    # BREVITY , experience-aware length targets
    brevity = [
        (bmin <= nb <= bmax, 6, f"{bmin}-{bmax} experience/project bullets{tier_sfx}"),
        (sub and longish == 0, 6, "Every bullet fits in 1-2 lines"),
        (sub and words <= round(wmax * 1.2), 5, f"Length ~{wmin}-{wmax} words{tier_sfx} (avoid bloat)"),
    ]
    # STYLE , also content-gated
    style = [
        (sub and passive == 0, 6, "Active voice throughout"),
        (sub and pronouns == 0, 5, "No personal pronouns (I/we/my)"),
        (sub and distinct_verbs >= max(3, round(nb * 0.6)), 5, "Action verbs are varied, not repeated"),
    ]
    # SECTIONS / searchability
    sections = [
        (bool(rj.get("email")) and bool(rj.get("phone")), 6, "Contact info (email + phone)"),
        (bool((rj.get("summary") or "").strip()), 3, "Has a summary"),
        (len(skills) >= 6, 6, "At least 6 skills listed"),
        (bool(exp), 4, "Has work experience"),
        (bool(rj.get("education")), 3, "Education listed"),
    ]
    g1, _ = cat("Impact", impact)
    g2, _ = cat("Brevity", brevity)
    g3, _ = cat("Style", style)
    g4, _ = cat("Sections", sections)
    score = min(100, round(g1 + g2 + g3 + g4))
    return {"score": score, "categories": cats, "checks": checks,
            "stats": {"bullets": nb, "quantified": quantified, "words": words,
                      "target_words": [wmin, wmax], "target_bullets": [bmin, bmax], "tier": tier}}
