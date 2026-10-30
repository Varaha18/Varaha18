import pytest

from jobbot.mailer import classify, match_job


@pytest.mark.parametrize("subject,body,expected", [
    ("Your application", "Thank you for applying. We will review your profile and may invite you to an interview.", "confirmation"),
    ("Update", "Thank you for your interest. Unfortunately we decided to move forward with other candidates.", "rejection"),
    ("Your application", "After careful assessment of your application, we regret to tell you that unfortunately...", "rejection"),
    ("Next steps", "We would like to invite you to a first interview. Please book a slot via calendly.com/acme", "interview"),
    ("Coding challenge", "Please complete the HackerRank test within 5 days.", "assessment"),
    ("Ihre Bewerbung", "Vielen Dank für Ihre Bewerbung. Leider müssen wir Ihnen mitteilen ...", "rejection"),
    ("Ihre Bewerbung", "Vielen Dank für Ihre Bewerbung, wir melden uns.", "confirmation"),
    ("Einladung", "Gerne laden wir Sie zu einem persönlichen Gespräch ein.", "interview"),
    ("Offer", "We are pleased to offer you the position of Robotics Engineer.", "offer"),
    ("Newsletter", "Our latest products", "other"),
])
def test_classify(subject, body, expected):
    assert classify(subject, body) == expected


JOBS = [
    {"id": "a", "company": "Wandelbots GmbH", "title": "Robotics Engineer"},
    {"id": "b", "company": "Wandelbots GmbH", "title": "Computer Vision Engineer"},
    {"id": "c", "company": "Agile Robots SE", "title": "Automation Engineer"},
]


def test_match_job_by_company_and_title():
    assert match_job("no-reply@greenhouse.io", "Your application to Wandelbots", "Computer Vision Engineer role", JOBS) == "b"
    assert match_job("jobs@agile-robots.com", "Hello", "Thanks", JOBS) == "c"
    assert match_job("friend@gmail.com", "Lunch?", "See you", JOBS) is None


def test_sync_updates_status_and_files_mail(tmp_path, monkeypatch):
    from email.message import EmailMessage

    from jobbot import mailer
    from jobbot.db import DB

    db = DB(tmp_path / "t.db")
    db.upsert_job({"id": "a", "source": "greenhouse", "company": "Wandelbots GmbH", "title": "Robotics Engineer",
                   "location": "Berlin", "url": "u", "apply_url": "u", "description": "", "posted_at": ""})
    db.update("a", status="applied")

    def msg(mid, frm, subject, body):
        m = EmailMessage()
        m["Message-ID"], m["From"], m["Subject"] = mid, frm, subject
        m["Date"] = "Mon, 26 Oct 2026 10:00:00 +0000"
        m.set_content(body)
        return m

    filed = []

    class FakeBox:
        def __init__(self, cfg):
            pass

        def fetch_recent(self, days):
            yield b"1", msg("<1>", "Wandelbots <no-reply@greenhouse.io>", "Wandelbots: interview",
                            "We would like to invite you to an interview for Robotics Engineer.")
            yield b"2", msg("<2>", "mom@example.com", "Dinner", "Sunday?")

        def file(self, uid, kind):
            filed.append((uid, kind))

        def close(self):
            pass

    monkeypatch.setattr(mailer, "Mailbox", FakeBox)
    counts = mailer.sync(db, {})
    assert counts == {"interview": 1}
    assert filed == [(b"1", "interview")]
    assert db.get("a")["status"] == "interview"
    assert mailer.sync(db, {}) == {}  # already processed
