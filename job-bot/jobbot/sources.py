"""Job sources. Each fetcher returns a list of normalized job dicts.

Only public, documented job-board endpoints are used (no scraping of sites
whose terms forbid automation, e.g. LinkedIn/Indeed).
"""
from __future__ import annotations

import base64
import html
import logging
import re
from datetime import datetime, timezone
from urllib.parse import quote

import requests

log = logging.getLogger(__name__)
TIMEOUT = 30
UA = {"User-Agent": "JobBot/1.0 (personal job search)"}


def html_to_text(raw: str) -> str:
    text = html.unescape(raw or "")
    text = re.sub(r"<(br|/p|/li|/h\d)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _job(source, company, ext_id, title, location, url, apply_url, description, posted_at):
    return {
        "id": f"{source}:{company}:{ext_id}",
        "source": source,
        "company": company,
        "title": (title or "").strip(),
        "location": (location or "").strip(),
        "url": url,
        "apply_url": apply_url or url,
        "description": description or "",
        "posted_at": posted_at or "",
    }


def _ms_to_iso(ms) -> str:
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def greenhouse(board: str, display_name: str | None = None) -> list[dict]:
    r = requests.get(
        f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs",
        params={"content": "true"}, headers=UA, timeout=TIMEOUT,
    )
    r.raise_for_status()
    company = display_name or board
    return [
        _job("greenhouse", company, j["id"], j.get("title"),
             (j.get("location") or {}).get("name"), j.get("absolute_url"), j.get("absolute_url"),
             html_to_text(j.get("content", "")), j.get("updated_at"))
        for j in r.json().get("jobs", [])
    ]


def lever(site: str, display_name: str | None = None) -> list[dict]:
    """`site` is the Lever company slug; prefix with `eu:` for EU-hosted boards."""
    host = "api.lever.co"
    if site.startswith("eu:"):
        host, site = "api.eu.lever.co", site[3:]
    r = requests.get(f"https://{host}/v0/postings/{site}", params={"mode": "json"},
                     headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    jobs = []
    for p in r.json():
        parts = [p.get("descriptionPlain", "")]
        for lst in p.get("lists", []):
            parts.append(f"{lst.get('text', '')}\n{html_to_text(lst.get('content', ''))}")
        parts.append(p.get("additionalPlain", ""))
        jobs.append(_job("lever", display_name or site, p["id"], p.get("text"),
                         (p.get("categories") or {}).get("location"), p.get("hostedUrl"),
                         p.get("applyUrl"), "\n\n".join(x for x in parts if x),
                         _ms_to_iso(p.get("createdAt"))))
    return jobs


def ashby(board: str, display_name: str | None = None) -> list[dict]:
    r = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{board}",
                     headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    jobs = []
    for j in r.json().get("jobs", []):
        if j.get("isListed") is False:
            continue
        loc = j.get("location") or ""
        if j.get("isRemote"):
            loc = f"{loc} (Remote)".strip()
        jobs.append(_job("ashby", display_name or board, j["id"], j.get("title"), loc,
                         j.get("jobUrl"), j.get("applyUrl"),
                         j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", "")),
                         j.get("publishedAt")))
    return jobs


BA_BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service"
BA_HEADERS = {**UA, "X-API-Key": "jobboerse-jobsuche"}  # public key documented by the BA


def arbeitsagentur(what: str, where: str, radius_km: int = 50, days: int = 21,
                   size: int = 50, fetch_details: bool = True) -> list[dict]:
    """Bundesagentur für Arbeit job search (Germany's national job board).

    Unofficial but stable API, documented at github.com/bundesAPI/jobsuche-api.
    """
    params = {"was": what, "wo": where, "umkreis": radius_km,
              "veroeffentlichtseit": min(days, 100), "size": size, "angebotsart": 1}
    r = None
    for version in ("v6", "v4"):  # v6 is the documented path; v4 still answers in some setups
        r = requests.get(f"{BA_BASE}/pc/{version}/jobs", params=params, headers=BA_HEADERS, timeout=TIMEOUT)
        if r.status_code != 404:
            break
    r.raise_for_status()
    data = r.json()
    if "stellenangebote" not in data:
        log.debug("arbeitsagentur %s: no results; response keys %s, maxErgebnisse=%s",
                  r.url, list(data), data.get("maxErgebnisse"))
    jobs = []
    for s in data.get("stellenangebote", []):
        refnr = s.get("refnr") or s.get("referenznummer")
        if not refnr:
            continue
        place = s.get("arbeitsort") or {}
        url = f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{refnr}"
        # Search results carry the occupation in `beruf`; the posting's own title
        # is only in the details response.
        title = s.get("titel") or s.get("beruf", "")
        description = ""
        apply_url = s.get("externeUrl") or url
        if fetch_details:
            details = _ba_details(refnr)
            title = details.get("stellenangebotsTitel") or details.get("titel") or title
            description = details.get("stellenangebotsBeschreibung") or details.get("stellenbeschreibung") or ""
            apply_url = details.get("allianzpartnerUrl") or details.get("externeUrl") or apply_url
        jobs.append(_job("arbeitsagentur", s.get("arbeitgeber", "?"), refnr, title,
                         place.get("ort", ""), url, apply_url, description,
                         s.get("aktuelleVeroeffentlichungsdatum")))
    return jobs


def _ba_details(refnr: str) -> dict:
    encoded = quote(base64.b64encode(refnr.encode()).decode(), safe="=")
    try:
        r = requests.get(f"{BA_BASE}/pc/v4/jobdetails/{encoded}", headers=BA_HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as e:
        log.debug("BA details unavailable for %s: %s", refnr, e)
        return {}


def _boards(entries) -> list[tuple[str, str | None]]:
    """Board lists accept either `slug` or `{slug: ..., name: ...}` entries."""
    out = []
    for e in entries or []:
        if isinstance(e, dict):
            out.append((e["slug"], e.get("name")))
        else:
            out.append((str(e), None))
    return out


def fetch_all(search_cfg: dict) -> list[dict]:
    sources = search_cfg.get("sources", {})
    results: list[dict] = []

    def run(label, fn, *args, **kwargs):
        try:
            found = fn(*args, **kwargs)
            log.info("%-32s %3d postings", label, len(found))
            results.extend(found)
        except requests.RequestException as e:
            log.warning("%s failed: %s", label, e)

    for slug, name in _boards(sources.get("greenhouse")):
        run(f"greenhouse:{slug}", greenhouse, slug, name)
    for slug, name in _boards(sources.get("lever")):
        run(f"lever:{slug}", lever, slug, name)
    for slug, name in _boards(sources.get("ashby")):
        run(f"ashby:{slug}", ashby, slug, name)

    ba = sources.get("arbeitsagentur") or {}
    if ba.get("enabled"):
        for keyword in search_cfg.get("keywords", []):
            run(f"arbeitsagentur:{keyword}", arbeitsagentur, keyword, ba.get("where", ""),
                ba.get("radius_km", 50), search_cfg.get("max_age_days", 21),
                ba.get("size", 50), ba.get("fetch_details", True))

    unique = {j["id"]: j for j in results}
    return list(unique.values())
