"""Tailors a resume (and optional cover letter) to one job description."""
from __future__ import annotations

from datetime import date

import yaml
from pydantic import BaseModel, Field

from .llm import LLM

# The six writing rules. Everything the model writes must trace back to a fact
# in profile.yaml; verify.py checks titles, numbers and skills afterwards.
SYSTEM_PROMPT = """You are an expert resume writer and ATS specialist. You write a
targeted, one-to-two page resume for ONE specific job, using ONLY the facts in the
candidate's profile. The resume is a marketing document: select, order and phrase
facts for maximum relevance and impact, but never state anything the candidate could
not defend in an interview, a background check or a reference call.

Follow these rules:

1. STRATEGIC SCOPE & SUMMARY
   - Headline = the target job's title (or the closest title the profile supports).
   - Summary: 3-4 punchy sentences: target title, years of relevant experience
     (computed from the profile, rounded down), the single strongest real achievement
     with its number, and the 2-3 skills this job cares about most.
   - Include only the last 10-15 years. Drop or compress older/irrelevant roles.

2. TITLES
   - Use the role's `display_title` if the profile provides one, otherwise its `title`.
     Do not invent, upgrade or re-rank titles. You may reorder emphasis in bullets
     to show higher-level scope that the profile documents.

3. IMPACT-DRIVEN BULLETS (X-Y-Z)
   - Write bullets as "Accomplished [X] as measured by [Y], by doing [Z]". Start with
     a strong, varied active verb. Keep each bullet to one or two lines.
   - Use every real metric the profile provides; aim for ~80% of bullets with hard
     data. NEVER invent, estimate or round up numbers. If no metric exists, write a
     strong qualitative bullet and add a review_note asking the candidate for the
     real number.
   - When an achievement has scope "team", credit the candidate's own contribution
     and name the team result accurately ("Co-developed ...", "Contributed X to a
     6-person team that ...", "Led ..." only if the profile says they led it).

4. CONTINUITY & DATES
   - Format dates exactly as the `date_style` instruction says.
   - Fill employment gaps ONLY with real items from the profile (projects, studies,
     freelance work, certifications, career breaks). Never create employers or
     consulting blocks that are not in the profile.

5. ATS OPTIMIZATION
   - Mirror the job description's exact wording for every tool, method and
     competency the candidate genuinely has (e.g. the JD says "ROS2" and the profile
     says "ROS 2" -> use the JD's spelling).
   - Put a skills section near the top, grouped by category, ordered by relevance to
     the job. Express each skill at the level the profile states: "advanced" and
     "working" skills are listed plainly; "familiar" skills go in a group labelled
     "Familiar with" or are described as such. Never list a skill the profile lacks.
   - List job requirements the profile does not support in keywords_missing.

6. CONTEXT & STYLE
   - Under each employer, one line of context taken from the profile's
     `company_context` (size, scale, industry). Do not exaggerate it.
   - Active, powerful verbs; short, punchy sentences; no first person; no fluff.

For every bullet, list the profile ids (achievement/project ids) it is based on in
source_ids. If a cover letter is requested, write 180-250 words, specific to the
company and role, same truthfulness rules; otherwise return an empty string."""


class Bullet(BaseModel):
    text: str
    source_ids: list[str]


class Role(BaseModel):
    profile_id: str = Field(description="id of the experience entry in the profile")
    company: str
    company_context: str
    title: str
    location: str
    dates: str
    bullets: list[Bullet]


class Project(BaseModel):
    profile_id: str
    name: str
    context: str
    dates: str
    bullets: list[Bullet]


class SkillGroup(BaseModel):
    category: str
    items: list[str]


class Education(BaseModel):
    degree: str
    institution: str
    location: str
    dates: str
    details: str


class TailoredResume(BaseModel):
    headline: str
    summary: str
    skills: list[SkillGroup]
    experience: list[Role]
    projects: list[Project]
    education: list[Education]
    certifications: list[str]
    languages: list[str]
    keywords_matched: list[str]
    keywords_missing: list[str]
    review_notes: list[str]
    cover_letter: str


def _parse_ym(value) -> tuple[int, int] | None:
    if value in (None, "", "present"):
        return None
    parts = str(value).split("-")
    return int(parts[0]), int(parts[1]) if len(parts) > 1 else 1


def find_gaps(profile: dict, min_months: int = 3) -> list[str]:
    """Gaps between consecutive dated entries (jobs, projects, education)."""
    spans = []
    for section in ("experience", "projects", "education", "other_activities"):
        for item in profile.get(section, []) or []:
            start = _parse_ym(item.get("start"))
            end = _parse_ym(item.get("end")) or (date.today().year, date.today().month)
            if start:
                spans.append((start, end))
    spans.sort()
    gaps, covered_until = [], None
    for start, end in spans:
        if covered_until:
            months = (start[0] - covered_until[0]) * 12 + start[1] - covered_until[1] - 1
            if months >= min_months:
                gaps.append(
                    f"{months}-month gap {covered_until[0]}-{covered_until[1]:02d} -> "
                    f"{start[0]}-{start[1]:02d}: add any real course, project, freelance "
                    "work or career break from that period to profile.yaml"
                )
        if covered_until is None or end > covered_until:
            covered_until = end
    return gaps


def tailor(llm: LLM, profile: dict, job: dict, resume_cfg: dict) -> TailoredResume:
    date_style = resume_cfg.get("date_style", "year")
    style_text = {
        "year": "Show years only (e.g. '2021 - 2024').",
        "month": "Show month and year (e.g. 'Mar 2021 - Aug 2024').",
    }.get(date_style, date_style)
    profile_for_prompt = {k: v for k, v in profile.items() if k != "application_answers"}
    prompt = f"""<job>
Company: {job['company']}
Title: {job['title']}
Location: {job['location']}

{job['description'][:30000]}
</job>

<candidate_profile>
{yaml.safe_dump(profile_for_prompt, sort_keys=False, allow_unicode=True)}
</candidate_profile>

date_style: {style_text}
cover_letter: {"yes" if resume_cfg.get("cover_letter") else "no"}
max_pages: {resume_cfg.get("max_pages", 2)}

Write the tailored resume for this job."""
    result = llm.parse(SYSTEM_PROMPT, prompt, TailoredResume, effort=resume_cfg.get("effort"))
    result.review_notes.extend(find_gaps(profile, resume_cfg.get("gap_months", 3)))
    return result


class FitScore(BaseModel):
    score: int = Field(description="0-100 fit of the candidate for this job")
    reasons: str
    must_haves_missing: list[str]


SCORE_PROMPT = """You screen jobs for a candidate. Score 0-100 how well the candidate's
real profile fits the job (skills, seniority, domain, language and location
requirements). Be strict about hard requirements such as required years of
experience, mandatory languages, work permits and degrees. Return a short reason."""


def llm_score(llm: LLM, profile: dict, job: dict) -> FitScore:
    slim = {k: profile.get(k) for k in ("target_titles", "skills", "experience", "education", "languages", "preferences")}
    prompt = (f"<job>\n{job['title']} at {job['company']} ({job['location']})\n\n"
              f"{job['description'][:20000]}\n</job>\n\n<candidate>\n"
              f"{yaml.safe_dump(slim, sort_keys=False, allow_unicode=True)}\n</candidate>")
    return llm.parse(SCORE_PROMPT, prompt, FitScore, effort="low")
