"""ATS auto-detect: given a company careers URL, find which supported ATS hosts its jobs and the
(board, slug) to crawl, so an admin can add a company by URL instead of hunting for its slug.

How it decides (cheapest and most certain first):
  1. The URL itself is an ATS board link (boards.greenhouse.io/x, x.keka.com/careers, ...).
  2. Fetch the careers page (SSRF-guarded, size-capped, at most 3 redirect hops). If a redirect
     lands on an ATS host, that is the answer.
  3. Otherwise scan the HTML for each provider's embed signatures (Greenhouse embed script
     `for=<slug>`, a jobs.lever.co/<slug> link, an Ashby embed, a <slug>.recruitee.com widget, ...)
     and count hits per (provider, slug). The board with the most hits wins; ties go to the
     provider order below. All candidates are returned so a human can see a close second.
  4. Optionally validate: run the provider's real fetcher and report how many open jobs it has, so
     a wrong slug (0 jobs) is visible before anything is added.

It never logs in, never submits forms, never follows more than 3 redirects, and refuses private,
loopback and link-local addresses (this runs on the server).
"""
import ipaddress
import re
import socket
import time
from urllib.parse import urljoin, urlsplit

import requests

from . import ats, ats_more

_UA = {"User-Agent": "JobHuntBot/1.0 (personal job matcher; ATS detection)",
       "Accept": "text/html,application/xhtml+xml,*/*"}
_MAX_HTML = 1_500_000
_TIMEOUT = 12

# Provider order breaks ties (older, better-supported boards first).
ORDER = ("greenhouse", "lever", "ashby", "smartrecruiters", "workday") + ats_more.PROVIDERS

_SLUG = r"([A-Za-z0-9][A-Za-z0-9_-]{0,62})"
_SIGS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/embed/job_board(?:/js)?\?for=" + _SLUG, re.I)),
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/embed/job_app\?(?:[^\"'\s>]*&(?:amp;)?)?for=" + _SLUG, re.I)),
    ("greenhouse", re.compile(r"boards-api\.greenhouse\.io/v1/boards/" + _SLUG, re.I)),
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?!embed\b)" + _SLUG, re.I)),
    ("lever", re.compile(r"(?:jobs|api)\.lever\.co/(?:v0/postings/)?" + _SLUG, re.I)),
    ("ashby", re.compile(r"(?:jobs\.ashbyhq\.com|api\.ashbyhq\.com/posting-api/job-board)/" + _SLUG, re.I)),
    ("smartrecruiters", re.compile(r"(?:jobs|careers|api)\.smartrecruiters\.com/(?:v1/companies/)?" + _SLUG, re.I)),
    ("workday", re.compile(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:wday/cxs/[a-z0-9-]+/)?(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/(?:api/v[13]/(?:widget/)?accounts/)?" + _SLUG, re.I)),
    ("workable", re.compile(r"//" + _SLUG + r"\.workable\.com", re.I)),
    ("recruitee", re.compile(r"//" + _SLUG + r"\.recruitee\.com", re.I)),
    ("bamboohr", re.compile(r"//" + _SLUG + r"\.bamboohr\.com/(?:careers|jobs|js/embed)", re.I)),
    ("breezy", re.compile(r"//" + _SLUG + r"\.breezy\.hr", re.I)),
    ("personio", re.compile(r"//" + _SLUG + r"\.jobs\.personio\.(de|com)", re.I)),
    ("teamtailor", re.compile(r"//" + _SLUG + r"\.teamtailor\.com", re.I)),
    ("keka", re.compile(r"//" + _SLUG + r"\.keka\.com/careers", re.I)),
]
_RESERVED = {"v1", "v2", "embed", "api", "search", "j", "js", "static", "assets", "jobs", "careers",
             "www", "app", "help", "support", "docs", "blog", "login", "apply", "cdn"} | ats._VENDOR_SUBS


def _public_host(host):
    """True only if every address the host resolves to is a public unicast address."""
    try:
        infos = socket.getaddrinfo(host, None)
    except Exception:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0].split("%", 1)[0])
        except ValueError:
            return False
        # is_global also rejects CGNAT 100.64/10 (Tailscale), 6to4 and other non-public ranges
        # that the individual is_private/is_loopback checks miss on older Pythons.
        if not ip.is_global or ip.is_multicast:
            return False
        if ip.version == 6 and (ip.sixtofour or ip.teredo
                                or (ip.ipv4_mapped and not ip.ipv4_mapped.is_global)):
            return False  # tunnel/mapped forms can wrap a private IPv4
    return True


def _safe_url(url):
    try:
        p = urlsplit(url)
    except Exception:
        return False
    return p.scheme in ("http", "https") and bool(p.hostname) and _public_host(p.hostname)


def fetch_page(url, max_hops=3):
    """GET a careers page with manual, re-validated redirects and a byte cap. Returns (final_url,
    html) or (final_url, "") when blocked/failed. Never raises."""
    cur = url
    for _ in range(max_hops + 1):
        if not _safe_url(cur):
            return cur, ""
        try:
            # trust_env=False: never route through an env-configured proxy. (Known gap: the name is
            # resolved again at connect time, so DNS rebinding is not fully closed; the endpoint is
            # RUN_TOKEN-only and only reads the page.)
            sess = requests.Session()
            sess.trust_env = False
            with sess.get(cur, headers=_UA, timeout=_TIMEOUT, stream=True,
                          allow_redirects=False) as r:
                if r.is_redirect or r.status_code in (301, 302, 303, 307, 308):
                    nxt = r.headers.get("location") or ""
                    if not nxt:
                        return cur, ""
                    cur = urljoin(cur, nxt)
                    if ats.board_from_url(cur):
                        return cur, ""  # redirected onto an ATS board: the URL itself answers
                    continue
                if r.status_code != 200:
                    return cur, ""
                buf, n, deadline = [], 0, time.monotonic() + 20
                for chunk in r.iter_content(65536):
                    n += len(chunk)
                    buf.append(chunk)
                    if n >= _MAX_HTML or time.monotonic() > deadline:
                        break  # the embed markers live near the top; the tail is not needed
                return cur, b"".join(buf).decode(r.encoding or "utf-8", errors="replace")
        except Exception:
            return cur, ""
    return cur, ""


def scan_html(html):
    """Count embed-signature hits per (provider, slug) in a page. Returns a list of
    {"board", "slug", "hits"} sorted best first."""
    counts = {}
    for prov, rx in _SIGS:
        for m in rx.finditer(html or ""):
            if prov == "workday":
                slug = _workday_from(m.group(0) if m.group(0).startswith("http") else "https://" + m.group(0))
                if not slug:
                    continue  # locale-only or /wday/cxs API paths are not a site name
            elif prov == "personio":
                slug = m.group(1).lower() + (".com" if m.group(2).lower() == "com" else "")
            else:
                slug = m.group(1)
                if prov not in ("smartrecruiters",):  # SR slugs are case-sensitive; others are not
                    slug = slug.lower()
                if slug.lower() in _RESERVED:
                    continue
            counts[(prov, slug)] = counts.get((prov, slug), 0) + 1
    rank = {p: i for i, p in enumerate(ORDER)}
    out = [{"board": p, "slug": s, "hits": n} for (p, s), n in counts.items()]
    out.sort(key=lambda d: (-d["hits"], rank.get(d["board"], 99)))
    return out


def count_jobs(board, slug):
    """Live-validate a detection by running the real fetcher once. Returns job count or None if
    the provider is not fetchable by slug (unknown provider)."""
    fns = {"greenhouse": ats._greenhouse, "lever": ats._lever, "ashby": ats._ashby,
           "smartrecruiters": ats._smartrecruiters}
    try:
        if board == "workday":
            t, dc, site = slug.split("|")
            return len(ats._workday((t, dc, site)))
        fn = fns.get(board) or ats_more.fetcher(board)
        if not fn:
            return None
        return len(fn(slug) or [])
    except Exception:
        return 0


def _workday_from(url):
    """Workday boards are (tenant, datacenter, site); encode them as one 'tenant|wdN|site' slug."""
    m = re.match(r"https?://([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:wday/cxs/[a-z0-9-]+/)?"
                 r"(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)", url or "")
    if not m or m.group(3).lower() in ("wday", "en-us") or re.fullmatch(r"[a-z]{2}-[A-Z]{2}", m.group(3)):
        return None
    return f"{m.group(1).lower()}|{m.group(2).lower()}|{m.group(3)}"


def detect(url, validate=False):
    """Detect the ATS behind a careers URL. Returns
    {"url", "final_url", "board", "slug", "via", "candidates": [...], "jobs"?}; board is None when
    nothing was found."""
    url = (url or "").strip()
    if url and "://" not in url:
        url = "https://" + url
    res = {"url": url, "final_url": url, "board": None, "slug": None, "via": None, "candidates": []}
    if not url:
        return res
    b = ats.board_from_url(url)
    wd = None if b else _workday_from(url)
    if wd:
        b = ("workday", wd)
    if b:
        res.update(board=b[0], slug=b[1], via="url", candidates=[{"board": b[0], "slug": b[1], "hits": 1}])
    else:
        final, html = fetch_page(url)
        res["final_url"] = final
        b = (ats.board_from_url(final) or (("workday", _workday_from(final)) if _workday_from(final) else None)) \
            if final != url else None
        if b:
            res.update(board=b[0], slug=b[1], via="redirect",
                       candidates=[{"board": b[0], "slug": b[1], "hits": 1}])
        else:
            cands = scan_html(html)
            res["candidates"] = cands[:8]
            if cands:
                res.update(board=cands[0]["board"], slug=cands[0]["slug"], via="html")
    if validate and res["board"]:
        res["jobs"] = count_jobs(res["board"], res["slug"])
    return res
