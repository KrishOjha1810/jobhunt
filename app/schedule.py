"""Human-friendly view of the fixed daily run schedule, so users know when matches arrive."""
from datetime import datetime, timedelta

from .config import RUN_TZ, RUN_HOURS

try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo(RUN_TZ)
    _TZ_OK = True
except Exception:
    _TZ = None
    _TZ_OK = False


def _fmt_hour(h: int) -> str:
    suffix = "AM" if h < 12 else "PM"
    hour12 = h % 12 or 12
    return f"{hour12} {suffix}"


def _tz_abbrev() -> str:
    # Friendly short label; falls back to the zone name's last segment.
    common = {"Asia/Kolkata": "IST", "America/Los_Angeles": "PT", "America/New_York": "ET", "UTC": "UTC"}
    return common.get(RUN_TZ, RUN_TZ.split("/")[-1])


def describe() -> str:
    """e.g. '9 AM and 9 PM IST, every day'."""
    hours = sorted(set(RUN_HOURS))
    if not hours:
        return "twice a day"
    labels = [_fmt_hour(h) for h in hours]
    if len(labels) == 1:
        joined = labels[0]
    elif len(labels) == 2:
        joined = f"{labels[0]} and {labels[1]}"
    else:
        joined = ", ".join(labels[:-1]) + f", and {labels[-1]}"
    return f"{joined} {_tz_abbrev()}, every day"


def next_run():
    """Return the next scheduled run as a timezone-aware datetime, or None if tz unavailable."""
    if not _TZ_OK or not RUN_HOURS:
        return None
    now = datetime.now(_TZ)
    candidates = []
    for day_offset in (0, 1):
        base = (now + timedelta(days=day_offset)).replace(minute=0, second=0, microsecond=0)
        for h in sorted(set(RUN_HOURS)):
            cand = base.replace(hour=h)
            if cand > now:
                candidates.append(cand)
    return min(candidates) if candidates else None


def next_run_label() -> str:
    """e.g. 'today 9 PM IST' or 'tomorrow 9 AM IST'."""
    nr = next_run()
    if not nr:
        return describe()
    today = datetime.now(_TZ).date()
    when = "today" if nr.date() == today else "tomorrow"
    return f"{when} {_fmt_hour(nr.hour)} {_tz_abbrev()}"
