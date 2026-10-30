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


def _first(d: dict, *keys):
    for k in keys:
        if d.get(k):
            return d[k]
    return None


def _ba_fields(s: dict) -> dict:
    """Normalize one search hit. The v6 API ("ergebnisliste") renamed most fields
    of the older format ("stellenangebote"); both are accepted."""
    employer = _first(s, "arbeitgeber", "firma", "arbeitgeberName", "unternehmen", "arbeitgeberBezeichnung")
    if isinstance(employer, dict):
        employer = _first(employer, "name", "firma", "bezeichnung")
    locations = s.get("stellenlokationen") or []
    place = s.get("arbeitsort") or (locations[0].get("adresse") if locations else None) or {}
    posted = _first(s, "aktuelleVeroeffentlichungsdatum", "datumErsteVeroeffentlichung",
                    "veroeffentlichungsdatum", "veroeffentlichtAm")
    if not posted:  # any other date field named after publication
        posted = next((v for k, v in s.items() if "veroeffentlich" in k.lower() and isinstance(v, str)), "")
    return {
        "refnr": _first(s, "refnr", "referenznummer", "hashId"),
        "title": _first(s, "stellenangebotsTitel", "titel", "beruf") or "",
        "employer": employer or "?",
        "city": place.get("ort", ""),
        "posted": posted,
        "external_url": _first(s, "externeUrl", "externeURL", "allianzpartnerUrl"),
    }


def arbeitsagentur(what: str, where: str, radius_km: int = 50, days: int = 21,
                   size: int = 50, fetch_details: bool = True) -> list[dict]:
    """Bundesagentur für Arbeit job search (Germany's national job board).

    Unofficial API, documented at github.com/bundesAPI/jobsuche-api.
    """
    params = {"was": what, "wo": where, "umkreis": radius_km,
              "veroeffentlichtseit": min(days, 100), "size": size, "angebotsart": 1}
    r = requests.get(f"{BA_BASE}/pc/v6/jobs", params=params, headers=BA_HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json() if r.text.strip() else {}
    hits = data.get("ergebnisliste") or data.get("stellenangebote") or []
    if not hits:
        log.debug("arbeitsagentur %s: no results; response keys %s", r.url, list(data))
    jobs = []
    for hit in hits:
        f = _ba_fields(hit)
        if not f["refnr"]:
            log.debug("arbeitsagentur: hit without reference number, keys %s", list(hit))
            continue
        url = f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{f['refnr']}"
        title, description, apply_url = f["title"], "", f["external_url"] or url
        if fetch_details:
            details = _ba_details(f["refnr"])
            title = details.get("stellenangebotsTitel") or details.get("titel") or title
            description = details.get("stellenangebotsBeschreibung") or details.get("stellenbeschreibung") or ""
            apply_url = details.get("allianzpartnerUrl") or details.get("externeUrl") or apply_url
        jobs.append(_job("arbeitsagentur", f["employer"], f["refnr"], title, f["city"],
                         url, apply_url, description, f["posted"]))
    return jobs


def _ba_details(refnr: str) -> dict:
    encoded = quote(base64.b64encode(refnr.encode()).decode(), safe="=")
    for path in (f"/pc/v6/jobdetails/{encoded}", f"/pc/v4/jobdetails/{encoded}"):
        try:
            r = requests.get(BA_BASE + path, headers=BA_HEADERS, timeout=TIMEOUT)
            if r.ok and r.text.strip():
                return r.json()
            log.debug("BA details %s -> HTTP %s", path, r.status_code)
        except (requests.RequestException, ValueError) as e:
            log.debug("BA details %s failed: %s", path, e)
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
