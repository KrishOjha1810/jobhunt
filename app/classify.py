"""Classify a job listing's employment type and fresher signals from its title + description.

Keyword-first (cheap, deterministic, no LLM) so it runs on every ingested job. Powers fresher mode:
internships, internships that convert to full-time (PPO), trainee/graduate programs, and full-time
roles with a training/probation period. The single highest-value signal for a student is has_ppo
(an internship that converts), so it is detected explicitly.
"""
import re

# employment_type values, most specific first
INTERNSHIP = "internship"
INTERNSHIP_PPO = "internship_with_ppo"
TRAINEE_PROGRAM = "trainee_graduate_program"
FULLTIME_PROBATION = "full_time_with_probation"
FULLTIME = "full_time"

_PPO = re.compile(
    r"\b(ppo|pre[\s-]?placement\s+offer|intern(ship)?\s+(leading to|convert\w*|to)\s+(full[\s-]?time|fte)"
    r"|intern[\s-]?to[\s-]?hire|full[\s-]?time\s+conversion|convert\w*\s+to\s+(full[\s-]?time|fte)"
    r"|fte\s+(on|based on)\s+(good\s+)?performance|internship\s+with\s+full[\s-]?time)\b", re.I)
_INTERN = re.compile(r"\b(intern|internship|summer\s+intern|trainee\s+intern|apprentice(ship)?)\b", re.I)
_TRAINEE = re.compile(
    r"\b(graduate\s+trainee|management\s+trainee|graduate\s+engineer\s+trainee|\bget\b"
    r"|campus\s+hire|new\s+grad(uate)?|early\s+career|university\s+(program|hire|graduate)"
    r"|rotational\s+program|graduate\s+program)\b", re.I)
_TRAINING = re.compile(
    r"\b(probation(ary)?\s+period|training\s+period|on[\s-]?the[\s-]?job\s+training"
    r"|stipend\s+during\s+training|\d+[\s-]?month\s+training)\b", re.I)
_FRESHER = re.compile(
    r"\b(fresher|freshers|no\s+experience\s+(required|needed)|final[\s-]?year|recent\s+graduate"
    r"|0\s*[-to]*\s*1\s*years?|0\s+years?|entry[\s-]?level)\b", re.I)


def classify_employment(title: str, description: str = "", structured: dict = None) -> dict:
    """Return {employment_type, has_ppo, has_training_period, is_fresher_friendly}. `structured` may
    carry board-provided booleans (e.g. Internshala's ppo flag) which override the text heuristics."""
    text = f"{title or ''}\n{description or ''}"
    s = structured or {}

    has_ppo = bool(s.get("has_ppo")) or bool(_PPO.search(text))
    is_intern = bool(s.get("is_internship")) or bool(_INTERN.search(text))
    has_training = bool(_TRAINING.search(text))
    is_trainee = bool(_TRAINEE.search(text))
    fresher_words = bool(_FRESHER.search(text))

    if is_intern:
        etype = INTERNSHIP_PPO if has_ppo else INTERNSHIP
    elif is_trainee:
        etype = TRAINEE_PROGRAM
    elif has_training:
        etype = FULLTIME_PROBATION
    else:
        etype = FULLTIME

    is_fresher_friendly = (
        etype in (INTERNSHIP, INTERNSHIP_PPO, TRAINEE_PROGRAM)
        or fresher_words
        or has_training  # a training/probation full-time role is still fresher-accessible
    )
    return {
        "employment_type": etype,
        "has_ppo": has_ppo,
        "has_training_period": has_training,
        "is_fresher_friendly": is_fresher_friendly,
    }


def converts_from_type(employment_type: str) -> bool:
    """Whether a stored employment_type represents a role that can convert to full-time (derivable
    without the description, so the tracker/queue can badge it from the one persisted column)."""
    return employment_type in (INTERNSHIP_PPO, TRAINEE_PROGRAM, FULLTIME_PROBATION)


def converts_to_fulltime(info: dict) -> bool:
    """True when this role can become a full-time job for a student: a PPO internship, a trainee/
    graduate program, or a full-time role with a training/probation ramp. The thing a final-year
    student optimizing for 'an internship that turns into a job' actually wants."""
    return bool(info) and (
        info.get("has_ppo")
        or info.get("employment_type") in (TRAINEE_PROGRAM, FULLTIME_PROBATION)
    )
