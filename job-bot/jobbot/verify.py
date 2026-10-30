"""Fact-checks a tailored resume against profile.yaml.

The model is instructed not to invent anything; this is the deterministic
safety net. Any issue keeps the resume out of auto-apply until you review it.
"""
from __future__ import annotations

import re
from datetime import date

from .resume import TailoredResume, _parse_ym

NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")


def _norm_num(token: str) -> str:
    if re.fullmatch(r"\d{1,3}(,\d{3})+", token):  # 1,000 -> 1000
        token = token.replace(",", "")
    token = token.replace(",", ".")  # German decimal comma
    if "." in token:
        token = token.rstrip("0").rstrip(".")
    return token


def _numbers(text: str) -> set[str]:
    return {_norm_num(t) for t in NUM_RE.findall(text or "")}


def _flatten(obj) -> str:
    if isinstance(obj, dict):
        return " ".join(f"{k} {_flatten(v)}" for k, v in obj.items())
    if isinstance(obj, list):
        return " ".join(_flatten(v) for v in obj)
    return str(obj)


def _key(s: str) -> str:
    s = re.sub(r"\(.*?\)", "", s.lower())
    return re.sub(r"[^a-z0-9+#]", "", s)


def _years_of_experience(profile: dict) -> int:
    months = 0
    today = (date.today().year, date.today().month)
    for e in profile.get("experience", []):
        start = _parse_ym(e.get("start"))
        end = _parse_ym(e.get("end")) or today
        if start:
            months += (end[0] - start[0]) * 12 + end[1] - start[1] + 1
    return months // 12


def _profile_ids(profile: dict) -> set[str]:
    ids = set()
    for section in ("experience", "projects", "education", "other_activities"):
        for item in profile.get(section, []) or []:
            if item.get("id"):
                ids.add(item["id"])
            for a in item.get("achievements", []) or []:
                if isinstance(a, dict) and a.get("id"):
                    ids.add(a["id"])
    return ids


def verify(resume: TailoredResume, profile: dict) -> list[str]:
    issues: list[str] = []
    profile_text = _flatten({k: v for k, v in profile.items() if k != "application_answers"})
    allowed_numbers = _numbers(profile_text)
    allowed_numbers |= {str(n) for n in range(0, _years_of_experience(profile) + 1)}

    # 1. Titles and employers must match the profile.
    roles = {e.get("id"): e for e in profile.get("experience", [])}
    for role in resume.experience:
        src = roles.get(role.profile_id)
        if src is None:
            issues.append(f"Experience entry '{role.company}' has unknown profile_id '{role.profile_id}'")
            continue
        allowed_titles = {src.get("title", "").strip().lower(), str(src.get("display_title", "")).strip().lower()}
        if role.title.strip().lower() not in allowed_titles:
            issues.append(f"Title '{role.title}' at {role.company} does not match profile title '{src.get('title')}'")
        if _key(role.company) != _key(src.get("company", "")):
            issues.append(f"Employer name '{role.company}' differs from profile '{src.get('company')}'")

    # 2. Every number in the text must come from the profile.
    texts = [resume.summary, resume.cover_letter]
    for block in [*resume.experience, *resume.projects]:
        texts.extend(b.text for b in block.bullets)
    for text in texts:
        for n in _numbers(text) - allowed_numbers:
            issues.append(f"Number '{n}' is not in your profile: \"{text[:90]}\"")

    # 3. Skills must exist in the profile, at the stated level.
    known, familiar = {}, set()
    for s in profile.get("skills", []):
        s = s if isinstance(s, dict) else {"name": str(s)}
        for name in [s["name"], *s.get("aliases", [])]:
            known[_key(name)] = s
        if s.get("level") == "familiar":
            familiar.add(_key(s["name"]))
    for group in resume.skills:
        is_familiar_group = "familiar" in group.category.lower()
        for item in group.items:
            k = _key(item)
            if k not in known:
                issues.append(f"Skill '{item}' is not in your profile skills")
            elif _key(known[k]["name"]) in familiar and not is_familiar_group and "familiar" not in item.lower():
                issues.append(f"Skill '{item}' is 'familiar' in your profile but listed as proficient")

    # 4. Bullets must cite real profile entries.
    ids = _profile_ids(profile)
    for block in [*resume.experience, *resume.projects]:
        for b in block.bullets:
            unknown = [i for i in b.source_ids if i not in ids]
            if not b.source_ids or unknown:
                issues.append(f"Bullet has no valid source in the profile: \"{b.text[:90]}\"")
    return issues
