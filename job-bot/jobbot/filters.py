"""Deterministic filtering and keyword scoring (no API key needed)."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower())


def _contains_any(text: str, words, whole_word: bool = False) -> str | None:
    """Match words at a word start ("robot" hits "Robotics"); whole_word for exclusions
    so that "intern" does not exclude "International"."""
    text = _norm(text)
    end = r"(?![a-z0-9])" if whole_word else ""
    for w in words or []:
        if re.search(r"(?<![a-z0-9])" + re.escape(w.lower()) + end, text):
            return w
    return None


def _parse_date(value: str):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def check(job: dict, cfg: dict) -> tuple[bool, str]:
    """Return (keep, reason) for a job against the `search` config."""
    title = job.get("title", "")
    hit = _contains_any(title, cfg.get("title_exclude"), whole_word=True)
    if hit:
        return False, f"title contains excluded word '{hit}'"

    include = cfg.get("title_include_any")
    if include and not _contains_any(title, include):
        return False, "title matches none of title_include_any"

    locations = cfg.get("locations")
    if locations and not _contains_any(job.get("location", ""), locations):
        return False, f"location '{job.get('location')}' not in allowed locations"

    hit = _contains_any(job.get("description", ""), cfg.get("description_exclude"), whole_word=True)
    if hit:
        return False, f"description contains excluded phrase '{hit}'"

    max_age = cfg.get("max_age_days")
    posted = _parse_date(job.get("posted_at", ""))
    if max_age and posted and posted < datetime.now(timezone.utc) - timedelta(days=max_age):
        return False, f"posted more than {max_age} days ago"

    return True, "passed filters"


def profile_skills(profile: dict) -> list[str]:
    skills = []
    for s in profile.get("skills", []):
        skills.append(s["name"] if isinstance(s, dict) else str(s))
    return skills


def keyword_score(job: dict, profile: dict) -> tuple[int, list[str]]:
    """Share of your skills that the posting mentions, scaled to 0-100."""
    text = _norm(job.get("title", "") + " " + job.get("description", ""))
    skills = profile_skills(profile)
    if not skills:
        return 0, []
    matched = [s for s in skills if re.search(r"(?<![a-z0-9])" + re.escape(s.lower()) + r"(?![a-z0-9])", text)]
    # A posting rarely names more than ~8 of your skills; saturate there.
    score = min(100, round(100 * len(matched) / min(8, len(skills))))
    return score, matched
