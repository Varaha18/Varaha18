from datetime import datetime, timedelta, timezone

from jobbot import filters

CFG = {
    "title_include_any": ["robot", "automation", "vision"],
    "title_exclude": ["senior", "intern", "sr."],
    "locations": ["Berlin", "Remote"],
    "description_exclude": ["security clearance"],
    "max_age_days": 21,
}


def job(**kw):
    base = {"title": "Robotics Engineer", "location": "Berlin, Germany", "description": "ROS 2, Python",
            "posted_at": datetime.now(timezone.utc).isoformat()}
    return {**base, **kw}


def test_keeps_matching_job():
    assert filters.check(job(), CFG) == (True, "passed filters")


def test_prefix_include_and_whole_word_exclude():
    assert filters.check(job(title="Computer Vision Engineer"), CFG)[0]
    assert not filters.check(job(title="Senior Robotics Engineer"), CFG)[0]
    assert not filters.check(job(title="Sr. Automation Engineer"), CFG)[0]
    # "intern" must not exclude "International"
    assert filters.check(job(title="Automation Engineer, International Sites"), CFG)[0]
    assert not filters.check(job(title="Robotics Intern"), CFG)[0]


def test_location_description_and_age():
    assert not filters.check(job(location="Munich"), CFG)[0]
    assert not filters.check(job(description="Requires security clearance"), CFG)[0]
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    assert not filters.check(job(posted_at=old), CFG)[0]
    assert filters.check(job(posted_at=""), CFG)[0]


def test_keyword_score(profile):
    score, matched = filters.keyword_score(
        job(description="We use Python, OpenCV and ROS 2 with PLC integration. YOLOv8 a plus."), profile)
    assert {"Python", "OpenCV", "ROS 2", "PLC", "YOLOv8"} <= set(matched)
    assert 0 < score <= 100
