import os
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# In environments where the Playwright browser revision differs, point at a local Chromium.
if "JOBBOT_CHROMIUM" not in os.environ:
    for candidate in Path("/opt/pw-browsers").glob("chromium-*/chrome-linux64/chrome"):
        os.environ["JOBBOT_CHROMIUM"] = str(candidate)
        break


@pytest.fixture
def profile():
    p = yaml.safe_load((ROOT / "profile.example.yaml").read_text())
    p["experience"][0].update(company="Acme Automation GmbH", title="Design Engineer",
                              display_title="Mechanical Design Engineer")
    p["experience"][0]["achievements"][0].update(metric="cut machining time 18%")
    p["application_answers"].update(salary_expectation="55000")
    return p
