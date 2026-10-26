"""Automatic resume validator , the gate that lets a tailored resume ship without a human recheck.

Every check runs against the generated .docx (and the rendered PDF when a renderer is available). A
resume is submit-ready only if validate_resume returns ok=True. The page-fit check is renderer-optional:
with a real LibreOffice present the pypdf page count is authoritative; without one, a conservative
content-budget estimator is used that FAILS a near-boundary resume (holds it for a render) rather than
letting a possibly-two-page resume pass as one. A missing renderer can only ever make this stricter.
"""
import io
import math
import os
import shutil
import subprocess

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH

# The gold format contract (measured from the user's own resumes).
SPEC = {
    "font": "Cambria",
    "body_pt": 10.0,
    "name_pt": 19.0,
    "margins": {"top": 0.3, "bottom": 0.3, "left": 0.45, "right": 0.45},
    "name_centered": True,
    "required_sections": ["PROFESSIONAL SUMMARY", "SKILLS", "PROFESSIONAL EXPERIENCE", "EDUCATION"],
}

_PAGE_W, _PAGE_H = 612.0, 792.0  # US Letter in points


def working_renderer():
    """Return a runnable soffice/libreoffice path, or None. A dangling shim on PATH returns None
    (we actually execute --version, we do not trust PATH presence)."""
    for name in ("soffice", "libreoffice"):
        exe = shutil.which(name)
        if not exe:
            continue
        try:
            r = subprocess.run([exe, "--version"], capture_output=True, timeout=25)
            if r.returncode == 0 and (r.stdout or b"").strip():
                return exe
        except Exception:
            pass
    return None


def _doc(docx_bytes):
    return Document(io.BytesIO(docx_bytes))


def _eff_size_pt(p, doc):
    for r in p.runs:
        if r.font.size:
            return r.font.size.pt
    try:
        return doc.styles["Normal"].font.size.pt or 10.0
    except Exception:
        return 10.0


def estimate_pages(doc):
    """Conservative content-budget page estimate from the .docx geometry. Over-estimates height so a
    borderline resume is flagged, never falsely passed."""
    sec = doc.sections[0]
    usable_w = _PAGE_W - sec.left_margin.pt - sec.right_margin.pt
    usable_h = _PAGE_H - sec.top_margin.pt - sec.bottom_margin.pt
    total_h = 0.0
    for p in doc.paragraphs:
        sz = _eff_size_pt(p, doc)
        char_w = 0.52 * sz  # proportional width for a Cambria-class face (conservative)
        indent = p.paragraph_format.left_indent.pt if p.paragraph_format.left_indent else 0.0
        avail_w = max(usable_w - indent, 1.0)
        cpl = max(int(avail_w / char_w), 1)
        n_lines = max(1, math.ceil(max(len(p.text), 1) / cpl))
        line_h = sz * 1.20
        after = p.paragraph_format.space_after.pt if p.paragraph_format.space_after else 8.0
        before = p.paragraph_format.space_before.pt if p.paragraph_format.space_before else 0.0
        total_h += n_lines * line_h + after + before
    return total_h / usable_h


def _headings(doc):
    """Paragraphs that look like a gold section heading: a bold, UPPERCASE first run."""
    out = []
    for i, p in enumerate(doc.paragraphs):
        if p.runs and p.runs[0].bold and p.text.strip() and p.text.strip().isupper():
            out.append((i, p.text.strip()))
    return out


def check_format(doc):
    f = doc.styles["Normal"].font
    font_ok = (f.name == SPEC["font"]) and (f.size is not None and abs(f.size.pt - SPEC["body_pt"]) < 0.1)
    sec = doc.sections[0]
    m = SPEC["margins"]
    m_ok = (abs(sec.top_margin.inches - m["top"]) < 0.02 and abs(sec.bottom_margin.inches - m["bottom"]) < 0.02
            and abs(sec.left_margin.inches - m["left"]) < 0.02 and abs(sec.right_margin.inches - m["right"]) < 0.02)
    if font_ok and m_ok:
        return True, "format ok", "docx"
    return False, f"font/margins off (font={f.name}/{f.size}, margins top={sec.top_margin.inches:.2f})", "docx"


def check_name_contact(doc, rj):
    paras = [p for p in doc.paragraphs if p.text.strip()]
    if not paras:
        return False, "empty document", "docx"
    nm = paras[0]
    name_ok = nm.text.strip() == (rj.get("name") or "").strip()
    size_ok = any(r.font.size and abs(r.font.size.pt - SPEC["name_pt"]) < 0.1 for r in nm.runs)
    center_ok = (nm.alignment == WD_ALIGN_PARAGRAPH.CENTER) if SPEC["name_centered"] else True
    import re
    contact_ok = any(re.search(r"[\w.+-]+@[\w-]+\.\w+", p.text) for p in paras[1:4])
    if name_ok and size_ok and center_ok and contact_ok:
        return True, "name + contact ok", "docx"
    return False, (f"name_ok={name_ok} size_ok={size_ok} centered={center_ok} email_present={contact_ok}"), "docx"


def check_sections(doc):
    heads = _headings(doc)
    names = {t for _, t in heads}
    missing = [s for s in SPEC["required_sections"] if s not in names]
    empty_bullets = any(p.runs and p.runs[0].text.startswith("•") and not p.text.replace("•", "").strip()
                        for p in doc.paragraphs)
    # dangling header: a heading with no non-heading, non-empty paragraph before the next heading/EOF
    dangling = []
    hidx = [i for i, _ in heads]
    paras = doc.paragraphs
    for k, i in enumerate(hidx):
        nxt = hidx[k + 1] if k + 1 < len(hidx) else len(paras)
        body = any(paras[j].text.strip() for j in range(i + 1, nxt))
        if not body:
            dangling.append(heads[k][1])
    if not missing and not empty_bullets and not dangling:
        return True, "sections ok", "docx"
    return False, f"missing={missing} empty_bullets={empty_bullets} dangling={dangling}", "docx"


def check_page_fit(doc, years, pdf_path=None):
    max_pages = 1 if (years is not None and years <= 2) else 2
    exe = working_renderer()
    if exe and pdf_path and os.path.exists(pdf_path):
        import pypdf
        n = len(pypdf.PdfReader(pdf_path).pages)
        ok = (n == 1) if max_pages == 1 else (1 <= n <= 2)
        return ok, f"rendered {n} pages (max {max_pages})", "rendered"
    est = estimate_pages(doc)
    if est > max_pages + 0.02:
        return False, f"estimate {est:.2f} pages > budget {max_pages}", "estimated"
    if est > max_pages - 0.10:
        return False, f"estimate {est:.2f} near boundary {max_pages}; render required to confirm", "needs_render"
    return True, f"estimate {est:.2f} pages within budget {max_pages}", "estimated"


def check_selectable(pdf_path, rj):
    if not (pdf_path and os.path.exists(pdf_path)):
        return None, "no PDF (renderer absent)", "skipped"
    import pypdf
    text = " ".join((pg.extract_text() or "") for pg in pypdf.PdfReader(pdf_path).pages)
    words = len(text.split())
    first = (rj.get("name") or "").split()[0].lower() if rj.get("name") else ""
    ok = words >= 80 and (not first or first in text.lower()) and "@" in text
    return ok, f"{words} words extracted, identity_present={bool(first and first in text.lower())}", "rendered"


def _flatten_skills(skills):
    if isinstance(skills, dict):
        out = []
        for v in skills.values():
            out.extend(v if isinstance(v, list) else [v])
        return [str(s) for s in out if s]
    if isinstance(skills, list) and skills and isinstance(skills[0], dict):
        out = []
        for g in skills:
            out.extend(g.get("items") or g.get("skills") or [])
        return [str(s) for s in out if s]
    if isinstance(skills, list):
        return [str(s) for s in skills if s]
    return []


def check_length_budget(rj, years):
    """Guard against a tailoring that BLOATED the resume past its page budget. Being short is fine
    (page_fit covers underflow and a short resume is not 'broken'); only overflow is a failure here."""
    try:
        from .ats_rules import _len_targets, _resume_words
        bullets = []
        for e in (rj.get("experience") or []):
            bullets += [b for b in (e.get("bullets") or []) if b]
        for p in (rj.get("projects") or []):
            bullets += [b for b in (p.get("bullets") or []) if b]
        for s in (rj.get("sections") or []):
            bullets += [b for b in (s.get("items") or []) if b]
        wmin, wmax, bmin, bmax, _ = _len_targets(years)
        words = _resume_words(rj, bullets, _flatten_skills(rj.get("skills")))
        if words > wmax * 1.3:
            return False, f"too long: {words} words >> budget {wmax} (bloated, will overflow)", "content"
        if len(bullets) > bmax + 4:
            return False, f"too many bullets: {len(bullets)} > {bmax}", "content"
        return True, f"{words} words, {len(bullets)} bullets within budget", "content"
    except Exception as e:
        return None, f"length check skipped ({e})", "skipped"


def validate_resume(docx_bytes, rj, years, pdf_path=None):
    """Run every check. Returns {ok, failures:[...], checks:[{name,ok,msg,level}]}. ok is True only if
    no check FAILED and no page-fit check is left unconfirmed (needs_render / skipped-with-no-render)."""
    doc = _doc(docx_bytes)
    results = [
        ("format", check_format(doc)),
        ("name_contact", check_name_contact(doc, rj)),
        ("sections", check_sections(doc)),
        ("page_fit", check_page_fit(doc, years, pdf_path)),
        ("selectable", check_selectable(pdf_path, rj)),
        ("length_budget", check_length_budget(rj, years)),
    ]
    checks, failures = [], []
    for name, (ok, msg, level) in results:
        checks.append({"name": name, "ok": ok, "msg": msg, "level": level})
        if ok is False:
            failures.append(f"{name}: {msg}")
    # page-fit must be positively confirmed (rendered, or estimated-within-budget), never needs_render
    pf = next(c for c in checks if c["name"] == "page_fit")
    page_confirmed = pf["ok"] is True and pf["level"] in ("rendered", "estimated")
    return {"ok": not failures and page_confirmed, "failures": failures, "checks": checks}
