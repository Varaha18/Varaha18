"""Scans your inbox over IMAP, matches replies to applications, classifies them
and files them into a `JobBot/<Type>` folder (Gmail shows these as labels).

Works with Gmail, iCloud, Outlook and most providers using an app password.
Messages are read with BODY.PEEK so they stay unread. By default they are
copied into the folder; set `mail.move: true` to also remove them from INBOX.
"""
from __future__ import annotations

import email
import imaplib
import logging
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime

log = logging.getLogger(__name__)

# Checked in order. Patterns are deliberately specific: confirmation emails often
# mention interviews conditionally ("we may invite you to an interview"), and
# rejections often say "after careful assessment", so those must not match.
RULES = [
    ("offer", r"pleased to (extend|offer you)|offer letter|vertragsangebot|ihnen ein (vertrags)?angebot"),
    ("interview", r"(invite|invitation) (you )?(to|for) (an? |the )?(first |second |technical )?(interview|call|conversation)"
                  r"|interview (invitation|invite)|schedule (an? |the )?(interview|call|chat|time)|book a (time|slot)"
                  r"|calendly\.com|would like to (meet|speak with) you|einladung zu(m|r)? (vorstellungs|kennenlern)?gespräch"
                  r"|laden (wir )?(sie|dich) .{0,60}(gespräch|interview) ein"),
    ("rejection", r"unfortunately|not (be )?moving forward|move forward with other|other candidates|not been selected"
                  r"|decided not to|position has been filled|leider|absage|nicht berücksichtigen|anderen kandidat"),
    ("assessment", r"coding challenge|take[- ]home|test task|online assessment|complete (the|an|this) assessment"
                   r"|case study|hackerrank|codility|testgorilla"),
    ("confirmation", r"received your application|thank you for (applying|your application|your interest)"
                     r"|application (has been )?(received|submitted)|eingangsbestätigung|bewerbung erhalten"
                     r"|vielen dank für (ihre|deine) bewerbung"),
]
ATS_SENDERS = ("greenhouse", "lever.co", "ashbyhq", "workday", "smartrecruiters", "personio",
               "recruitee", "join.com", "workable", "successfactors", "icims", "teamtailor", "softgarden")
FOLDER_NAMES = {"offer": "Offer", "interview": "Interview", "assessment": "Assessment",
                "rejection": "Rejected", "confirmation": "Confirmation", "other": "Other"}
STATUS_FOR = {"offer": "offer", "interview": "interview", "assessment": "assessment",
              "rejection": "rejected", "confirmation": "confirmation"}


# "we may invite you to an interview" is a promise, not an invitation.
HEDGE = re.compile(r"\b(may|might|could|if|should|possibly|eventuell|ggf|falls|sollte)\b")


def classify(subject: str, body: str) -> str:
    text = f"{subject}\n{body}".lower()
    for label, pattern in RULES:
        for m in re.finditer(pattern, text):
            if label == "interview" and HEDGE.search(text[max(0, m.start() - 40):m.start()]):
                continue
            return label
    return "other"


def _norm(s: str) -> str:
    s = re.sub(r"\b(gmbh|ag|se|inc|ltd|llc|co|kg|corp|group)\b\.?", "", (s or "").lower())
    return re.sub(r"[^a-z0-9]", "", s)


def match_job(sender: str, subject: str, body: str, jobs) -> str | None:
    """Return the id of the applied job this email most likely belongs to."""
    hay_sender = sender.lower()
    hay = _norm(sender + " " + subject + " " + body[:3000])
    best = None
    for job in jobs:
        key = _norm(job["company"])
        if len(key) < 3:
            continue
        domain_hit = key in _norm(hay_sender.split("@")[-1])
        if (key in hay or domain_hit) and (best is None or len(key) > len(_norm(best["company"]))):
            best = job
    if best is None:
        return None
    # Several applications at one company: prefer the one whose title is mentioned.
    text = (subject + " " + body[:3000]).lower()
    for job in jobs:
        if _norm(job["company"]) == _norm(best["company"]) and job["title"] and job["title"].lower() in text:
            return job["id"]
    return best["id"]


def _decode(value) -> str:
    try:
        return str(make_header(decode_header(value or "")))
    except Exception:
        return value or ""


def _body_text(msg) -> str:
    parts = msg.walk() if msg.is_multipart() else [msg]
    html_fallback = ""
    for part in parts:
        ctype = part.get_content_type()
        if part.get_content_disposition() == "attachment":
            continue
        try:
            payload = part.get_payload(decode=True)
            if payload is None:
                continue
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except (LookupError, AttributeError):
            continue
        if ctype == "text/plain":
            return text
        if ctype == "text/html" and not html_fallback:
            html_fallback = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html_fallback)


class Mailbox:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.imap = imaplib.IMAP4_SSL(cfg["imap_host"], int(cfg.get("imap_port", 993)))
        self.imap.login(cfg["username"], cfg["password"])
        self.delimiter = self._delimiter()
        self.created: set[str] = set()

    def _delimiter(self) -> str:
        typ, data = self.imap.list('""', '""')
        if typ == "OK" and data and data[0]:
            m = re.search(rb'\(.*?\) "(.)"', data[0])
            if m:
                return m.group(1).decode()
        return "/"

    def folder(self, kind: str) -> str:
        name = f"{self.cfg.get('responses_folder', 'JobBot')}{self.delimiter}{FOLDER_NAMES[kind]}"
        if name not in self.created:
            self.imap.create(f'"{name}"')  # harmless "already exists" error otherwise
            self.created.add(name)
        return name

    def fetch_recent(self, days: int):
        self.imap.select(self.cfg.get("inbox", "INBOX"))
        since = (datetime.now() - timedelta(days=days)).strftime("%d-%b-%Y")
        typ, data = self.imap.uid("search", None, f"SINCE {since}")
        if typ != "OK":
            return
        for uid in data[0].split():
            typ, msg_data = self.imap.uid("fetch", uid, "(BODY.PEEK[])")
            if typ != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                continue
            yield uid, email.message_from_bytes(msg_data[0][1])

    def file(self, uid: bytes, kind: str) -> None:
        target = self.folder(kind)
        typ, _ = self.imap.uid("copy", uid, f'"{target}"')
        if typ == "OK" and self.cfg.get("move"):
            self.imap.uid("store", uid, "+FLAGS", r"(\Deleted)")
            self.imap.expunge()

    def close(self) -> None:
        try:
            self.imap.close()
        finally:
            self.imap.logout()


def sync(db, cfg: dict, llm_classify=None) -> dict:
    """Process new emails. Returns counts per classification."""
    tracked = db.jobs("applied", "confirmation", "assessment", "interview", "rejected", "offer", "needs_manual")
    counts: dict[str, int] = {}
    if not tracked:
        log.info("No applications to match emails against yet.")
        return counts
    box = Mailbox(cfg)
    try:
        for uid, msg in box.fetch_recent(int(cfg.get("lookback_days", 30))):
            message_id = msg.get("Message-ID") or f"uid-{uid.decode()}"
            if db.email_seen(message_id):
                continue
            subject = _decode(msg.get("Subject"))
            sender = parseaddr(_decode(msg.get("From")))[1] or _decode(msg.get("From"))
            body = _body_text(msg)
            job_id = match_job(sender, subject, body, tracked)
            from_ats = any(d in sender.lower() for d in ATS_SENDERS)
            if not job_id and not from_ats:
                continue  # not job related; leave it alone and don't record it
            kind = classify(subject, body)
            if kind == "other" and llm_classify:
                kind = llm_classify(subject, body)
            try:
                received = parsedate_to_datetime(msg.get("Date")).astimezone(timezone.utc).isoformat()
            except (TypeError, ValueError):
                received = datetime.now(timezone.utc).isoformat()

            db.add_email(message_id=message_id, job_id=job_id, received_at=received, sender=sender,
                         subject=subject, classification=kind, snippet=body[:300])
            if job_id and kind in STATUS_FOR:
                db.advance_status(job_id, STATUS_FOR[kind])
            if job_id:
                db.update(job_id, last_response_at=received, last_response_type=kind,
                          last_response_subject=subject)
            box.file(uid, kind)
            counts[kind] = counts.get(kind, 0) + 1
            log.info("%-12s %s | %s", kind, sender, subject[:80])
    finally:
        box.close()
    return counts
