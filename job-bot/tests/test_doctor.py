from jobbot import doctor


def test_profile_check_flags_todos_bad_dates_and_duplicates(profile):
    profile["experience"][0]["start"] = "March 2020"
    profile["projects"][0]["id"] = "exp1"
    msgs = [m for _, m in doctor.check_profile(profile)]
    assert any("TODO placeholders" in m for m in msgs)
    assert any("March 2020" in m for m in msgs)
    assert any("'exp1' is used more than once" in m for m in msgs)


def test_arbeitsagentur_check_explains_zero_results(monkeypatch):
    def fake(what, *a, **k):
        if what == "Ingenieur":
            return [{"id": "arbeitsagentur:X:1"}]
        return []

    monkeypatch.setattr(doctor.sources, "arbeitsagentur", fake)
    msgs = [m for _, m in doctor._check_arbeitsagentur({"keywords": ["Robotics Engineer"]}, {"where": "Berlin"})]
    assert "'Robotics Engineer' -> 0 postings" in msgs[0]
    assert "none of your keywords match" in msgs[1]
