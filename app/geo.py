"""Geo-eligibility helpers for the USD-remote target market.

Most "remote" roles are a trap for an India-based applicant: they mean "remote within the US" or
"remote in EMEA" or require in-country work authorization. The rule here (from the job-search spec):
a region/poster tag can only DROP a role, never ACCEPT it (tags like "Worldwide" are self-reported
and the JD body often contradicts them); only the JD body can accept. EOR/contractor language is the
most reliable positive signal because it names the actual mechanism a foreign company uses to employ
someone in India. Timezone overlap is surfaced on the digest card, not filtered , the candidate decides.
"""
import re

# Hard exclusions: if the JD body says one of these, an India-based applicant cannot take the role.
_HARD_EXCLUDE = [
    r"\bus[-\s]only\b", r"\bu\.?s\.?[-\s]based\b", r"\bus[-\s]based (?:candidates?|applicants?|only)",
    r"\bbased in the (?:us|u\.s\.|united states)\b",
    r"\bmust (?:be )?(?:a |an )?(?:us|u\.s\.|uk|eu|eea|canadian|european) (?:citizen|resident|national)",
    r"\b(?:us|u\.s\.|uk|eu|eea) work author(?:i|r)zation\b",
    r"\bauthor(?:i|r)zed to work in the (?:us|u\.s\.|united states|uk|eu|eea|united kingdom|canada)\b",
    r"\bno visa sponsorship\b", r"\bgreen card\b", r"\bh[-\s]?1b\b",
    r"\beea? residents?\b", r"\beu[-\s]only\b", r"\buk[-\s]only\b", r"\bemea[-\s]only\b",
    r"\bmust (?:be )?(?:located|reside|residing|based) in (?:the )?(?:us|u\.s\.|united states|uk|eu|eea|canada|europe|north america)\b",
    r"\b(?:us|u\.s\.|north america|uk|eu)[-\s]based (?:candidates?|applicants?|only)\b",
    r"\bonly (?:us|u\.s\.|uk|eu|eea|canadian|european)[-\s]based\b",
]
_HARD_RE = re.compile("|".join(_HARD_EXCLUDE), re.I)

# Positive eligibility: the mechanisms by which a US/EU company actually employs someone in India.
_EOR_RE = re.compile(
    r"\b(deel|remote\.com|oyster|employer of record|eor|globally as contractors?|"
    r"contractors? welcome|hire(?:s|d)? globally|we hire globally|async[-\s]first)\b", re.I)
_WORLDWIDE_RE = re.compile(
    r"\b(worldwide|anywhere in the world|work from anywhere|any time ?zone|"
    r"no location requirement|globally distributed|fully distributed|remote worldwide|remote anywhere)\b", re.I)
_INDIA_RE = re.compile(r"\b(india|ist\b|indian standard time|apac|south asia)\b", re.I)

# Timezone-overlap requirement to SURFACE (not filter).
_TZ_RE = re.compile(
    r"(\d+\+?\s*(?:hours?|hrs?)\s*(?:of\s*)?overlap[^.\n]{0,40}"
    r"|overlap[^.\n]{0,30}(?:pst|pdt|est|edt|\bet\b|\bpt\b|cet|cest|gmt|utc|emea|pacific|eastern)[^.\n]{0,20}"
    r"|(?:pst|pdt|est|edt|cet|cest|gmt|utc|emea)\s*(?:time ?zone|hours|working hours|overlap)[^.\n]{0,20})",
    re.I)


def geo_hard_excluded(text: str) -> bool:
    """True when the JD body clearly excludes an India-based applicant (cheap pre-LLM drop)."""
    return bool(_HARD_RE.search(text or ""))


def geo_signals(text: str) -> dict:
    t = text or ""
    return {"eor": bool(_EOR_RE.search(t)),
            "worldwide": bool(_WORLDWIDE_RE.search(t)),
            "india": bool(_INDIA_RE.search(t))}


def geo_rank(text: str) -> int:
    """Confidence that the role is India-eligible, for ranking (higher first):
    3 = names India/APAC, 2 = EOR/contractor mechanism, 1 = worldwide/anywhere, 0 = ambiguous."""
    s = geo_signals(text)
    if s["india"]:
        return 3
    if s["eor"]:
        return 2
    if s["worldwide"]:
        return 1
    return 0


def timezone_overlap(text: str) -> str:
    """A short timezone/overlap requirement string if the posting states one, else ''."""
    m = _TZ_RE.search(text or "")
    return re.sub(r"\s+", " ", m.group(1)).strip()[:60] if m else ""
