"""`python -m jobbot check`: verifies setup without applying or sending anything."""
from __future__ import annotations

import os
import re

import requests

from . import sources

OK, WARN, FAIL = "  OK  ", " WARN ", " FAIL "
DATE_RE = re.compile(r"^\d{4}(-\d{2})?$")


def _todos(obj, path="") -> list[str]:
    if isinstance(obj, dict):
        return [p for k, v in obj.items() for p in _todos(v, f"{path}.{k}" if path else k)]
    if isinstance(obj, list):
        return [p for i, v in enumerate(obj) for p in _todos(v, f"{path}[{i}]")]
    return [path] if isinstance(obj, str) and "todo" in obj.lower() else []


def check_profile(profile: dict) -> list[tuple[str, str]]:
    out = []
    personal = profile.get("personal") or {}
    for key in ("name", "email", "phone"):
        if not personal.get(key):
            out.append((FAIL, f"profile: personal.{key} is empty"))
    todos = _todos(profile)
    if todos:
        shown = ", ".join(todos[:8]) + (f" (+{len(todos) - 8} more)" if len(todos) > 8 else "")
        out.append((WARN, f"profile: {len(todos)} TODO placeholders left: {shown}"))

    ids: dict[str, int] = {}
    for section in ("experience", "projects", "education", "other_activities"):
        for i, item in enumerate(profile.get(section) or []):
            where = f"{section}[{i}]"
            if not item.get("id"):
                out.append((FAIL, f"profile: {where} has no id"))
            for key in ("start", "end"):
                value = str(item.get(key, "")).strip()
                if value and value != "present" and not DATE_RE.match(value):
                    out.append((FAIL, f"profile: {where}.{key} = '{value}' (use \"YYYY-MM\" or present)"))
            for x in [item, *(item.get("achievements") or [])]:
                if isinstance(x, dict) and x.get("id"):
                    ids[x["id"]] = ids.get(x["id"], 0) + 1
            if section == "experience" and not item.get("title"):
                out.append((FAIL, f"profile: {where} has no title"))
    for dup in [k for k, n in ids.items() if n > 1]:
        out.append((FAIL, f"profile: id '{dup}' is used more than once"))

    for s in profile.get("skills") or []:
        if isinstance(s, dict) and s.get("level") not in ("advanced", "working", "familiar"):
            out.append((WARN, f"profile: skill '{s.get('name')}' level should be advanced, working or familiar"))
    if not profile.get("skills"):
        out.append((FAIL, "profile: no skills listed"))
    if not out:
        out.append((OK, f"profile: {len(profile.get('experience') or [])} jobs, "
                        f"{len(profile.get('projects') or [])} projects, {len(profile.get('skills') or [])} skills"))
    return out


def check_claude(cfg: dict) -> list[tuple[str, str]]:
    if cfg.get("enabled", True) is False:
        return [(WARN, "claude: disabled in config (resumes can't be written)")]
    import anthropic

    model = cfg.get("model", "claude-opus-5-5")
    try:
        anthropic.Anthropic().models.retrieve(model)
    except anthropic.AuthenticationError:
        return [(FAIL, "claude: API key rejected - check ANTHROPIC_API_KEY")]
    except anthropic.NotFoundError:
        return [(FAIL, f"claude: model '{model}' not available to this key")]
    except anthropic.APIConnectionError as e:
        return [(FAIL, f"claude: can't reach the API ({e})")]
    except Exception as e:  # a missing key raises before any request is sent
        if "authentication" in str(e).lower():
            return [(FAIL, "claude: ANTHROPIC_API_KEY is not set - run `source .env` in this terminal")]
        return [(FAIL, f"claude: {e}")]
    return [(OK, f"claude: key works, model {model} available")]


def check_sources(search: dict) -> list[tuple[str, str]]:
    out = []
    srcs = search.get("sources") or {}
    fetchers = {"greenhouse": sources.greenhouse, "lever": sources.lever, "ashby": sources.ashby}
    for kind, fn in fetchers.items():
        for slug, name in sources._boards(srcs.get(kind)):
            try:
                jobs = fn(slug, name)
                out.append((OK if jobs else WARN, f"{kind}:{slug}: {len(jobs)} open postings"))
            except requests.HTTPError as e:
                hint = " - slug probably wrong" if e.response is not None and e.response.status_code == 404 else ""
                out.append((FAIL, f"{kind}:{slug}: HTTP error {e}{hint}"))
            except requests.RequestException as e:
                out.append((FAIL, f"{kind}:{slug}: {e}"))
    ba = srcs.get("arbeitsagentur") or {}
    if ba.get("enabled"):
        out.extend(_check_arbeitsagentur(search, ba))
    if not out:
        out.append((FAIL, "sources: none configured - add boards or enable arbeitsagentur in config.yaml"))
    return out


def _check_arbeitsagentur(search: dict, ba: dict) -> list[tuple[str, str]]:
    where, radius, days = ba.get("where", ""), ba.get("radius_km", 50), search.get("max_age_days", 21)
    out, found = [], None
    try:
        for keyword in search.get("keywords") or []:
            jobs = sources.arbeitsagentur(keyword, where, radius, days, size=5, fetch_details=False)
            out.append((OK if jobs else WARN, f"arbeitsagentur: '{keyword}' -> {len(jobs)} postings"))
            found = found or (jobs[0] if jobs else None)
        if found is None:
            # Tell "API works, keywords too narrow" apart from "API returns nothing".
            probe = sources.arbeitsagentur("Ingenieur", where, radius, days, size=5, fetch_details=False)
            if probe:
                out.append((WARN, f"arbeitsagentur: API works ('Ingenieur' -> {len(probe)} postings) but none of "
                                  "your keywords match - add German terms such as Robotik, Mechatronik, "
                                  "Automatisierungstechnik, Konstrukteur"))
            else:
                out.append((FAIL, "arbeitsagentur: even 'Ingenieur' returns nothing - the API may have changed; "
                                  "run with -v and share the output"))
            return out
        details = sources._ba_details(found["id"].split(":", 2)[2])
        text = details.get("stellenangebotsBeschreibung") or details.get("stellenbeschreibung") or ""
        out.append((OK if len(text) > 300 else WARN,
                    "arbeitsagentur: job details " + ("available" if len(text) > 300 else
                                                      "unavailable; scoring will rely on titles")))
    except requests.RequestException as e:
        out.append((FAIL, f"arbeitsagentur: {e}"))
    return out


def check_mail(cfg: dict) -> list[tuple[str, str]]:
    if not cfg.get("username") or not cfg.get("password"):
        return [(WARN, "mail: JOBBOT_MAIL_USER / JOBBOT_MAIL_PASSWORD not set (mail sync will be skipped)")]
    import imaplib

    try:
        imap = imaplib.IMAP4_SSL(cfg["imap_host"], int(cfg.get("imap_port", 993)))
        imap.login(cfg["username"], cfg["password"])
        typ, data = imap.select(cfg.get("inbox", "INBOX"), readonly=True)
        imap.logout()
    except imaplib.IMAP4.error as e:
        return [(FAIL, f"mail: login failed ({e}) - use an app-specific password")]
    except OSError as e:
        return [(FAIL, f"mail: can't connect to {cfg.get('imap_host')} ({e})")]
    if typ != "OK":
        return [(FAIL, f"mail: folder '{cfg.get('inbox', 'INBOX')}' not found")]
    return [(OK, f"mail: logged in, {data[0].decode()} messages in {cfg.get('inbox', 'INBOX')}")]


def check_browser() -> list[tuple[str, str]]:
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=os.environ.get("JOBBOT_CHROMIUM") or None)
            browser.close()
    except Exception as e:
        first = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
        return [(FAIL, f"browser: {first} - run `playwright install chromium`")]
    return [(OK, "browser: Chromium starts")]


def run(settings) -> int:
    print("Checking JobBot setup (nothing is applied or sent)...\n")
    results = []
    sections = [
        ("Profile", lambda: check_profile(settings.profile)),
        ("Claude API", lambda: check_claude(settings.section("llm"))),
        ("Browser", check_browser),
        ("Job sources", lambda: check_sources(settings.section("search"))),
        ("Mail", lambda: check_mail(settings.section("mail"))),
    ]
    for title, fn in sections:
        print(title)
        for status, msg in fn():
            print(f"[{status}] {msg}")
            results.append(status)
        print()
    fails, warns = results.count(FAIL), results.count(WARN)
    print(f"{fails} problem(s), {warns} warning(s)." + ("" if fails else " Ready for `python -m jobbot run`."))
    return 1 if fails else 0
