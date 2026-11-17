"""Live smoke run for the newer sources (NOT part of the test suite: it hits the real endpoints).

    python scripts/smoke_sources.py            # every new provider + HN
    python scripts/smoke_sources.py keka hn    # just these

Prints, per source: boards tried, boards with jobs, jobs, tech-titled jobs, India-located jobs,
wall time and the Python heap peak while that source ran (tracemalloc), then the process RSS peak.
A provider marked * has no seeded boards yet, so a couple of public check boards are fetched instead.
It reads only public feeds, one request per board (two for HN: thread lookup, then its comments).
"""
import concurrent.futures as cf
import os
import resource
import sys
import time
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.sources import ats, ats_more, hn_hiring  # noqa: E402

_INDIA = ("india", "bengaluru", "bangalore", "mumbai", "pune", "hyderabad", "chennai", "delhi",
          "gurgaon", "gurugram", "noida", "kolkata", "ahmedabad", "kochi", "jaipur")


def _row(name, boards, ok, jobs, secs, peak):
    tech = sum(1 for j in jobs if ats._is_tech(j.get("title")))
    india = sum(1 for j in jobs if any(k in (j.get("location") or "").lower() for k in _INDIA))
    print(f"{name:<11} boards {boards:>3}  live {ok:>3}  jobs {len(jobs):>4}  tech {tech:>4}  "
          f"india {india:>4}  {secs:6.1f}s  heap peak {peak / 1e6:6.1f} MB", flush=True)
    return len(jobs)


# Endpoint-check boards for providers that have no India seed yet (public non-India company boards,
# used ONLY here to prove the adapter works live; they are not in the crawl lists).
_CHECK = {"recruitee": ["channable", "bunq"], "bamboohr": ["zepto"], "breezy": ["glance"],
          "personio": ["personio", "open"], "teamtailor": ["career"]}


def run_provider(prov, workers):
    entries = list(ats_more._seeds(prov))
    if not entries and prov in _CHECK:
        entries, prov_label = _CHECK[prov], prov + "*"
    else:
        prov_label = prov
    fn = ats_more.fetcher(prov)
    tracemalloc.start()
    t0 = time.time()
    jobs, ok = [], 0
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for res in ex.map(fn, entries):
            if res:
                ok += 1
                jobs += res
    secs = time.time() - t0
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    return _row(prov_label, len(entries), ok, jobs, secs, peak)


def run_hn():
    tracemalloc.start()
    t0 = time.time()
    jobs = hn_hiring.fetch()
    secs = time.time() - t0
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    return _row("hn", 1, 1 if jobs else 0, jobs, secs, peak)


def main(argv):
    want = argv or list(ats_more.PROVIDERS) + ["hn"]
    workers = int(os.environ.get("ATS_WORKERS", "") or "20")
    total, t0 = 0, time.time()
    for name in want:
        total += run_hn() if name == "hn" else run_provider(name, workers)
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_mb = rss / 1e6 if sys.platform == "darwin" else rss / 1e3  # bytes on macOS, KB on Linux
    print(f"TOTAL jobs {total} in {time.time() - t0:.1f}s, process RSS peak {rss_mb:.0f} MB")


if __name__ == "__main__":
    main(sys.argv[1:])
