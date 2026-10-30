"""Exports the database to an Excel workbook (Applications, Responses, Summary)."""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

log = logging.getLogger(__name__)

STATUS_COLORS = {
    "offer": "63BE7B", "interview": "A9D08E", "assessment": "C6E0B4",
    "confirmation": "DDEBF7", "applied": "BDD7EE", "rejected": "F4B6B6",
    "needs_manual": "FFE699", "resume_needs_review": "FFE699", "resume_ready": "E2EFDA",
    "shortlisted": "F2F2F2", "skipped": "D9D9D9", "filtered_out": "D9D9D9", "withdrawn": "D9D9D9",
}
APP_COLUMNS = [
    ("Company", "company", 22), ("Title", "title", 38), ("Location", "location", 18),
    ("Status", "status", 16), ("Fit score", "score", 9), ("Date found", "found_at", 12),
    ("Date applied", "applied_at", 12), ("Last response", "last_response_type", 14),
    ("Response date", "last_response_at", 12), ("Response subject", "last_response_subject", 40),
    ("Job URL", "url", 40), ("Resume file", "resume_path", 30), ("Source", "source", 13),
    ("Score reason", "score_reason", 50), ("Review notes", "review_notes", 50),
    ("Notes", "notes", 30), ("Job ID", "id", 30),
]
HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(bold=True, color="FFFFFF")


def _short_date(value: str | None) -> str:
    return (value or "")[:10]


def _sheet(ws, headers: list[tuple[str, int]]) -> None:
    for col, (name, width) in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col, value=name)
        cell.fill, cell.font = HEADER_FILL, HEADER_FONT
        ws.column_dimensions[get_column_letter(col)].width = width
    ws.freeze_panes = "A2"


def export(db, path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Applications"
    _sheet(ws, [(h, w) for h, _, w in APP_COLUMNS])
    jobs = [j for j in db.jobs() if j["status"] != "filtered_out"]
    status_col = [k for _, k, _ in APP_COLUMNS].index("status") + 1
    for r, job in enumerate(jobs, start=2):
        for c, (_, key, _) in enumerate(APP_COLUMNS, start=1):
            value = job[key]
            if key.endswith("_at"):
                value = _short_date(value)
            cell = ws.cell(row=r, column=c, value=value)
            cell.alignment = Alignment(vertical="top", wrap_text=key in ("score_reason", "review_notes"))
            if key == "url" and value:
                cell.hyperlink, cell.style = value, "Hyperlink"
        color = STATUS_COLORS.get(job["status"])
        if color:
            ws.cell(row=r, column=status_col).fill = PatternFill("solid", fgColor=color)
    ws.auto_filter.ref = ws.dimensions

    rs = wb.create_sheet("Responses")
    _sheet(rs, [("Received", 12), ("Type", 14), ("Company", 22), ("Job title", 36),
                ("From", 30), ("Subject", 50), ("Snippet", 70)])
    by_id = {j["id"]: j for j in db.jobs()}
    for r, e in enumerate(db.emails(), start=2):
        job = by_id.get(e["job_id"])
        rs.append([_short_date(e["received_at"]), e["classification"],
                   job["company"] if job else "", job["title"] if job else "",
                   e["sender"], e["subject"], e["snippet"]])
        color = STATUS_COLORS.get({"rejection": "rejected"}.get(e["classification"], e["classification"]))
        if color:
            rs.cell(row=r, column=2).fill = PatternFill("solid", fgColor=color)
    rs.auto_filter.ref = rs.dimensions

    ss = wb.create_sheet("Summary")
    _sheet(ss, [("Metric", 28), ("Value", 14)])
    counts = Counter(j["status"] for j in jobs)
    applied_like = {"applied", "confirmation", "rejected", "assessment", "interview", "offer"}
    applied = sum(counts[s] for s in applied_like)
    heard_back = sum(counts[s] for s in ("rejected", "assessment", "interview", "offer"))
    rows = [("Last updated", datetime.now().strftime("%Y-%m-%d %H:%M")),
            ("Jobs tracked", len(jobs)), ("Applications sent", applied),
            ("Heard back (beyond confirmation)", heard_back),
            ("Response rate", f"{100 * heard_back / applied:.0f}%" if applied else "-"),
            ("Interview rate", f"{100 * (counts['interview'] + counts['offer']) / applied:.0f}%" if applied else "-"),
            ("", "")]
    rows += [(f"Status: {s}", n) for s, n in sorted(counts.items(), key=lambda x: -x[1])]
    for row in rows:
        ss.append(list(row))

    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        wb.save(path)
    except PermissionError:  # the file is open in Excel
        alt = path.with_name(f"{path.stem}-{datetime.now():%Y%m%d-%H%M%S}{path.suffix}")
        wb.save(alt)
        log.warning("%s is open in another program; wrote %s instead", path.name, alt.name)
        return alt
    return path
