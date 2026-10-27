"""Auto-Pilot: turn the quality-gated apply queue into a one-click "apply to all" run.

This module is the board-aware layer. It does NOT submit anything itself (the browser extension,
riding the user's own logged-in session, does the actual filling and clicking). Its job is to look at
each queued job's application URL, decide which board it lives on, and say how far we can take it.

The classification is deliberately honest about what a browser can actually automate, learned the
hard way from dry-runs: the only boards where a true one-click "apply to all" works are the ones
where (a) the apply is a single in-page action and (b) the resume is ALREADY stored in the user's
account, so no file-upload dialog is needed. This is exactly the set myjobb restricts itself to; it
never offers to auto-apply to a Workday or a company career page, because it structurally cannot.

Modes:
  - "auto"      : resume pre-stored, single-action apply (Naukri / Instahyre / Internshala, and
                  LinkedIn Easy Apply). The extension can run the whole thing hands-off, behind a
                  dry-run the first time then live on the user's go. These are the ONLY boards the
                  one-click "apply to selected" button touches.
  - "assisted"  : a single-page ATS form (Greenhouse / Lever) where we fill every text field and
                  answer, but the browser blocks auto-attaching the resume file, so the user attaches
                  it and clicks submit. One screen, mostly done for them, not zero-touch.
  - "open_only" : multi-step / account-walled ATS (Workday / Ashby / iCIMS) and arbitrary company
                  career pages. The submit lives behind an Apply click, several steps and a file
                  upload, so there is no reliable zero-touch; we prepare the package and open the page.

`group` collapses modes for the UI: "auto" jobs are the auto-apply feed; everything else is the
"open these yourself" list and is never part of the one-click run. Nothing here defeats a CAPTCHA.
"""
from urllib.parse import urlparse

# host-substring -> (board key, mode). First match wins; order is longest/most-specific first.
_BOARD_RULES = [
    # Truly auto-appliable: resume pre-stored in the account, single-action apply.
    ("naukri.com", ("naukri", "auto")),
    ("instahyre.com", ("instahyre", "auto")),
    ("internshala.com", ("internshala", "auto")),
    ("linkedin.com", ("linkedin", "auto")),
    # Single-page ATS: we fill everything, the user attaches the resume file and clicks submit.
    ("boards.greenhouse.io", ("greenhouse", "assisted")),
    ("job-boards.greenhouse.io", ("greenhouse", "assisted")),
    ("greenhouse.io", ("greenhouse", "assisted")),
    ("jobs.lever.co", ("lever", "assisted")),
    ("lever.co", ("lever", "assisted")),
    # Multi-step / account-walled: no reliable zero-touch, open and do it yourself.
    ("jobs.ashbyhq.com", ("ashby", "open_only")),
    ("ashbyhq.com", ("ashby", "open_only")),
    ("myworkdayjobs.com", ("workday", "open_only")),
]

# human labels for the UI
BOARD_LABELS = {
    "greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby", "workday": "Workday",
    "naukri": "Naukri", "instahyre": "Instahyre", "internshala": "Internshala",
    "linkedin": "LinkedIn", "other": "Company site",
}

# mode -> which feed group it belongs to in the UI
_MODE_GROUP = {"auto": "auto_apply", "assisted": "assisted", "open_only": "open_only"}


def detect_board(url):
    """Classify a job's application URL. Returns {board, mode, group, can_autoapply, supported, label}.

    `can_autoapply` is True only for boards the one-click run can actually complete end to end
    (resume pre-stored, single-action apply). `supported` stays True for assisted boards too, since
    the extension can still fill those, just not submit them hands-off.
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
        "group": _MODE_GROUP.get(mode, "open_only"),
        "can_autoapply": mode == "auto",
        "supported": mode in ("auto", "assisted"),
        "label": BOARD_LABELS.get(board, "Company site"),
    }


def annotate(candidates):
    """Tag each queue candidate dict with its board classification."""
    out = []
    for c in candidates or []:
        info = detect_board(c.get("url"))
        out.append({**c, **info})
    return out


def queue(user_id, review_queue_fn):
    """Build the Auto-Pilot run list from the existing quality-gated review queue.

    `review_queue_fn` is injected (autoapply.review_queue) so this module stays import-light and easy
    to test. Returns the annotated candidates plus a summary that counts BOTH the raw modes and the
    UI groups, so the panel can say "3 I can apply to, 7 you open yourself" before a run.
    """
    items = annotate(review_queue_fn(user_id))
    summary = {
        "auto": 0, "assisted": 0, "open_only": 0, "total": len(items),
        "auto_apply": 0,  # jobs the one-click run can actually complete
    }
    for it in items:
        summary[it["mode"]] = summary.get(it["mode"], 0) + 1
    summary["auto_apply"] = summary["auto"]
    return {"items": items, "summary": summary}
