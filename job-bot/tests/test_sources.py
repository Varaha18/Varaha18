from jobbot import sources


class Resp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def test_greenhouse(monkeypatch):
    data = {"jobs": [{"id": 7, "title": "Robotics Engineer", "location": {"name": "Berlin"},
                      "absolute_url": "https://boards.greenhouse.io/acme/jobs/7", "updated_at": "2026-10-01T00:00:00Z",
                      "content": "&lt;p&gt;Build &lt;b&gt;robots&lt;/b&gt;&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;/ul&gt;"}]}
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: Resp(data))
    [job] = sources.greenhouse("acme", "Acme")
    assert job["id"] == "greenhouse:Acme:7" and job["location"] == "Berlin"
    assert "Build robots" in job["description"] and "- Python" in job["description"]


def test_lever_eu(monkeypatch):
    seen = {}

    def get(url, **kw):
        seen["url"] = url
        return Resp([{"id": "x1", "text": "Automation Engineer", "categories": {"location": "Berlin"},
                      "hostedUrl": "https://jobs.eu.lever.co/acme/x1", "applyUrl": "https://jobs.eu.lever.co/acme/x1/apply",
                      "createdAt": 1790000000000, "descriptionPlain": "About", "lists": [{"text": "You have", "content": "<li>PLC</li>"}]}])

    monkeypatch.setattr(sources.requests, "get", get)
    [job] = sources.lever("eu:acme")
    assert seen["url"].startswith("https://api.eu.lever.co/")
    assert "PLC" in job["description"] and job["posted_at"].startswith("2026")


def test_ashby_skips_unlisted(monkeypatch):
    data = {"jobs": [{"id": "1", "title": "CV Engineer", "location": "Berlin", "isRemote": True, "jobUrl": "u",
                      "applyUrl": "a", "descriptionPlain": "d", "publishedAt": "2026-10-01"},
                     {"id": "2", "title": "Hidden", "isListed": False}]}
    monkeypatch.setattr(sources.requests, "get", lambda *a, **k: Resp(data))
    jobs = sources.ashby("acme")
    assert [j["title"] for j in jobs] == ["CV Engineer"] and jobs[0]["location"] == "Berlin (Remote)"


def test_arbeitsagentur_uses_beruf_and_details(monkeypatch):
    calls = []

    class R(Resp):
        status_code, ok, text = 200, True, "{...}"

    def get(url, **kw):
        calls.append(url)
        if url.endswith("/jobs"):
            return R({"stellenangebote": [{"refnr": "10000-1184867112-S", "beruf": "Ingenieur/in - Robotik",
                                           "arbeitgeber": "Acme GmbH", "arbeitsort": {"ort": "Berlin"},
                                           "aktuelleVeroeffentlichungsdatum": "2026-10-20"}]})
        return R({"stellenangebotsTitel": "Robotics Engineer (m/w/d)", "stellenangebotsBeschreibung": "ROS 2 und Python"})

    monkeypatch.setattr(sources.requests, "get", get)
    [job] = sources.arbeitsagentur("Robotik", "Berlin")
    assert calls[0].endswith("/pc/v6/jobs")
    assert job["title"] == "Robotics Engineer (m/w/d)" and job["description"] == "ROS 2 und Python"

    monkeypatch.setattr(sources, "_ba_details", lambda refnr: {})
    [job] = sources.arbeitsagentur("Robotik", "Berlin")
    assert job["title"] == "Ingenieur/in - Robotik"  # falls back to the occupation, never empty


def test_arbeitsagentur_v6_format(monkeypatch):
    """Shape of the v6 response as returned by the live API in October 2026."""
    class R(Resp):
        status_code, ok, text = 200, True, "{...}"

    hit = {"stellenangebotsart": "ARBEIT", "stellenangebotsTitel": "Ingenieur*in (m/w/d)",
           "referenznummer": "10001-1002716922-S", "firma": "Acme Robotics GmbH",
           "datumErsteVeroeffentlichung": "2026-10-20",
           "stellenlokationen": [{"adresse": {"plz": "10178", "ort": "Berlin", "region": "BERLIN"}}]}
    monkeypatch.setattr(sources.requests, "get", lambda url, **kw: R({"ergebnisliste": [hit]}))
    [job] = sources.arbeitsagentur("Ingenieur", "Berlin", fetch_details=False)
    assert job["id"] == "arbeitsagentur:Acme Robotics GmbH:10001-1002716922-S"
    assert (job["title"], job["location"], job["posted_at"]) == ("Ingenieur*in (m/w/d)", "Berlin", "2026-10-20")
