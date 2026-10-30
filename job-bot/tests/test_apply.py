from pathlib import Path

import pytest

from jobbot import apply as ap

FIXTURE = Path(__file__).parent / "fixtures" / "form.html"


def test_answer_for(profile):
    assert ap.answer_for("First Name *", profile) == "Varaha"
    assert ap.answer_for("Last name", profile) == "Venkat"
    assert ap.answer_for("Email address", profile) == "varaha.venkat@icloud.com"
    assert ap.answer_for("Do you require visa sponsorship?", profile) is False
    assert ap.answer_for("Are you authorized to work in Germany?", profile) is True
    assert ap.answer_for("Salary expectations (EUR)", profile) == "55000"
    assert ap.answer_for("Gender", profile) == "Decline to self-identify"
    assert ap.answer_for("Do you have open source contributions?", profile) is None
    assert ap.answer_for("Favourite colour", profile) is None


def test_choose_option():
    assert ap.choose_option(True, ["Select...", "Yes", "No"]) == "Yes"
    assert ap.choose_option(False, ["Ja", "Nein"]) == "Nein"
    assert ap.choose_option("Decline to self-identify", ["Male", "Female", "I don't wish to answer"]) == "I don't wish to answer"
    assert ap.choose_option("Berlin", ["Munich", "Berlin, Germany"]) == "Berlin, Germany"
    assert ap.choose_option("Mars", ["Munich", "Berlin"]) is None


def test_application_url():
    assert ap.application_url({"source": "lever", "apply_url": "https://jobs.lever.co/x/123", "url": ""}).endswith("/123/apply")
    assert ap.application_url({"source": "ashby", "apply_url": "https://jobs.ashbyhq.com/x/abc", "url": ""}).endswith("/abc/application")


@pytest.fixture
def page():
    pw, browser = ap.launch(headless=True)
    pg = browser.new_page()
    pg.goto(FIXTURE.as_uri())
    yield pg
    browser.close()
    pw.stop()


def test_fill_form_reports_unknown_required_fields(page, profile, tmp_path):
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    report = ap.fill_form(page, profile, {"resume": str(resume)}, {"accept_privacy_consent": True},
                          free_text=lambda q: "Because I build robots." if "why" in q.lower() else None)
    assert page.input_value("#fn") == "Varaha"
    assert page.input_value("#em") == "varaha.venkat@icloud.com"
    assert page.eval_on_selector("#auth", "e => e.value") == "Yes"
    assert page.is_checked("input[name=spons][value='0']")  # sponsorship -> No
    assert page.input_value("#why") == "Because I build robots."
    assert page.is_checked("input[name=gdpr]")
    assert page.eval_on_selector("#cv", "e => e.files.length") == 1
    assert report.unresolved == ["Your favourite colour *"]
    assert not report.captcha


def test_submit_detects_confirmation(page, profile, tmp_path):
    page.fill("#fav", "blue")
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    ap.fill_form(page, profile, {"resume": str(resume)}, {"accept_privacy_consent": True}, free_text=lambda q: "x")
    page.fill("#fav", "blue")
    assert ap.submit(page, timeout_ms=5000)
