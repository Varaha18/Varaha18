# JobBot

A personal job-search pipeline that runs on your own computer:

1. **Search:** pulls new postings from the Bundesagentur für Arbeit job board and from the company career boards you list (Greenhouse, Lever, Ashby).
2. **Filter and score:** drops postings that fail your title, location, age and exclusion rules. Claude then scores how well each remaining job fits your profile (0–100) and shortlists the strong ones.
3. **Tailor:** writes a resume (PDF + DOCX) and an optional cover letter for each shortlisted job, using only facts from your `profile.yaml`.
4. **Fact-check:** compares every title, number, skill and bullet with your profile. A resume with any mismatch goes to `resume_needs_review` instead of being sent.
5. **Apply:** opens the application form, fills it from your profile, uploads the resume and submits it. By default you confirm each submit yourself.
6. **Mail:** reads your inbox over IMAP, matches replies to your applications and labels each one as confirmation, rejection, assessment, interview or offer. It files them into `JobBot/<Type>` folders and updates the application's status.
7. **Excel tracker:** rewrites `data/applications.xlsx` after every command. It has three sheets: Applications, Responses and Summary (response and interview rates).

## Setup

```bash
cd job-bot
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
python -m jobbot init              # creates config.yaml and profile.yaml
```

Set these environment variables (e.g. in your shell profile):

| Variable | What |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key from console.anthropic.com (used for scoring, resumes, form answers) |
| `JOBBOT_MAIL_USER` | your email address |
| `JOBBOT_MAIL_PASSWORD` | an **app-specific password**. iCloud: appleid.apple.com → Sign-In and Security → App-Specific Passwords. Gmail: myaccount.google.com/apppasswords |

Then edit:

- **`profile.yaml`** is the only source of facts the bot uses. It is pre-filled from your GitHub README; replace every `TODO`. Real numbers in `achievements` make the biggest difference to resume quality.
- **`config.yaml`** holds search terms, filters, the career boards to watch, the apply mode and your IMAP server (iCloud is preset).

`config.yaml`, `profile.yaml` and `data/` are git-ignored, so your personal data stays on your machine.

## Usage

```bash
python -m jobbot run                 # full pipeline (apply in review mode)
python -m jobbot run --mode auto     # unattended applying (see below)

python -m jobbot search              # individual steps
python -m jobbot score
python -m jobbot tailor --limit 5
python -m jobbot apply               # review mode: you confirm each submit
python -m jobbot mail
python -m jobbot status              # pipeline counts + what needs you
python -m jobbot mark <job-id> interview --note "call with HR on Friday"
```

### Apply modes

- **review** (default): a browser window opens with the form already filled in. The terminal lists what was filled and what is missing. Check the form, complete anything missing, then press Enter to submit. Type `m` if you submitted it yourself, or `s` to skip.
- **auto**: the bot submits by itself only when all of these are true: the resume passed fact-checking, every required field could be answered from your profile, and no CAPTCHA is on the page. Otherwise the job is marked `needs_manual` and waits for your next review run. `daily_limit` caps the number of applications per day. A screenshot of every submitted form is saved in `data/screenshots/`.

The bot never tries to bypass CAPTCHAs. LinkedIn and Indeed are not used because their terms forbid automated applications. Postings from the Arbeitsagentur often redirect to a company's own portal (Workday, SuccessFactors and so on), which usually requires an account. Those jobs go to `needs_manual` so you can apply in review mode.

### Run it every day

macOS/Linux (`crontab -e`), every weekday at 08:10:

```
10 8 * * 1-5 cd /path/to/job-bot && .venv/bin/python -m jobbot run --mode auto >> data/cron.log 2>&1
```

Windows: in Task Scheduler, create a daily task that runs `.venv\Scripts\python.exe -m jobbot run --mode auto` with "Start in" set to the `job-bot` folder.

## How resumes are written

The prompt is in `jobbot/resume.py` and the checks are in `jobbot/verify.py`. Every resume follows these rules:

| Rule | What the bot does |
|---|---|
| Strategic scope & summary | Treats the resume as a marketing document for this one job. Writes a 3–4 sentence summary with the target title, years of experience and your strongest real achievement. Covers only the last 10–15 years. |
| Titles | Uses your official title, or the `display_title` you set for that role in your profile (e.g. "Werkstudent" → "Working Student, Robotics Engineering"). It does not invent or raise titles. |
| X-Y-Z bullets | Writes every bullet as "Accomplished X, measured by Y, by doing Z", starting with strong verbs, and uses every metric in your profile (aiming for ~80% of bullets with hard data). For team results it states your part accurately. Where a number is missing, a review note asks you for the real figure instead of making one up. |
| Dates & gaps | Can show years only (`date_style: year`), which smooths over short gaps. Longer gaps are flagged in the review notes so you can add real courses, projects, freelance work or career breaks to `other_activities`. No made-up employers. |
| ATS optimization | Uses the job ad's exact spelling for tools and methods you have (e.g. "ROS2" vs "ROS 2") and puts a skills section near the top. Skills marked `familiar` appear under "Familiar with". Job requirements you don't meet are listed in the review notes, not on the resume. |
| Context & style | Adds one line per employer from your `company_context` and writes short, active sentences. |

Each resume folder in `data/resumes/` also contains `review_notes.txt`. It lists fact-check findings, gaps, metrics worth adding and job requirements your profile doesn't cover. That file is the quickest way to improve your profile.

## Tests

```bash
pip install pytest
python -m pytest -q
```

The tests cover filtering, fact-checking, form filling on a sample application form, submit confirmation, email classification (English and German), inbox sync, the Excel export, and a search → score → tailor → export run that uses a stubbed Claude client.
