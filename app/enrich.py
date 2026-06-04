"""V2: LLM resume tailoring. Given a job + the user's resume text, produce a short tailored
block (why-you-fit, 2-3 resume tweaks, a one-line cover note). Provider-agnostic via plain HTTP,
so no heavy SDK. Gracefully disabled when no API key is set.

Providers:
- openai / groq / gemini : OpenAI-compatible /chat/completions endpoint
- anthropic              : Anthropic /v1/messages endpoint

Free options worth knowing: Groq (free, fast) and Google Gemini (generous free tier) are the
cheapest way to run this. OpenAI and Anthropic are paid (cheap, but not free).
"""
import requests
from .config import LLM_PROVIDER, LLM_API_KEY, LLM_MODEL

# OpenAI-compatible base URLs + sensible default models per provider.
OPENAI_COMPAT = {
    "openai": ("https://api.openai.com/v1/chat/completions", "gpt-4o-mini"),
    "groq": ("https://api.groq.com/openai/v1/chat/completions", "llama-3.3-70b-versatile"),
    "gemini": (
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "gemini-2.0-flash",
    ),
}

PROMPT = (
    "You are a sharp technical recruiter helping a candidate apply. Given their resume and a job, "
    "write a SHORT block, no preamble:\n"
    "1) Why you fit (2 lines, concrete, from the resume only, no fabrication).\n"
    "2) Resume tweaks (2-3 bullet rewrites that mirror the JD's keywords, using only real experience).\n"
    "3) Cover note (3 sentences, direct, no buzzwords).\n"
    "No em dashes. Be specific. If the resume genuinely lacks something the JD needs, say so plainly."
)


BOOSTER_PROMPT = (
    "You help a candidate boost their chances AFTER applying to a job. Using their resume and the "
    "job, output exactly these four sections with these headers, no preamble:\n"
    "LINKEDIN CONNECTION NOTE: one note under 300 characters to send a recruiter/hiring manager at "
    "the company, referencing the role and one concrete reason they fit.\n"
    "FOLLOW-UP MESSAGE: a 2-3 sentence message to send after the connection is accepted.\n"
    "RECRUITER EMAIL: a short cold email (subject line + 4-5 sentence body) to a recruiter at the "
    "company about this role.\n"
    "CHECKLIST: 4-5 concrete bullet steps to maximize chances (e.g. find the right person on "
    "LinkedIn, engage with company posts, referral ask). Practical and specific.\n"
    "No em dashes. No fabrication, use only what the resume supports. Keep it tight and ready to send."
)


def available() -> bool:
    return bool(LLM_API_KEY)


INTERVIEW_PROMPT = (
    "You are an interview coach. Given a candidate's resume and a job, output, no preamble:\n"
    "LIKELY QUESTIONS: 6-8 questions they will probably be asked for THIS role (mix of technical "
    "specific to the JD and behavioral), as a numbered list.\n"
    "YOUR ANGLES: for the 3 most important questions, a 1-2 line talking point drawing on the "
    "candidate's real resume experience.\n"
    "ASK THEM: 3 sharp questions the candidate should ask the interviewer.\n"
    "No em dashes. Be specific to this job and this resume, not generic."
)

GAP_PROMPT = (
    "You assess fit between a resume and a job. Output, no preamble:\n"
    "FIT: one line, an honest read (strong / decent / a stretch) and why.\n"
    "MATCHES: 3-4 bullets of what the resume already shows that the JD wants.\n"
    "GAPS: 2-4 bullets of what the JD asks for that the resume does NOT show, each with a concrete "
    "way to address it (a real bullet they could add if true, a quick skill to learn, or how to "
    "reframe existing experience). Never tell them to fabricate.\n"
    "No em dashes. Be concrete and honest, not flattering."
)


def _run(prompt: str, job: dict, resume_text: str, jd_chars: int = 3000):
    """Shared LLM call for the per-job advice features. Returns (text, error)."""
    if not available():
        return "", "No LLM key set (add a free Groq or Gemini key)."
    if not resume_text:
        return "", "No resume on file. Subscribe with a resume first."
    user_msg = (f"RESUME:\n{resume_text[:6000]}\n\nJOB: {job.get('title','')} at "
                f"{job.get('company','')}\n{job.get('description','')[:jd_chars]}")
    try:
        if LLM_PROVIDER == "anthropic":
            return _chat_anthropic(prompt, user_msg), ""
        return _chat_openai_compat(
            [{"role": "system", "content": prompt}, {"role": "user", "content": user_msg}]), ""
    except requests.HTTPError as e:
        code = (e.response.status_code if e.response is not None else "?")
        if code == 429:
            return "", "Daily free AI quota is used up. Resets daily, or use a free Groq key."
        return "", f"{LLM_PROVIDER} API error {code}"
    except Exception as e:
        return "", f"{LLM_PROVIDER} request failed: {e}"


def _json_call(system: str, user_msg: str):
    """Run an LLM call expected to return JSON; parse defensively. Returns (obj, error)."""
    if not available():
        return None, "No LLM key set (add a free Groq or Gemini key)."
    try:
        if LLM_PROVIDER == "anthropic":
            raw = _chat_anthropic(system, user_msg)
        else:
            raw = _chat_openai_compat([{"role": "system", "content": system}, {"role": "user", "content": user_msg}])
        import json as _json
        import re as _re
        m = _re.search(r"\{.*\}", raw, _re.S)
        return (_json.loads(m.group(0)) if m else _json.loads(raw)), ""
    except requests.HTTPError as e:
        code = (e.response.status_code if e.response is not None else "?")
        return None, ("Daily free AI quota used up; resets daily or use a Groq key." if code == 429
                      else f"{LLM_PROVIDER} API error {code}")
    except Exception as e:
        return None, f"could not parse AI response ({e})"


def improve_text(field: str, text: str, jd: str = ""):
    """Rewrite a single resume field (summary or a bullet) , premium inline assist. Returns (text, error)."""
    if not available():
        return "", "No LLM key set (add a free Groq or Gemini key)."
    if not (text or "").strip():
        return "", "nothing to improve"
    instr = {
        "summary": "Rewrite this resume summary to be punchy and specific in 2-3 lines.",
        "bullet": "Rewrite this resume bullet to open with a strong action verb and quantify impact "
                  "where the original implies numbers; keep it to one line.",
        "bullets": "Rewrite EACH line below as a stronger resume bullet (strong action verb, quantify "
                   "impact where the original implies numbers). Return the SAME number of lines, one "
                   "improved bullet per line, no numbering or extra lines.",
    }.get(field, "Tighten this resume text.")
    sys = (instr + " Use only what the original supports, never fabricate. Mirror the target job's "
           "language where relevant. No em dashes. Return ONLY the rewritten text, no quotes/preamble.")
    user_msg = (f"TARGET JOB:\n{jd[:1500]}\n\n" if jd else "") + f"TEXT:\n{text[:1200]}"
    try:
        if LLM_PROVIDER == "anthropic":
            out = _chat_anthropic(sys, user_msg)
        else:
            out = _chat_openai_compat([{"role": "system", "content": sys}, {"role": "user", "content": user_msg}])
        return out.strip().strip('"'), ""
    except requests.HTTPError as e:
        code = (e.response.status_code if e.response is not None else "?")
        return "", ("Daily free AI quota used up; resets daily or use a Groq key." if code == 429
                    else f"{LLM_PROVIDER} API error {code}")
    except Exception as e:
        return "", f"{LLM_PROVIDER} request failed: {e}"


def parse_resume_structured(resume_text: str):
    """Turn raw resume text into a structured, editable resume. Returns (dict, error)."""
    if not resume_text:
        return None, "No resume on file."
    sys = (
        "Convert this resume into JSON with EXACTLY these keys: name, email, phone, links (array of "
        "strings), summary (string), skills (array of strings), experience (array of objects with "
        "keys: title, company, dates, bullets[array of strings]), education (array of objects with "
        "keys: degree, school, dates). Use only what's in the resume; empty string/array if unknown. "
        "Keep bullets verbatim. Output ONLY the JSON object."
    )
    obj, err = _json_call(sys, resume_text[:8000])
    return obj, err


def tailor_edits(resume_json: dict, job_title: str, job_desc: str):
    """Produce concrete, reviewable edits to tailor the resume to a job. Returns (dict, error) where
    dict = {summary: str, add_skills: [str], bullets: [{original, improved, why}]}."""
    import json as _json
    sys = (
        "You tailor a resume to a specific job. Given the candidate's structured resume (JSON) and a "
        "job, return JSON with keys: summary (a rewritten 2-3 line summary tuned to this job, or ''), "
        "add_skills (array of real skills from the candidate that this JD wants but the skills list "
        "omits, [] if none), bullets (array of up to 6 objects {original, improved, why} that rewrite "
        "EXISTING experience bullets to mirror the JD's language and quantify impact). Never fabricate; "
        "improve only what the resume supports. No em dashes. Output ONLY the JSON object."
    )
    user_msg = (f"RESUME JSON:\n{_json.dumps(resume_json)[:6000]}\n\n"
                f"JOB: {job_title}\n{(job_desc or '')[:2500]}")
    return _json_call(sys, user_msg)


def rerank(resume_text: str, jobs: list):
    """Score how well the candidate fits each job, 0-100, in ONE batched LLM call. Returns a list of
    ints aligned to `jobs` (or [] on any failure). This is the strongest matching signal we have,
    the LLM judges real fit far better than keyword overlap. Best-effort; caller falls back."""
    if not available() or not resume_text or not jobs:
        return []
    listing = "\n".join(
        f"{i+1}. {j.get('title','')} @ {j.get('company','')} :: {(j.get('description','') or '')[:500]}"
        for i, j in enumerate(jobs)
    )
    sys = (
        "You are a precise technical recruiter. Given a candidate's resume and a numbered list of "
        "jobs, rate how strong a fit the candidate is for EACH job from 0 to 100 (consider skills "
        "overlap, seniority match, and how core the candidate's experience is to the role). Output "
        "ONLY a JSON array of integers, one per job, in the same order. No prose."
    )
    user_msg = f"RESUME:\n{resume_text[:5000]}\n\nJOBS:\n{listing}"
    try:
        if LLM_PROVIDER == "anthropic":
            raw = _chat_anthropic(sys, user_msg)
        else:
            raw = _chat_openai_compat([{"role": "system", "content": sys}, {"role": "user", "content": user_msg}])
        import json as _json
        import re as _re
        m = _re.search(r"\[.*\]", raw, _re.S)
        arr = _json.loads(m.group(0)) if m else []
        out = []
        for v in arr[:len(jobs)]:
            try:
                out.append(max(0, min(100, int(round(float(v))))))
            except Exception:
                out.append(None)
        return out
    except Exception as e:
        print(f"[enrich] rerank failed: {e}")
        return []


def interview_prep(job: dict, resume_text: str):
    return _run(INTERVIEW_PROMPT, job, resume_text)


def resume_gap(job: dict, resume_text: str):
    return _run(GAP_PROMPT, job, resume_text)


def answer_questions(job: dict, resume_text: str, questions: list, facts: dict = None):
    """Return (answers, error). answers is a list aligned to `questions`, each a short first-person
    answer drafted from the resume + facts. One batched LLM call (cheaper, fewer 429s)."""
    if not available():
        return [], "No LLM key set (add a free Groq or Gemini key)."
    if not questions:
        return [], "no questions"
    facts = facts or {}
    numbered = "\n".join(f"{i+1}. {q}" for i, q in enumerate(questions))
    sys = (
        "You fill job-application screening questions for a candidate. Answer each numbered question "
        "in the FIRST PERSON, concise (1-3 sentences), using ONLY the resume and facts given. Do not "
        "fabricate: if a fact isn't known (exact notice period, salary, etc.), answer with a clearly "
        "bracketed placeholder like '[confirm: ...]'. No em dashes. Output ONLY a JSON array of "
        "strings, one per question, in order. No prose around it."
    )
    user_msg = (
        f"RESUME:\n{resume_text[:4000]}\n\nFACTS: {facts}\n\n"
        f"JOB: {job.get('title','')} at {job.get('company','')}\n{job.get('description','')[:1500]}\n\n"
        f"QUESTIONS:\n{numbered}"
    )
    try:
        if LLM_PROVIDER == "anthropic":
            raw = _chat_anthropic(sys, user_msg)
        else:
            raw = _chat_openai_compat([{"role": "system", "content": sys}, {"role": "user", "content": user_msg}])
        import json as _json
        import re as _re
        m = _re.search(r"\[.*\]", raw, _re.S)
        arr = _json.loads(m.group(0)) if m else None
        if isinstance(arr, list) and arr:
            return [str(a) for a in arr][:len(questions)], ""
        # fallback: split on numbered headers
        parts = _re.split(r"\n\s*\d+[\.\)]\s*", "\n" + raw)
        parts = [p.strip() for p in parts if p.strip()]
        return (parts[:len(questions)] or [raw.strip()]), ""
    except requests.HTTPError as e:
        code = (e.response.status_code if e.response is not None else "?")
        if code == 429:
            return [], "Daily free AI quota is used up. Resets daily, or use a free Groq key."
        return [], f"{LLM_PROVIDER} API error {code}"
    except Exception as e:
        return [], f"{LLM_PROVIDER} request failed: {e}"


def booster(job: dict, resume_text: str):
    """Return (text, error): ready-to-send outreach drafts + a checklist for a job. The user sends
    everything manually, no automated LinkedIn/email actions."""
    if not available():
        return "", "No LLM key set (add a free Groq or Gemini key)."
    if not resume_text:
        return "", "No resume on file. Subscribe with a resume first."
    user_msg = (
        f"RESUME:\n{resume_text[:6000]}\n\n"
        f"JOB: {job.get('title','')} at {job.get('company','')}\n"
        f"{job.get('description','')[:2500]}"
    )
    try:
        if LLM_PROVIDER == "anthropic":
            return _chat_anthropic(BOOSTER_PROMPT, user_msg), ""
        return _chat_openai_compat(
            [{"role": "system", "content": BOOSTER_PROMPT}, {"role": "user", "content": user_msg}]
        ), ""
    except requests.HTTPError as e:
        resp = e.response
        code = (resp.status_code if resp is not None else "?")
        if code == 429:
            return "", ("Daily free AI quota is used up. It resets daily, or use a free Groq key "
                        "(groq.com) for higher limits.")
        return "", f"{LLM_PROVIDER} API error {code}"
    except Exception as e:
        return "", f"{LLM_PROVIDER} request failed: {e}"


def _chat_openai_compat(messages: list) -> str:
    url, default_model = OPENAI_COMPAT.get(LLM_PROVIDER, OPENAI_COMPAT["groq"])
    model = LLM_MODEL or default_model
    r = requests.post(
        url,
        headers={"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
        json={"model": model, "messages": messages, "temperature": 0.4, "max_tokens": 600},
        timeout=40,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def _chat_anthropic(system: str, user: str) -> str:
    model = LLM_MODEL or "claude-haiku-4-5-20251001"
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": LLM_API_KEY,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 600,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        },
        timeout=40,
    )
    r.raise_for_status()
    return r.json()["content"][0]["text"].strip()


def tailor(job: dict, resume_text: str):
    """Return (tailored_block, error). On success error is ''. On failure block is '' and error
    holds a human-readable reason so the UI can show what actually went wrong."""
    if not available():
        return "", "No LLM key set (add a free Gemini or Groq key)."
    if not resume_text:
        return "", "No resume on file. Subscribe with a resume first, then tailor."
    user_msg = (
        f"RESUME:\n{resume_text[:6000]}\n\n"
        f"JOB: {job.get('title','')} at {job.get('company','')}\n"
        f"{job.get('description','')[:3000]}"
    )
    try:
        if LLM_PROVIDER == "anthropic":
            return _chat_anthropic(PROMPT, user_msg), ""
        return _chat_openai_compat(
            [{"role": "system", "content": PROMPT}, {"role": "user", "content": user_msg}]
        ), ""
    except requests.HTTPError as e:
        resp = e.response
        body = (resp.text[:300] if resp is not None else "")
        code = (resp.status_code if resp is not None else "?")
        print(f"[enrich] HTTP {code}: {body}")
        if code == 429:
            return "", ("Daily free AI quota is used up for now. It resets every day. "
                        "Tip: a free Groq key (groq.com) has much higher limits for this feature.")
        if code in (401, 403):
            return "", "The AI key looks invalid or lacks access. Double-check LLM_API_KEY/LLM_PROVIDER."
        return "", f"{LLM_PROVIDER} API error {code}: {body[:160]}"
    except Exception as e:
        print(f"[enrich] error: {e}")
        return "", f"{LLM_PROVIDER} request failed: {e}"
