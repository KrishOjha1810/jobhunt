"""Render + auto-fit layer that sits between the resume builder and the apply flow.

Given a (tailored) resume JSON, it builds the gold-format .docx, renders a PDF when a real LibreOffice
is present, validates the result, and , if the only problem is that the content overflows its page
budget , trims the lowest-value bullets and retries until it fits. The caller gets a resume that is
either confirmed submit-ready or an explicit list of why it is not, so nothing broken is ever attached.
"""
import copy
import os
import subprocess
import tempfile

from . import resume_export, resume_validation


def render_pdf(docx_bytes):
    """Render .docx bytes to a PDF path using a real LibreOffice, or None if none is available.
    Uses a throwaway per-call profile dir so there is no stale-cache class of bug."""
    exe = resume_validation.working_renderer()
    if not exe:
        return None
    workdir = tempfile.mkdtemp(prefix="jh_render_")
    docx_path = os.path.join(workdir, "resume.docx")
    with open(docx_path, "wb") as f:
        f.write(docx_bytes)
    profile = "file://" + os.path.join(workdir, "lo_profile")
    try:
        subprocess.run(
            [exe, "--headless", "--invisible", "--nodefault", "--norestore", "--nolockcheck",
             "-env:UserInstallation=" + profile,
             "--convert-to", "pdf:writer_pdf_Export", "--outdir", workdir, docx_path],
            capture_output=True, timeout=120, check=True,
        )
        pdf_path = os.path.join(workdir, "resume.pdf")
        if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 0:
            return pdf_path
    except Exception as e:
        print(f"[resume_render] soffice failed: {e}")
    return None


def _removable_bullet_locations(rj):
    """Yield (container_key, entry_index) for entries that have more than one bullet, projects first
    then experience (trim the least load-bearing content first), each from its last bullet."""
    order = []
    for idx in range(len(rj.get("projects") or []) - 1, -1, -1):
        if len(rj["projects"][idx].get("bullets") or []) > 1:
            order.append(("projects", idx))
    for idx in range(len(rj.get("experience") or []) - 1, -1, -1):
        if len(rj["experience"][idx].get("bullets") or []) > 1:
            order.append(("experience", idx))
    return order


def _trim_one(rj):
    """Drop a single lowest-priority bullet (keep at least one per entry). Returns True if one was cut."""
    for key, idx in _removable_bullet_locations(rj):
        bullets = rj[key][idx].get("bullets") or []
        if len(bullets) > 1:
            bullets.pop()  # drop the trailing (lowest-priority) bullet of this entry
            rj[key][idx]["bullets"] = bullets
            return True
    return False


def _fixable_by_trim(validation):
    """True when every failure is something trimming bullets can fix (page overflow and/or a
    length-budget bloat). Format/section/identity failures are NOT trim-fixable, so we don't loop."""
    fails = validation.get("failures") or []
    return bool(fails) and all(f.startswith("page_fit") or f.startswith("length_budget") for f in fails)


def _ats_score(rj, years):
    """The deterministic ATS-health score (0-100) of the resume, so every tailored copy is tracked
    and we never ship one that quietly regressed on ATS."""
    try:
        return resume_export.ats_health(rj, years=years).get("score")
    except Exception:
        return None


def build_validated(rj, years, autofit=True, max_trims=10):
    """Build -> (render) -> validate, trimming on overflow. Returns a dict with docx bytes, the pdf
    path (or None), the validation report, and how many bullets were trimmed to make it fit."""
    work = copy.deepcopy(rj or {})
    trimmed = 0
    for _ in range(max_trims + 1):
        docx_bytes = resume_export.build_docx(work)
        pdf_path = render_pdf(docx_bytes)
        validation = resume_validation.validate_resume(docx_bytes, work, years, pdf_path)
        if validation["ok"] or not autofit:
            return {"docx": docx_bytes, "pdf_path": pdf_path, "validation": validation,
                    "trimmed": trimmed, "resume": work, "ats": _ats_score(work, years)}
        if _fixable_by_trim(validation) and _trim_one(work):
            trimmed += 1
            continue
        return {"docx": docx_bytes, "pdf_path": pdf_path, "validation": validation,
                "trimmed": trimmed, "resume": work, "ats": _ats_score(work, years)}
    # ran out of trims , return the last build with its (still-failing) validation
    docx_bytes = resume_export.build_docx(work)
    pdf_path = render_pdf(docx_bytes)
    return {"docx": docx_bytes, "pdf_path": pdf_path,
            "validation": resume_validation.validate_resume(docx_bytes, work, years, pdf_path),
            "trimmed": trimmed, "resume": work, "ats": _ats_score(work, years)}


def apply_edits_to_json(rj, edits):
    """Apply reviewable tailor_edits ({summary, add_skills, bullets:[{original, improved}]}) to a COPY
    of the structured resume, producing the tailored resume JSON that build_docx renders."""
    out = copy.deepcopy(rj or {})
    e = edits or {}
    if e.get("summary"):
        out["summary"] = e["summary"]
    rewrites = {}
    for b in (e.get("bullets") or []):
        orig = (b.get("original") or "").strip()
        imp = (b.get("improved") or "").strip()
        if orig and imp:
            rewrites[" ".join(orig.split()).lower()] = imp

    def _rewrite_list(bullets):
        res = []
        for b in bullets or []:
            key = " ".join(str(b).split()).lower()
            res.append(rewrites.get(key, b))
        return res

    for sect in ("experience", "projects"):
        for entry in (out.get(sect) or []):
            if entry.get("bullets"):
                entry["bullets"] = _rewrite_list(entry["bullets"])
    add = [s for s in (e.get("add_skills") or []) if s]
    if add:
        sk = out.get("skills")
        if isinstance(sk, dict):
            sk.setdefault("Additional", [])
            sk["Additional"] = list(sk["Additional"]) + [s for s in add if s not in sk["Additional"]]
        elif isinstance(sk, list):
            existing = {str(x).lower() for x in sk if not isinstance(x, dict)}
            out["skills"] = sk + [s for s in add if s.lower() not in existing]
        else:
            out["skills"] = add
    return out
