"""Build a .docx that reproduces the user's gold resume format exactly, so a tailored copy never needs
a manual recheck. The layout (Cambria 10pt, 0.3"/0.45" margins, centered 19pt name, blue uppercase
section headings, gray meta lines, literal-glyph bullets with a hanging indent, categorized skills,
real hyperlinks) is the measured spec of the user's own resumes. No tables/columns/images, so it stays
ATS-safe. Also a deterministic ATS health score (unchanged).
"""
import io

from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE as _RT

# Measured from the gold resumes (Krish_Ojha_EzXBRL_Resume.docx and the SoftwareEngineer copy , the
# same template). Colors: near-black body, dark-blue headings/links, gray meta lines.
FONT = "Cambria"
BLACK = RGBColor(0x1A, 0x1A, 0x1A)
BLUE = RGBColor(0x1F, 0x4E, 0x79)
GRAY = RGBColor(0x59, 0x59, 0x59)


def _run(p, text, size, color=BLACK, bold=False, underline=False):
    r = p.add_run(text)
    r.font.name = FONT
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.color.rgb = color
    if underline:
        r.font.underline = True
    return r


def _para(doc, align=None, before=0.0, after=2.0, line=1.02):
    """A paragraph with the gold spacing: ~1.04x line height and tight before/after (in points)."""
    p = doc.add_paragraph()
    pf = p.paragraph_format
    if align is not None:
        p.alignment = align
    pf.space_before = Pt(before)
    pf.space_after = Pt(after)
    pf.line_spacing = line
    return p


def _heading(doc, text):
    p = _para(doc, before=5.0, after=1.5)
    _run(p, (text or "").upper(), 11.5, BLUE, bold=True)
    return p


def _bullet(doc, text):
    """A literal '•' glyph with a manual hanging indent , NOT a Word list (gold uses no numbering)."""
    p = _para(doc, before=0.0, after=2.0)
    pf = p.paragraph_format
    pf.left_indent = Inches(0.2)
    pf.first_line_indent = Inches(-0.16)
    _run(p, "•  ", 10, BLACK)
    _run(p, str(text), 10, BLACK)
    return p


def _hyperlink(p, url, text):
    """Append a real colored, underlined hyperlink run to paragraph p (falls back to styled text)."""
    try:
        r_id = p.part.relate_to(url, _RT.HYPERLINK, is_external=True)
        link = OxmlElement("w:hyperlink")
        link.set(qn("r:id"), r_id)
        run = OxmlElement("w:r")
        rpr = OxmlElement("w:rPr")
        rfonts = OxmlElement("w:rFonts")
        rfonts.set(qn("w:ascii"), FONT)
        rfonts.set(qn("w:hAnsi"), FONT)
        rpr.append(rfonts)
        col = OxmlElement("w:color")
        col.set(qn("w:val"), "1F4E79")
        rpr.append(col)
        u = OxmlElement("w:u")
        u.set(qn("w:val"), "single")
        rpr.append(u)
        sz = OxmlElement("w:sz")
        sz.set(qn("w:val"), "20")  # 10pt in half-points
        rpr.append(sz)
        run.append(rpr)
        t = OxmlElement("w:t")
        t.text = text
        t.set(qn("xml:space"), "preserve")
        run.append(t)
        link.append(run)
        p._p.append(link)
    except Exception:
        _run(p, text, 10, BLUE, underline=True)


def _looks_url(s):
    s = str(s or "").strip()
    return s.startswith("http://") or s.startswith("https://") or ("." in s and "/" in s and " " not in s)


def _norm_skills(skills):
    """Normalize skills into [(label, 'comma, list'), ...] category rows. Accepts a dict
    {cat: [items]}, a list of {label/category, items/skills}, or a flat list (-> one 'Skills' row)."""
    rows = []
    if isinstance(skills, dict):
        for k, v in skills.items():
            items = v if isinstance(v, list) else [v]
            rows.append((str(k), ", ".join(str(s) for s in items if s)))
    elif isinstance(skills, list) and skills and isinstance(skills[0], dict):
        for grp in skills:
            label = grp.get("label") or grp.get("category") or "Skills"
            items = grp.get("items") or grp.get("skills") or []
            items = items if isinstance(items, list) else [items]
            rows.append((str(label), ", ".join(str(s) for s in items if s)))
    elif isinstance(skills, list):
        flat = ", ".join(str(s) for s in skills if s)
        if flat:
            rows.append(("Skills", flat))
    return [(lbl, body) for lbl, body in rows if body]


def build_docx(r: dict) -> bytes:
    import docx

    doc = docx.Document()
    sec = doc.sections[0]
    sec.page_width = Inches(8.5)
    sec.page_height = Inches(11)
    sec.top_margin = Inches(0.3)
    sec.bottom_margin = Inches(0.3)
    sec.left_margin = Inches(0.45)
    sec.right_margin = Inches(0.45)

    normal = doc.styles["Normal"].font
    normal.name = FONT
    normal.size = Pt(10)

    # Header: name (centered, bold, 19pt), contact line, links line.
    name = (r.get("name") or "").strip()
    if name:
        p = _para(doc, align=WD_ALIGN_PARAGRAPH.CENTER, before=0.0, after=2.0)
        _run(p, name, 19, BLACK, bold=True)

    city = r.get("location") or r.get("city") or ""
    contact_bits = [str(x) for x in (r.get("email"), r.get("phone"), city) if x]
    if contact_bits:
        p = _para(doc, align=WD_ALIGN_PARAGRAPH.CENTER, before=0.0, after=2.0)
        _run(p, "  |  ".join(contact_bits), 10, BLACK)

    links = [x for x in (r.get("links") or []) if x]
    if links:
        p = _para(doc, align=WD_ALIGN_PARAGRAPH.CENTER, before=0.0, after=2.0)
        for i, link in enumerate(links):
            if i:
                _run(p, "   |   ", 10, BLACK)
            # allow {"text": "LinkedIn", "url": "..."} so the anchor shows a label, not the raw URL
            if isinstance(link, dict):
                text = str(link.get("text") or link.get("label") or "").strip()
                href = str(link.get("url") or link.get("href") or "").strip()
                if href:
                    url = href if href.startswith("http") else "https://" + href
                    _hyperlink(p, url, text or url)
                elif text:
                    _run(p, text, 10, BLUE, underline=True)
                continue
            s = str(link).strip()
            if _looks_url(s):
                url = s if s.startswith("http") else "https://" + s
                _hyperlink(p, url, s.replace("https://", "").replace("http://", ""))
            else:
                _run(p, s, 10, BLUE, underline=True)

    if r.get("summary"):
        _heading(doc, "Professional Summary")
        p = _para(doc, before=2.0, after=2.0)
        _run(p, str(r["summary"]), 10, BLACK)

    skill_rows = _norm_skills(r.get("skills"))
    if skill_rows:
        _heading(doc, "Skills")
        for label, body in skill_rows:
            p = _para(doc, before=0.5, after=0.5)
            _run(p, f"{label}: ", 10, BLACK, bold=True)
            _run(p, body, 10, BLACK)

    if r.get("experience"):
        _heading(doc, "Professional Experience")
        for e in r["experience"]:
            if e.get("title"):
                p = _para(doc, before=2.0, after=0.0)
                _run(p, str(e["title"]), 11, BLACK, bold=True)
            meta = "  |  ".join(str(x) for x in [e.get("company"), e.get("dates"), e.get("location")] if x)
            if meta:
                p = _para(doc, before=0.0, after=2.0)
                _run(p, meta, 9.5, GRAY)
            for b in (e.get("bullets") or []):
                if b:
                    _bullet(doc, b)

    if r.get("projects"):
        _heading(doc, "Key Projects")
        for pr in r["projects"]:
            if pr.get("name"):
                p = _para(doc, before=3.0, after=0.0)
                _run(p, str(pr["name"]), 11, BLUE, bold=True)
            meta = "  |  ".join(str(x) for x in [pr.get("stack"), pr.get("dates")] if x)
            if meta:
                p = _para(doc, before=0.0, after=2.0)
                _run(p, meta, 9.5, GRAY)
            for b in (pr.get("bullets") or []):
                if b:
                    _bullet(doc, b)

    for s in (r.get("sections") or []):
        if s.get("heading"):
            _heading(doc, s["heading"])
        for it in (s.get("items") or []):
            if it:
                _bullet(doc, it)

    if r.get("education"):
        _heading(doc, "Education")
        for ed in r["education"]:
            p = _para(doc, before=2.0, after=0.0)
            if ed.get("school"):
                _run(p, str(ed["school"]), 10, BLACK, bold=True)
            tail = "   |   ".join(str(x) for x in [ed.get("degree"), ed.get("dates")] if x)
            if tail:
                _run(p, ("   |   " if ed.get("school") else "") + tail, 10, BLACK)

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# Strong action verbs and weak openers, for the deterministic ATS health checks.
_ACTION = ("led", "built", "shipped", "designed", "architected", "implemented", "launched",
           "improved", "reduced", "increased", "drove", "owned", "delivered", "created", "scaled",
           "automated", "developed", "optimized", "migrated", "established")


def ats_health(r: dict, years=None) -> dict:
    """Deterministic resume-quality score (0-100) + concrete checks. Independent of any JD.
    Delegates to the ResumeWorded-style analyzer (Impact / Brevity / Style / Sections) so the score
    is explainable; keeps `score`+`checks` keys for existing callers and adds `categories`.
    `years` (experience) makes the length targets tier-aware."""
    from .ats_rules import quality_report
    return quality_report(r, years=years)
