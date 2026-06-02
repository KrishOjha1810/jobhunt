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


def available() -> bool:
    return bool(LLM_API_KEY)


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
