"""Fills and submits application forms with Playwright.

Works on Greenhouse, Lever and Ashby hosted forms, and best-effort on other
simple HTML forms. Two modes:
  review - opens a visible browser, fills everything it can, and waits for you
           to check the form and confirm before it clicks Submit (default).
  auto   - submits on its own, but only when every required field was filled
           from your profile, the resume passed fact-checking and no CAPTCHA
           is shown. Anything else is left as `needs_manual`.
CAPTCHAs are never bypassed; those applications are handed to you.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- answering

YES = ("yes", "ja", "true", "y")
NO = ("no", "nein", "false", "n")
DECLINE = ("decline", "prefer not", "don't wish", "do not wish", "not to say", "keine angabe", "choose not")


def _first_last(personal: dict) -> tuple[str, str]:
    first = personal.get("first_name")
    last = personal.get("last_name")
    if first and last:
        return first, last
    parts = personal.get("name", "").split()
    return (parts[0] if parts else ""), " ".join(parts[1:])


def _current(profile: dict, key: str) -> str:
    exp = profile.get("experience") or []
    if not exp:
        return ""
    latest = max(exp, key=lambda e: str(e.get("start", "")))
    return latest.get(key, "")


def answer_for(label: str, profile: dict) -> str | bool | None:
    """Map a form question to an answer from the profile, or None if unknown."""
    text = re.sub(r"\s+", " ", (label or "").lower()).strip(" *:")
    p = profile.get("personal", {})
    a = profile.get("application_answers", {}) or {}

    for rule in a.get("custom", []) or []:
        if re.search(rule["match"], text, flags=re.I):
            return rule["answer"]

    first, last = _first_last(p)
    rules = [
        (r"first name|given name|vorname|preferred name", first),
        (r"last name|surname|family name|nachname", last),
        (r"^(full )?name$|^your name|vollständiger name", p.get("name")),
        (r"e-?mail", p.get("email")),
        (r"phone|mobile|telefon|handy", p.get("phone")),
        (r"linkedin", p.get("linkedin")),
        (r"github", p.get("github")),
        (r"website|portfolio|personal (site|url)", p.get("website") or p.get("github")),
        (r"sponsor|visa", a.get("requires_sponsorship")),
        (r"authori[sz]ed to work|right to work|work permit|arbeitserlaubnis|legally (allowed|eligible)", a.get("work_authorization")),
        (r"relocat|umzug", a.get("willing_to_relocate")),
        (r"salary|compensation|gehalt|pay expectation", a.get("salary_expectation")),
        (r"notice period|kündigungsfrist", a.get("notice_period")),
        (r"start date|earliest|available to start|eintrittsdatum|verfügbar", a.get("earliest_start")),
        (r"years of (professional |relevant )?experience|berufserfahrung", a.get("years_experience")),
        (r"how did you (hear|find)|where did you (hear|find)|referral source|aufmerksam geworden", a.get("how_did_you_hear")),
        (r"german", a.get("german_level")),
        (r"english", a.get("english_level")),
        (r"gender|race|ethnic|veteran|disabilit|pronoun|geschlecht|hispanic|latino",
         a.get("eeo_answer", "Decline to self-identify")),
        (r"current (company|employer)|arbeitgeber", _current(profile, "company")),
        (r"current (title|role|position)", _current(profile, "display_title") or _current(profile, "title")),
        (r"location|city|where are you based|wohnort|address", p.get("location")),
    ]
    for pattern, value in rules:
        if re.search(pattern, text) and value not in (None, ""):
            return value
    return None


def choose_option(answer, options: list[str]) -> str | None:
    """Pick the option text best matching an answer (handles yes/no and decline)."""
    opts = [(o, o.strip().lower()) for o in options if o and o.strip()]
    if isinstance(answer, bool):
        words = YES if answer else NO
        for o, low in opts:
            if low in words or low.split()[0].strip(",.") in words:
                return o
        return None
    ans = str(answer).strip().lower()
    for o, low in opts:
        if low == ans:
            return o
    if any(d in ans for d in DECLINE):
        for o, low in opts:
            if any(d in low for d in DECLINE):
                return o
    if ans in YES or ans in NO:
        return choose_option(ans in YES, options)
    for o, low in opts:
        if ans and (ans in low or low in ans):
            return o
    return None


def as_text(answer) -> str:
    if isinstance(answer, bool):
        return "Yes" if answer else "No"
    return str(answer)


# ---------------------------------------------------------------- browser

FIELDS_JS = r"""
() => {
  const visible = el => {
    const s = getComputedStyle(el), r = el.getBoundingClientRect();
    return s.display !== 'none' && s.visibility !== 'hidden' && (r.width > 0 || r.height > 0);
  };
  const text = el => (el ? (el.innerText || el.textContent || '') : '').replace(/\s+/g, ' ').trim();
  const labelOf = el => {
    let t = '';
    if (el.id) t = text(document.querySelector(`label[for="${CSS.escape(el.id)}"]`));
    if (!t && el.closest('label')) t = text(el.closest('label'));
    if (!t && el.getAttribute('aria-labelledby'))
      t = el.getAttribute('aria-labelledby').split(' ').map(id => text(document.getElementById(id))).join(' ');
    return t || el.getAttribute('aria-label') || el.placeholder || el.name || '';
  };
  const questionOf = el => {
    const fs = el.closest('fieldset');
    if (fs && fs.querySelector('legend')) return text(fs.querySelector('legend'));
    const g = el.closest('[role=radiogroup],[role=group]');
    if (g) return g.getAttribute('aria-label') || text(g).split('\n')[0];
    return el.name || '';
  };
  const out = [];
  let i = 0;
  document.querySelectorAll('input, textarea, select').forEach(el => {
    const type = (el.tagName === 'INPUT' ? (el.type || 'text') : el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'image', 'reset'].includes(type)) return;
    if (type !== 'file' && !visible(el)) return;
    if (el.disabled || el.readOnly) return;
    const idx = String(i++);
    el.setAttribute('data-jobbot-idx', idx);
    const label = labelOf(el);
    const required = el.required || el.getAttribute('aria-required') === 'true' || /\*\s*$/.test(label);
    out.push({
      idx, type, label, required,
      name: el.name || '',
      role: el.getAttribute('role') || '',
      question: (type === 'radio' || type === 'checkbox') ? questionOf(el) : label,
      options: el.tagName === 'SELECT' ? [...el.options].map(o => o.text.trim()) : [],
    });
  });
  return out;
}
"""

SUBMIT_SELECTORS = [
    "button[type=submit]", "input[type=submit]",
    "button:has-text('Submit application')", "button:has-text('Submit Application')",
    "button:has-text('Submit')", "button:has-text('Bewerbung absenden')", "button:has-text('Absenden')",
]
CONFIRM_RE = re.compile(
    r"thank you for (applying|your application|your interest)|application (has been |was )?(received|submitted)"
    r"|we('ve| have) received your application|vielen dank für ihre bewerbung|bewerbung (wurde )?(erfolgreich )?(eingegangen|übermittelt|gesendet)",
    re.I,
)
CAPTCHA_SELECTORS = ["iframe[src*='recaptcha/api2/anchor']", "iframe[src*='hcaptcha.com']",
                     "iframe[src*='challenges.cloudflare.com']"]
CONSENT_RE = re.compile(r"privacy|consent|agree|datenschutz|einwillig|gdpr|terms", re.I)
COVER_RE = re.compile(r"cover letter|anschreiben|motivation", re.I)
RESUME_RE = re.compile(r"resume|résumé|\bcv\b|lebenslauf", re.I)


@dataclass
class FillReport:
    filled: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    captcha: bool = False


def application_url(job) -> str:
    url = job["apply_url"] or job["url"]
    if job["source"] == "lever" and not url.rstrip("/").endswith("/apply"):
        url = url.rstrip("/") + "/apply"
    if job["source"] == "ashby" and "/application" not in url:
        url = url.rstrip("/") + "/application"
    return url


def _open_form(page) -> None:
    """Some boards show the description first; click through to the form."""
    if page.locator("input[type=file]").count():
        return
    for sel in ["a:has-text('Apply for this job')", "button:has-text('Apply for this job')",
                "a:has-text('Apply now')", "button:has-text('Apply now')",
                "button:has-text('Apply')", "a:has-text('Apply')", "a:has-text('Jetzt bewerben')"]:
        loc = page.locator(sel).first
        if loc.count() and loc.is_visible():
            loc.click()
            page.wait_for_timeout(2500)
            return


def fill_form(page, profile: dict, files: dict, cfg: dict, free_text=None) -> FillReport:
    """Fill every recognised field. `free_text(question)` may answer open questions."""
    report = FillReport()
    fields = page.evaluate(FIELDS_JS)
    radios: dict[str, list[dict]] = {}

    for f in fields:
        loc = page.locator(f'[data-jobbot-idx="{f["idx"]}"]')
        label = f["label"]
        try:
            if f["type"] == "file":
                target = None
                if COVER_RE.search(label + " " + f["name"]):
                    target = files.get("cover_letter")
                elif RESUME_RE.search(label + " " + f["name"]) or sum(x["type"] == "file" for x in fields) == 1:
                    target = files.get("resume")
                if target:
                    loc.set_input_files(str(target))
                    report.filled.append(f"{label or 'file'} <- {Path(target).name}")
                elif f["required"]:
                    report.unresolved.append(label or "file upload")
                continue

            if f["type"] == "radio":
                radios.setdefault(f["name"] or f["question"], []).append(f)
                continue

            if f["type"] == "checkbox":
                if CONSENT_RE.search(label) and cfg.get("accept_privacy_consent"):
                    loc.check()
                    report.filled.append(f"[x] {label[:60]}")
                elif f["required"]:
                    report.unresolved.append(label)
                continue

            answer = answer_for(label, profile)
            if answer is None and f["type"] == "textarea" and COVER_RE.search(label) and files.get("cover_letter_text"):
                answer = files["cover_letter_text"]
            if answer is None and f["type"] in ("textarea", "text") and f["required"] and free_text:
                answer = free_text(label)

            if answer is None:
                if f["required"]:
                    report.unresolved.append(label)
                continue

            if f["type"] == "select":
                option = choose_option(answer, f["options"])
                if option:
                    loc.select_option(label=option)
                    report.filled.append(f"{label} <- {option}")
                elif f["required"]:
                    report.unresolved.append(f"{label} (no option matches '{as_text(answer)}')")
            elif f["role"] == "combobox":  # React-style dropdowns
                loc.fill(as_text(answer))
                page.wait_for_timeout(400)
                loc.press("Enter")
                report.filled.append(f"{label} <- {as_text(answer)}")
            else:
                loc.fill(as_text(answer))
                report.filled.append(f"{label} <- {as_text(answer)[:60]}")
        except Exception as e:  # one odd field should not abort the whole form
            log.debug("field %s failed: %s", label, e)
            if f["required"]:
                report.unresolved.append(label)

    for group in radios.values():
        question = group[0]["question"]
        answer = answer_for(question, profile)
        option = choose_option(answer, [g["label"] for g in group]) if answer is not None else None
        if option:
            chosen = next(g for g in group if g["label"] == option)
            page.locator(f'[data-jobbot-idx="{chosen["idx"]}"]').check()
            report.filled.append(f"{question} <- {option}")
        elif any(g["required"] for g in group):
            report.unresolved.append(question)

    report.captcha = any(page.locator(s).count() for s in CAPTCHA_SELECTORS)
    return report


def submit(page, timeout_ms: int = 20000) -> bool:
    """Click submit and wait for a confirmation message. Returns True if confirmed."""
    for sel in SUBMIT_SELECTORS:
        btn = page.locator(sel).first
        if btn.count() and btn.is_visible():
            btn.click()
            break
    else:
        return False
    waited = 0
    while waited < timeout_ms:
        page.wait_for_timeout(1000)
        waited += 1000
        body = page.locator("body").inner_text(timeout=5000)
        if CONFIRM_RE.search(body) or re.search(r"confirm|thank", page.url, re.I):
            return True
    return False


def launch(headless: bool):
    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    # JOBBOT_CHROMIUM: optional path to a Chrome/Chromium binary to use instead of
    # the one installed by `playwright install chromium`.
    browser = pw.chromium.launch(headless=headless,
                                 executable_path=os.environ.get("JOBBOT_CHROMIUM") or None)
    return pw, browser
