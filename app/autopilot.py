"""Auto-Pilot: turn the quality-gated apply queue into a one-click "apply to all" run.

This module is the board-aware layer. It does NOT submit anything itself (the browser extension,
riding the user's own logged-in session, does the actual filling and clicking). Its job is to look at
each queued job's application URL, decide which board it lives on, and say how far we can take it:

  - "auto"          : a standard ATS (Greenhouse / Lever / Ashby / Workday). The extension already
                      fills and submits these, so the whole queue can run hands-off (behind a dry-run
                      the first time, then live on the user's go).
  - "dry_run_first" : a login-walled job board (Naukri / Instahyre / Internshala / LinkedIn). We can
                      drive the apply flow in the user's session, but the selectors must be confirmed
                      once with a dry-run in the user's browser before anything is submitted live.
  - "open_only"     : an arbitrary company career page. No reliable zero-touch (file-upload dialogs,
                      every form different), so these stay "prepare the package and open the page".

Nothing here defeats a CAPTCHA or a bot check; a board that throws one pauses and asks the user.
"""
from urllib.parse import urlparse

# host-substring -> (board key, mode). First match wins; order is longest/most-specific first.
_BOARD_RULES = [
    ("boards.greenhouse.io", ("greenhouse", "auto")),
    ("job-boards.greenhouse.io", ("greenhouse", "auto")),
    ("greenhouse.io", ("greenhouse", "auto")),
    ("jobs.lever.co", ("lever", "auto")),
    ("lever.co", ("lever", "auto")),
    ("jobs.ashbyhq.com", ("ashby", "auto")),
    ("ashbyhq.com", ("ashby", "auto")),
    ("myworkdayjobs.com", ("workday", "auto")),
    ("naukri.com", ("naukri", "dry_run_first")),
    ("instahyre.com", ("instahyre", "dry_run_first")),
    ("internshala.com", ("internshala", "dry_run_first")),
    ("linkedin.com", ("linkedin", "dry_run_first")),
]

# human labels for the UI
BOARD_LABELS = {
    "greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby", "workday": "Workday",
    "naukri": "Naukri", "instahyre": "Instahyre", "internshala": "Internshala",
    "linkedin": "LinkedIn", "other": "Company site",
}


def detect_board(url):
    """Classify a job's application URL. Returns {board, mode, supported, label}.

    `supported` is True when the extension can drive the apply flow at all (auto or dry_run_first);
    an unknown/company page is open_only and not auto-applyable.
    """
    host = ""
    try:
        host = (urlparse(url or "").hostname or "").lower()
    except Exception:
        host = ""
    board, mode = "other", "open_only"
    if host:
        for frag, (b, m) in _BOARD_RULES:
            if host == frag or host.endswith("." + frag) or frag in host:
                board, mode = b, m
                break
    return {
        "board": board,
        "mode": mode,
        "supported": mode in ("auto", "dry_run_first"),
        "label": BOARD_LABELS.get(board, "Company site"),
    }


def annotate(candidates):
    """Tag each queue candidate dict with its board classification (board/mode/supported/label)."""
    out = []
    for c in candidates or []:
        info = detect_board(c.get("url"))
        out.append({**c, **info})
    return out


def queue(user_id, review_queue_fn):
    """Build the Auto-Pilot run list from the existing quality-gated review queue.

    `review_queue_fn` is injected (autoapply.review_queue) so this module stays import-light and easy
    to test. Returns the annotated candidates plus a small summary of how many are auto / dry-run /
    open-only, which the UI uses to set expectations before a run.
    """
    items = annotate(review_queue_fn(user_id))
    summary = {"auto": 0, "dry_run_first": 0, "open_only": 0, "total": len(items)}
    for it in items:
        summary[it["mode"]] = summary.get(it["mode"], 0) + 1
    return {"items": items, "summary": summary}
