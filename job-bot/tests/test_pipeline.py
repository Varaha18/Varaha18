"""End-to-end run of the CLI with network sources and Claude replaced by fakes."""
from datetime import datetime, timezone

import openpyxl
import yaml

from jobbot import cli, sources
from jobbot.resume import Bullet, Education, Project, SkillGroup, TailoredResume, FitScore

JOBS = [
    {"id": "greenhouse:Acme:1", "source": "greenhouse", "company": "Acme", "title": "Robotics Engineer",
     "location": "Berlin", "url": "https://example.com/1", "apply_url": "https://example.com/1",
     "description": "Python, OpenCV, ROS 2, PLC, YOLOv8, MediaPipe", "posted_at": datetime.now(timezone.utc).isoformat()},
    {"id": "greenhouse:Acme:2", "source": "greenhouse", "company": "Acme", "title": "Senior Robotics Engineer",
     "location": "Berlin", "url": "https://example.com/2", "apply_url": "https://example.com/2",
     "description": "Python", "posted_at": datetime.now(timezone.utc).isoformat()},
]


class FakeLLM:
    def parse(self, system, prompt, schema, effort=None):
        if schema is FitScore:
            return FitScore(score=82, reasons="Strong CV + robotics match", must_haves_missing=[])
        return TailoredResume(
            headline="Robotics Engineer",
            summary="Robotics engineer with computer vision focus; reached 91.2% accuracy on 6 gestures.",
            skills=[SkillGroup(category="Programming & CV", items=["Python", "OpenCV", "MediaPipe"]),
                    SkillGroup(category="Familiar with", items=["ROS2"])],
            experience=[],
            projects=[Project(profile_id="proj-thesis", name="Gesture control for an Igus robot arm",
                              context="Master's thesis", dates="2024",
                              bullets=[Bullet(text="Reached 91.2% accuracy on 6 gestures by pairing MediaPipe with an LSTM",
                                              source_ids=["proj-thesis-a1"])])],
            education=[Education(degree="M.Eng. Industry 4.0", institution="SRH Berlin", location="Berlin",
                                 dates="2023 - 2025", details="")],
            certifications=["Siemens Mechatronic Systems Certification (Levels 1-3)"], languages=["English"],
            keywords_matched=["Python"], keywords_missing=["PLC safety"], review_notes=[],
            cover_letter="Dear Acme team,\nI build robots that see.",
        )


def test_search_score_tailor_export(tmp_path, monkeypatch):
    root = cli.PACKAGE_ROOT
    cfg = yaml.safe_load((root / "config.example.yaml").read_text())
    cfg["search"]["sources"]["arbeitsagentur"]["enabled"] = False
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg))
    (tmp_path / "profile.yaml").write_text((root / "profile.example.yaml").read_text())
    monkeypatch.setattr(sources, "fetch_all", lambda c: JOBS)
    monkeypatch.setattr(cli, "_llm", lambda s: FakeLLM())

    for cmd in ("search", "score", "tailor"):
        cli.main(["--home", str(tmp_path), cmd])

    from jobbot.db import DB
    db = DB(tmp_path / "data" / "jobbot.db")
    good, senior = db.get("greenhouse:Acme:1"), db.get("greenhouse:Acme:2")
    assert senior["status"] == "filtered_out"
    assert good["status"] == "resume_ready", good["review_notes"]
    folder = tmp_path / "data" / "resumes" / "Acme_Robotics_Engineer"
    assert (folder / "Varaha_Venkat_Acme.pdf").read_bytes().startswith(b"%PDF")
    assert (folder / "Varaha_Venkat_Acme.docx").exists()
    assert (folder / "Varaha_Venkat_Acme_Cover_Letter.pdf").exists()

    cli.main(["--home", str(tmp_path), "mark", "greenhouse:Acme:1", "applied"])
    db.advance_status("greenhouse:Acme:1", "interview")
    db.advance_status("greenhouse:Acme:1", "confirmation")  # late confirmation must not downgrade
    cli.main(["--home", str(tmp_path), "export"])
    wb = openpyxl.load_workbook(tmp_path / "data" / "applications.xlsx")
    assert wb.sheetnames == ["Applications", "Responses", "Summary"]
    rows = list(wb["Applications"].iter_rows(values_only=True))
    assert rows[0][:4] == ("Company", "Title", "Location", "Status")
    assert ("Acme", "Robotics Engineer", "Berlin", "interview") == rows[1][:4]
    assert len(rows) == 2  # filtered-out posting is not listed
