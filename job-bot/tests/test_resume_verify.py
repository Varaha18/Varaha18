from jobbot.resume import Bullet, Education, Role, SkillGroup, TailoredResume, find_gaps, Project
from jobbot.verify import verify


def make(profile, **over):
    base = dict(
        headline="Robotics Engineer",
        summary="Robotics engineer who reached 91.2% accuracy on 6 gestures.",
        skills=[SkillGroup(category="Programming", items=["Python", "OpenCV"]),
                SkillGroup(category="Familiar with", items=["ROS2"])],
        experience=[Role(profile_id="exp1", company="Acme Automation GmbH", company_context="Automation supplier",
                         title="Mechanical Design Engineer", location="Berlin", dates="2020 - 2022",
                         bullets=[Bullet(text="Cut machining time 18% by redesigning fixtures", source_ids=["exp1-a1"])])],
        projects=[Project(profile_id="proj-thesis", name="Gesture control", context="Thesis", dates="2024",
                          bullets=[Bullet(text="Reached 91.2% accuracy on 6 gestures", source_ids=["proj-thesis-a1"])])],
        education=[Education(degree="M.Eng.", institution="SRH Berlin", location="Berlin", dates="2025", details="")],
        certifications=[], languages=[], keywords_matched=[], keywords_missing=[], review_notes=[], cover_letter="",
    )
    base.update(over)
    return TailoredResume(**base)


def test_clean_resume_passes(profile):
    assert verify(make(profile), profile) == []


def test_flags_invented_number(profile):
    r = make(profile, summary="Improved throughput by 45% across 3 plants.")
    issues = verify(r, profile)
    assert any("'45'" in i for i in issues)


def test_flags_inflated_title(profile):
    r = make(profile)
    r.experience[0].title = "Head of Robotics"
    assert any("Head of Robotics" in i for i in verify(r, profile))


def test_flags_unknown_and_overstated_skills(profile):
    r = make(profile, skills=[SkillGroup(category="Tools", items=["Kubernetes", "ROS 2"])])
    issues = verify(r, profile)
    assert any("Kubernetes" in i for i in issues)
    assert any("'familiar'" in i for i in issues)


def test_flags_unsourced_bullet(profile):
    r = make(profile)
    r.experience[0].bullets.append(Bullet(text="Led global rollout", source_ids=[]))
    assert any("no valid source" in i for i in verify(r, profile))


def test_find_gaps():
    profile = {"experience": [{"start": "2019-01", "end": "2019-12"}, {"start": "2020-08", "end": "2021-06"}]}
    gaps = find_gaps(profile, min_months=3)
    assert len(gaps) == 1 and gaps[0].startswith("7-month gap")
