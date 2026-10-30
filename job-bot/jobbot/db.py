"""SQLite store: the source of truth that the Excel tracker is exported from."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

# Pipeline order. A job's status only moves forward (an interview invite is
# never overwritten by a late "we received your application" email).
STATUS_RANK = {
    "found": 0,
    "filtered_out": 1,
    "shortlisted": 2,
    "resume_ready": 3,
    "resume_needs_review": 3,
    "needs_manual": 4,
    "skipped": 4,
    "applied": 5,
    "confirmation": 6,
    "rejected": 7,
    "assessment": 8,
    "interview": 9,
    "offer": 10,
    "withdrawn": 11,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    source TEXT, company TEXT, title TEXT, location TEXT,
    url TEXT, apply_url TEXT, description TEXT,
    posted_at TEXT, found_at TEXT,
    score INTEGER, score_reason TEXT,
    status TEXT DEFAULT 'found',
    resume_path TEXT, cover_letter_path TEXT, review_notes TEXT,
    applied_at TEXT, notes TEXT,
    last_response_at TEXT, last_response_type TEXT, last_response_subject TEXT
);
CREATE TABLE IF NOT EXISTS emails (
    message_id TEXT PRIMARY KEY,
    job_id TEXT, received_at TEXT, sender TEXT, subject TEXT,
    classification TEXT, snippet TEXT
);
"""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class DB:
    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def upsert_job(self, job: dict) -> bool:
        """Insert a newly found job. Returns True if it was new."""
        exists = self.conn.execute("SELECT 1 FROM jobs WHERE id=?", (job["id"],)).fetchone()
        if exists:
            # Refresh posting fields but never touch pipeline state.
            self.conn.execute(
                "UPDATE jobs SET title=?, location=?, url=?, apply_url=?, description=?, posted_at=? WHERE id=?",
                (job["title"], job["location"], job["url"], job["apply_url"],
                 job["description"], job["posted_at"], job["id"]),
            )
            self.conn.commit()
            return False
        self.conn.execute(
            "INSERT INTO jobs (id, source, company, title, location, url, apply_url, description, posted_at, found_at, status)"
            " VALUES (?,?,?,?,?,?,?,?,?,?, 'found')",
            (job["id"], job["source"], job["company"], job["title"], job["location"],
             job["url"], job["apply_url"], job["description"], job["posted_at"], now()),
        )
        self.conn.commit()
        return True

    def update(self, job_id: str, **fields) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))
        self.conn.commit()

    def advance_status(self, job_id: str, status: str, **fields) -> bool:
        """Set status only if it moves the job forward in the pipeline."""
        row = self.get(job_id)
        if row is None:
            return False
        if STATUS_RANK.get(status, 0) < STATUS_RANK.get(row["status"], 0):
            if fields:
                self.update(job_id, **fields)
            return False
        self.update(job_id, status=status, **fields)
        return True

    def get(self, job_id: str):
        return self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()

    def jobs(self, *statuses: str) -> list[sqlite3.Row]:
        if statuses:
            marks = ",".join("?" * len(statuses))
            sql = f"SELECT * FROM jobs WHERE status IN ({marks}) ORDER BY score DESC, found_at"
            return self.conn.execute(sql, statuses).fetchall()
        return self.conn.execute("SELECT * FROM jobs ORDER BY found_at DESC").fetchall()

    def applied_today(self) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        return self.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE applied_at LIKE ?", (today + "%",)
        ).fetchone()[0]

    def email_seen(self, message_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM emails WHERE message_id=?", (message_id,)
        ).fetchone() is not None

    def add_email(self, **row) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO emails (message_id, job_id, received_at, sender, subject, classification, snippet)"
            " VALUES (:message_id, :job_id, :received_at, :sender, :subject, :classification, :snippet)",
            row,
        )
        self.conn.commit()

    def emails(self) -> list[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM emails ORDER BY received_at DESC").fetchall()
