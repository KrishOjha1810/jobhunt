"""More public ATS job boards: Workable, Recruitee, BambooHR, Breezy, Personio, Teamtailor, Keka.

Same idea as ats.py: each company exposes a public, keyless job list straight from its careers page,
so we keep a small curated list of company slugs per provider and fetch them alongside the
Greenhouse/Lever/Ashby boards (ats.fetch() folds these tasks into its own bounded pool, so they share
the same wall-clock budget, the same _norm() shape, the ATS_CAP/ATS_PROVIDER_FLOOR capping and the
same dedupe as every other company board).

Endpoints (all public, no login, no key; verified live before a company was added):
  workable:    GET apply.workable.com/api/v1/widget/accounts/<slug>            (JSON, no JD body)
  recruitee:   GET <slug>.recruitee.com/api/offers/                            (JSON, with JD)
  bamboohr:    GET <slug>.bamboohr.com/careers/list                            (JSON, no JD body)
  breezy:      GET <slug>.breezy.hr/json                                       (JSON, no JD body)
  personio:    GET <slug>.jobs.personio.de/xml  (or .com)                      (XML, with JD)
  teamtailor:  GET <slug>.teamtailor.com/jobs.rss                              (RSS, with JD)
  keka:        GET <slug>.keka.com/careers/api/embedjobs/default/active/<org>  (JSON, with JD)
               where <org> is the portal GUID read once off <slug>.keka.com/careers/ (both paths
               are under /careers, which Keka's robots.txt allows).

Every provider can be switched off with SRC_<PROVIDER>=0 (e.g. SRC_KEKA=0). Each board response is
read with a byte ceiling (ATS_MORE_MAX_BYTES) and each board contributes at most ATS_MORE_PER_BOARD
jobs, so one huge board cannot blow the memory budget on a 512 MB host. Any failure returns [] for
that one board; nothing here ever raises into the run.

Skipped on purpose (see README): Darwinbox (bot-only stub page + WAF 403 on its API), Zoho Recruit
(public portal answers "page does not exist" to a plain client, RSS feed redirects to login),
Freshteam (product sunset, customer subdomains now 404).
"""
import email.utils
import json
import os
import re
import xml.etree.ElementTree as ET

import requests

from .ats import _norm, _strip_html

# A plain, honest User-Agent for these boards (they serve JSON to any client).
_UA = "JobHuntBot/1.0 (personal job matcher; reads public job board feeds)"
_HDRS = {"User-Agent": _UA, "Accept": "application/json, application/xml, text/xml, */*"}
_TIMEOUT = float(os.environ.get("ATS_MORE_TIMEOUT", "") or "12")
_MAX_BYTES = int(os.environ.get("ATS_MORE_MAX_BYTES", "") or str(3 * 1024 * 1024))
PER_BOARD = int(os.environ.get("ATS_MORE_PER_BOARD", "") or "80")

PROVIDERS = ("workable", "recruitee", "bamboohr", "breezy", "personio", "teamtailor", "keka")
# Every direct company-board provider (old + new). Dedupe ranking, the matcher's source prior and the
# JD backfill key off these, so a new provider only needs adding here.
BOARD_PROVIDERS = ("greenhouse", "lever", "ashby", "smartrecruiters", "workday") + PROVIDERS
# Providers whose LIST omits the JD body but which have a cheap per-job detail call (ats.fetch_detail).
DETAIL_PROVIDERS = ("workday", "smartrecruiters", "greenhouse", "workable", "bamboohr")


def enabled(provider):
    """SRC_<PROVIDER>=0 turns one provider off without a deploy of code."""
    return os.environ.get(f"SRC_{provider.upper()}", "1") != "0"


# ---- curated boards (each verified live with open roles; India or remote-India friendly) ----
# Keka entries are (subdomain, portal GUID); the GUID is resolved from the careers page when absent.
WORKABLE = []
RECRUITEE = []
BAMBOOHR = []
BREEZY = []
PERSONIO = []      # "<slug>" (.de host) or "<slug>.com" for the jobs.personio.com host
TEAMTAILOR = []
KEKA = []


def _get(url, max_bytes=None):
    """GET with a timeout and a hard byte ceiling (streamed), so a giant or hostile response is cut
    off instead of being buffered whole. Returns (status, text) or (0, "") on any error."""
    cap = max_bytes or _MAX_BYTES
    try:
        with requests.get(url, headers=_HDRS, timeout=_TIMEOUT, stream=True, allow_redirects=False) as r:
            if r.status_code != 200:
                return r.status_code, ""
            buf, n = [], 0
            for chunk in r.iter_content(65536):
                n += len(chunk)
                if n > cap:
                    return 0, ""   # over budget: drop the board for this run rather than OOM
                buf.append(chunk)
            return 200, b"".join(buf).decode(r.encoding or "utf-8", errors="replace")
    except Exception:
        return 0, ""


def _json(url):
    st, txt = _get(url)
    if st != 200 or not txt:
        return None
    try:
        return json.loads(txt)
    except Exception:
        return None


def _xml(txt):
    """Parse a small XML/RSS document. Refuse DTDs/entities outright (cheap defence against entity
    expansion on old expat builds) since no job feed needs them."""
    if not txt or "<!DOCTYPE" in txt[:2048].upper() or "<!ENTITY" in txt.upper():
        return None
    try:
        return ET.fromstring(txt)
    except Exception:
        return None


def _rfc822_to_iso(s):
    try:
        return email.utils.parsedate_to_datetime(s).strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        return s or ""


def _join(*parts):
    return ", ".join(p for p in (str(x).strip() for x in parts if x) if p)


# ---- Workable ----

def _workable(slug):
    d = _json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}")
    if not isinstance(d, dict):
        return []
    company = d.get("name") or slug
    out = []
    for j in (d.get("jobs") or [])[:PER_BOARD]:
        loc = _join(j.get("city"), j.get("state"), j.get("country"))
        if j.get("telecommuting"):
            loc = _join("Remote", loc)
        desc = " ".join(filter(None, [j.get("department"), j.get("function"), j.get("industry"),
                                      j.get("employment_type"), j.get("experience")]))
        jd = _norm(j.get("title"), company, loc, j.get("url") or j.get("shortlink"), desc,
                   j.get("published_on") or j.get("created_at"), f"workable:{slug}")
        jd["_wk_code"] = j.get("shortcode")   # transient: lets fetch_detail pull the JD body later
        out.append(jd)
    return out


# ---- Recruitee ----

def _recruitee(slug):
    d = _json(f"https://{slug}.recruitee.com/api/offers/")
    offers = (d or {}).get("offers") if isinstance(d, dict) else None
    out = []
    for j in (offers or [])[:PER_BOARD]:
        loc = j.get("location") or _join(j.get("city"), j.get("country"))
        if j.get("remote"):
            loc = _join("Remote", loc)
        desc = _strip_html((j.get("description") or "") + " " + (j.get("requirements") or ""))
        out.append(_norm(j.get("title"), j.get("company_name") or slug, loc,
                         j.get("careers_url"), desc, j.get("published_at") or j.get("created_at"),
                         f"recruitee:{slug}"))
    return out


# ---- BambooHR ----

def _bamboohr(slug):
    d = _json(f"https://{slug}.bamboohr.com/careers/list")
    rows = (d or {}).get("result") if isinstance(d, dict) else None
    out = []
    for j in (rows or [])[:PER_BOARD]:
        lc = j.get("location") or {}
        al = j.get("atsLocation") or {}
        loc = _join(lc.get("city") or al.get("city"), lc.get("state") or al.get("state"),
                    al.get("country"))
        if j.get("isRemote") or str(j.get("locationType") or "") == "1":
            loc = _join("Remote", loc)
        jid = j.get("id")
        if not jid:
            continue
        desc = " ".join(filter(None, [j.get("departmentLabel"), j.get("employmentStatusLabel")]))
        out.append(_norm(j.get("jobOpeningName"), slug, loc,
                         f"https://{slug}.bamboohr.com/careers/{jid}", desc, "", f"bamboohr:{slug}"))
    return out


# ---- Breezy ----

def _breezy(slug):
    rows = _json(f"https://{slug}.breezy.hr/json")
    if not isinstance(rows, list):
        return []
    out = []
    for j in rows[:PER_BOARD]:
        lc = j.get("location") or {}
        loc = lc.get("name") or _join(lc.get("city"), (lc.get("country") or {}).get("name"))
        if lc.get("is_remote"):
            loc = _join("Remote", loc)
        company = (j.get("company") or {}).get("name") or slug
        desc = " ".join(filter(None, [j.get("department"), (j.get("type") or {}).get("name")]))
        out.append(_norm(j.get("name"), company, loc, j.get("url"), desc,
                         j.get("published_date"), f"breezy:{slug}"))
    return out


# ---- Personio ----

def _personio(entry):
    slug, tld = (entry[:-4], "com") if entry.endswith(".com") else (entry, "de")
    st, txt = _get(f"https://{slug}.jobs.personio.{tld}/xml")
    root = _xml(txt) if st == 200 else None
    if root is None:
        return []
    out = []
    for p in root.findall("position")[:PER_BOARD]:
        jid = (p.findtext("id") or "").strip()
        if not jid:
            continue
        offices = [o.text for o in p.findall("office") if o.text] + \
                  [o.text for o in p.findall("additionalOffices/office") if o.text]
        parts = []
        for jd in p.findall("jobDescriptions/jobDescription"):
            parts.append(jd.findtext("name") or "")
            parts.append(jd.findtext("value") or "")
        desc = _strip_html(" ".join(parts)) or " ".join(filter(None, [
            p.findtext("department"), p.findtext("seniority"), p.findtext("occupationCategory")]))
        out.append(_norm(p.findtext("name"), p.findtext("subcompany") or slug, _join(*offices[:3]),
                         f"https://{slug}.jobs.personio.{tld}/job/{jid}", desc,
                         p.findtext("createdAt"), f"personio:{entry}"))
    return out


# ---- Teamtailor ----

_TT = "{https://teamtailor.com/locations}"


def _teamtailor(slug):
    st, txt = _get(f"https://{slug}.teamtailor.com/jobs.rss")
    root = _xml(txt) if st == 200 else None
    if root is None:
        return []
    out = []
    for it in root.findall("channel/item")[:PER_BOARD]:
        locs = []
        for l in it.findall(f"{_TT}locations/{_TT}location"):
            locs.append(_join(l.findtext(f"{_TT}city"), l.findtext(f"{_TT}country")))
        loc = "; ".join(x for x in locs if x)
        if (it.findtext("remoteStatus") or "").lower() in ("fully", "remote"):
            loc = _join("Remote", loc)
        company = (root.findtext("channel/title") or slug).strip()
        out.append(_norm(it.findtext("title"), company, loc, it.findtext("link"),
                         _strip_html(it.findtext("description") or ""),
                         _rfc822_to_iso(it.findtext("pubDate")), f"teamtailor:{slug}"))
    return out


# ---- Keka ----

_KEKA_GUID = re.compile(r"/ats/documents/([0-9a-fA-F-]{36})/")


def keka_portal_guid(slug):
    """Read the career-portal GUID off <slug>.keka.com/careers/ (the page the browser loads first)."""
    st, txt = _get(f"https://{slug}.keka.com/careers/", max_bytes=256 * 1024)
    m = _KEKA_GUID.search(txt or "") if st == 200 else None
    return m.group(1) if m else ""


def _keka(entry):
    slug, guid = (entry, "") if isinstance(entry, str) else (entry[0], entry[1])
    guid = guid or keka_portal_guid(slug)
    if not guid:
        return []
    rows = _json(f"https://{slug}.keka.com/careers/api/embedjobs/default/active/{guid}")
    if not isinstance(rows, list):
        return []
    out = []
    for j in rows[:PER_BOARD]:
        jid = j.get("id")
        if not jid:
            continue
        loc = "; ".join(_join(l.get("city"), l.get("countryName"))
                        for l in (j.get("jobLocations") or []) if isinstance(l, dict))
        skills = ", ".join(j.get("skillNames") or [])
        desc = _strip_html(j.get("description") or "") + (f" Skills: {skills}." if skills else "")
        if j.get("experience"):
            desc = f"Experience: {j['experience']} years. " + desc
        out.append(_norm(j.get("title"), slug, loc or "India",
                         f"https://{slug}.keka.com/careers/jobdetails/{jid}", desc,
                         j.get("publishedOn"), f"keka:{slug}"))
    return out


_FETCHERS = {"workable": _workable, "recruitee": _recruitee, "bamboohr": _bamboohr,
             "breezy": _breezy, "personio": _personio, "teamtailor": _teamtailor, "keka": _keka}


def _seeds(provider):
    """The curated list for a provider, looked up at call time (so tests/env can swap it)."""
    return globals()[provider.upper()]


def fetcher(provider):
    return _FETCHERS.get(provider)


def tasks(discovered=None, limit_companies=0):
    """(fn, entry) pairs for every enabled provider: curated seeds plus boards auto-discovered from
    jobs users saved/applied to. ats.fetch() runs these in its own bounded thread pool."""
    disc = discovered or {}
    out = []
    for prov in PROVIDERS:
        if not enabled(prov):
            continue
        seeds = list(_seeds(prov)[:limit_companies] if limit_companies else _seeds(prov))
        have = {(e if isinstance(e, str) else e[0]) for e in seeds}
        for s in disc.get(prov, []) or []:
            if s and s not in have:
                seeds.append(s)
                have.add(s)
        out += [(_FETCHERS[prov], e) for e in seeds]
    return out


def fetch_detail(job):
    """Full JD body for the providers whose list omits it (Workable, BambooHR). Never raises."""
    src = job.get("source") or ""
    prov, _, slug = src.partition(":")
    try:
        if prov == "workable":
            code = job.get("_wk_code") or (job.get("url") or "").rstrip("/").rsplit("/", 1)[-1]
            d = _json(f"https://apply.workable.com/api/v2/accounts/{slug}/jobs/{code}")
            if isinstance(d, dict):
                return _strip_html(" ".join(filter(None, [d.get("description"), d.get("requirements"),
                                                          d.get("benefits")])))
        elif prov == "bamboohr":
            jid = (job.get("url") or "").rstrip("/").rsplit("/", 1)[-1]
            d = _json(f"https://{slug}.bamboohr.com/careers/{jid}/detail")
            op = ((d or {}).get("result") or {}).get("jobOpening") if isinstance(d, dict) else None
            if isinstance(op, dict):
                return _strip_html(op.get("description") or "")
    except Exception:
        return ""
    return ""
