"""Command line entry point: python -m jobbot <command>."""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path

from pydantic import BaseModel

from . import filters, sources, tracker
from .config import Settings, load_settings
from .db import DB, now

log = logging.getLogger("jobbot")
PACKAGE_ROOT = Path(__file__).resolve().parent.parent


def _llm(s: Settings):
    from .llm import LLM

    cfg = s.section("llm")
    if cfg.get("enabled", True) is False:
        return None
    return LLM(cfg)


# ------------------------------------------------------------------ commands

def cmd_init(s_root: Path) -> None:
    for name in ("config", "profile"):
        target = s_root / f"{name}.yaml"
        if target.exists():
            print(f"{target.name} already exists, leaving it alone")
        else:
            shutil.copy(PACKAGE_ROOT / f"{name}.example.yaml", target)
            print(f"created {target.name} - edit it before running the bot")


def cmd_search(s: Settings, db: DB) -> None:
    cfg = s.section("search")
    found = sources.fetch_all(cfg)
    new = kept = 0
    for job in found:
        if not db.upsert_job(job):
            continue
        new += 1
        keep, reason = filters.check(job, cfg)
        if keep:
            kept += 1
        else:
            db.update(job["id"], status="filtered_out", score_reason=reason)
    print(f"search: {len(found)} postings, {new} new, {kept} passed filters")


class _Answer(BaseModel):
    answer: str
    answerable_from_profile: bool


class _MailType(BaseModel):
    category: str


def cmd_score(s: Settings, db: DB) -> None:
    cfg = s.section("scoring")
    min_score = cfg.get("min_score", 60)
    llm = _llm(s) if cfg.get("use_llm", True) else None
    shortlisted = 0
    for job in db.jobs("found"):
        kw_score, matched = filters.keyword_score(dict(job), s.profile)
        score, reason = kw_score, f"keyword match: {', '.join(matched) or 'none'}"
        # Postings without a description can't be keyword-matched; let Claude judge the title.
        thin = len(job["description"] or "") < 300
        if llm and (thin or kw_score >= cfg.get("llm_prefilter_min", 15)):
            from .llm import LLMError
            from .resume import llm_score

            try:
                fit = llm_score(llm, s.profile, dict(job))
                score = fit.score
                reason = fit.reasons
                if fit.must_haves_missing:
                    reason += " | missing: " + ", ".join(fit.must_haves_missing)
            except LLMError as e:
                log.warning("LLM scoring failed for %s: %s (using keyword score)", job["title"], e)
        status = "shortlisted" if score >= min_score else "filtered_out"
        shortlisted += status == "shortlisted"
        db.update(job["id"], score=score, score_reason=reason, status=status)
        log.info("%3d  %-9s %s @ %s", score, status, job["title"], job["company"])
    print(f"score: {shortlisted} jobs shortlisted (min score {min_score})")


def cmd_tailor(s: Settings, db: DB, limit: int | None, job_id: str | None) -> None:
    from .llm import LLMError
    from .render import html_to_pdf, letter_html, render_docx, resume_html, slug
    from .resume import tailor
    from .verify import verify

    llm = _llm(s)
    if llm is None:
        sys.exit("tailor needs the Claude API (set llm.enabled: true and ANTHROPIC_API_KEY)")
    cfg = s.section("resume")
    jobs = [db.get(job_id)] if job_id else db.jobs("shortlisted")[: limit or None]
    personal = s.profile.get("personal", {})
    for job in filter(None, jobs):
        print(f"tailoring: {job['title']} @ {job['company']}")
        try:
            result = tailor(llm, s.profile, dict(job), cfg)
        except LLMError as e:
            log.error("  failed: %s", e)
            continue
        issues = verify(result, s.profile)
        base = s.resumes_dir / slug(f"{job['company']}_{job['title']}")
        base.mkdir(exist_ok=True)
        stem = slug(f"{personal.get('name', 'Resume')}_{job['company']}")
        (base / "resume.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        render_docx(result, personal, base / f"{stem}.docx")
        pdf = html_to_pdf(resume_html(result, personal), base / f"{stem}.pdf")
        cover = None
        if result.cover_letter.strip():
            (base / "cover_letter.txt").write_text(result.cover_letter, encoding="utf-8")
            cover = html_to_pdf(letter_html(result.cover_letter, personal), base / f"{stem}_Cover_Letter.pdf")

        notes = [f"FACT-CHECK: {i}" for i in issues] + result.review_notes
        if result.keywords_missing:
            notes.append("JD asks for (not in your profile): " + ", ".join(result.keywords_missing))
        (base / "review_notes.txt").write_text("\n".join(notes) or "No issues.", encoding="utf-8")
        status = "resume_needs_review" if issues else "resume_ready"
        db.update(job["id"], status=status, resume_path=str(pdf),
                  cover_letter_path=str(cover) if cover else None, review_notes="\n".join(notes))
        print(f"  -> {status}: {pdf.relative_to(s.root)}" + (f" ({len(issues)} fact-check issues)" if issues else ""))


def cmd_apply(s: Settings, db: DB, mode: str, limit: int | None, job_id: str | None) -> None:
    from . import apply as ap

    cfg = s.section("apply")
    mode = mode or cfg.get("mode", "review")
    if job_id:
        jobs = [db.get(job_id)]
    elif mode == "auto":
        jobs = db.jobs("resume_ready")
    else:
        jobs = db.jobs("resume_ready", "resume_needs_review", "needs_manual")
    daily_limit = cfg.get("daily_limit", 15)
    budget = max(0, daily_limit - db.applied_today())
    jobs = [j for j in jobs if j and j["resume_path"]][: min(budget, limit or budget)]
    if not jobs:
        print(f"apply: nothing to do (daily limit {daily_limit}, already applied today: {db.applied_today()})")
        return

    llm = _llm(s) if cfg.get("llm_free_text_answers", True) else None
    screens = s.data_dir / "screenshots"
    screens.mkdir(exist_ok=True)
    pw, browser = ap.launch(headless=(mode == "auto" and cfg.get("headless", True)))
    try:
        for job in jobs:
            url = ap.application_url(job)
            print(f"\napply [{mode}]: {job['title']} @ {job['company']}\n  {url}")
            page = browser.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2000)
                ap._open_form(page)
                cover_txt = Path(job["resume_path"]).parent / "cover_letter.txt"
                files = {"resume": job["resume_path"], "cover_letter": job["cover_letter_path"],
                         "cover_letter_text": cover_txt.read_text(encoding="utf-8") if cover_txt.exists() else None}
                free_text = _free_text_answerer(llm, s.profile, job) if llm else None
                report = ap.fill_form(page, s.profile, files, cfg, free_text)
                for line in report.filled:
                    print(f"  filled  {line}")
                for line in report.unresolved:
                    print(f"  MISSING {line}")
                if report.captcha:
                    print("  CAPTCHA present - needs you")

                if mode == "auto":
                    if report.unresolved or report.captcha or job["status"] != "resume_ready":
                        db.advance_status(job["id"], "needs_manual",
                                          notes="auto-apply stopped: " + "; ".join(report.unresolved or ["captcha"]))
                        continue
                    confirmed = ap.submit(page)
                else:
                    choice = input("  Check the browser. [Enter]=submit  m=I submitted it myself  s=skip  q=quit: ").strip().lower()
                    if choice == "q":
                        break
                    if choice == "s":
                        db.advance_status(job["id"], "skipped")
                        continue
                    confirmed = True if choice == "m" else ap.submit(page)

                page.screenshot(path=str(screens / f"{job['id'].replace(':', '_')}.png"), full_page=True)
                if confirmed:
                    db.advance_status(job["id"], "applied", applied_at=now())
                    print("  -> applied")
                else:
                    db.advance_status(job["id"], "needs_manual", notes="submit clicked but no confirmation seen")
                    print("  -> no confirmation seen; marked needs_manual")
            except Exception as e:
                log.error("  error: %s", e)
                db.advance_status(job["id"], "needs_manual", notes=f"error: {e}"[:300])
            finally:
                page.close()
    finally:
        browser.close()
        pw.stop()


FREE_TEXT_PROMPT = """You answer one question on a job application form for the candidate.
Use only facts from the candidate profile and the job description. 2-4 sentences,
first person, specific, no clichés. If answering would need a fact that is not in the
profile (a number, a date, a legal/eligibility statement, personal data), set
answerable_from_profile to false and leave answer empty."""


def _free_text_answerer(llm, profile: dict, job):
    import yaml

    from .llm import LLMError

    slim = {k: v for k, v in profile.items() if k != "application_answers"}

    def answer(question: str) -> str | None:
        prompt = (f"<job>{job['title']} at {job['company']}\n{job['description'][:8000]}</job>\n"
                  f"<profile>{yaml.safe_dump(slim, sort_keys=False, allow_unicode=True)}</profile>\n"
                  f"<question>{question}</question>")
        try:
            out = llm.parse(FREE_TEXT_PROMPT, prompt, _Answer, effort="low")
        except LLMError as e:
            log.warning("free-text answer failed: %s", e)
            return None
        return out.answer if out.answerable_from_profile and out.answer.strip() else None

    return answer


def cmd_mail(s: Settings, db: DB) -> None:
    from . import mailer

    cfg = s.section("mail")
    if not cfg.get("username") or not cfg.get("password"):
        print("mail: skipped (set JOBBOT_MAIL_USER and JOBBOT_MAIL_PASSWORD)")
        return
    llm = _llm(s) if cfg.get("llm_classify", True) else None

    def llm_classify(subject: str, body: str) -> str:
        from .llm import LLMError

        try:
            out = llm.parse(
                "Classify a reply to a job application as exactly one of: offer, interview, "
                "assessment, rejection, confirmation, other.",
                f"Subject: {subject}\n\n{body[:4000]}", _MailType, effort="low")
        except LLMError:
            return "other"
        return out.category if out.category in mailer.FOLDER_NAMES else "other"

    counts = mailer.sync(db, cfg, llm_classify if llm else None)
    print("mail: " + (", ".join(f"{k}={v}" for k, v in counts.items()) or "no new responses"))


def cmd_status(db: DB) -> None:
    from collections import Counter

    counts = Counter(j["status"] for j in db.jobs())
    for status, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"{status:22} {n}")
    pending = db.jobs("resume_needs_review", "needs_manual")
    if pending:
        print("\nNeeds your attention:")
        for j in pending:
            print(f"  [{j['status']}] {j['id']}  {j['title']} @ {j['company']}")


# ------------------------------------------------------------------ main

def main(argv=None) -> None:
    parser = argparse.ArgumentParser(prog="jobbot", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--home", type=Path, help="folder with config.yaml/profile.yaml (default: cwd)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create config.yaml and profile.yaml from the examples")
    sub.add_parser("check", help="test profile, API key, browser, job boards and mail login")
    sub.add_parser("search", help="fetch new postings and apply filters")
    sub.add_parser("score", help="score new postings for fit and shortlist")
    t = sub.add_parser("tailor", help="generate tailored resumes for shortlisted jobs")
    t.add_argument("--limit", type=int)
    t.add_argument("--job")
    a = sub.add_parser("apply", help="fill and submit applications")
    a.add_argument("--mode", choices=["review", "auto"])
    a.add_argument("--limit", type=int)
    a.add_argument("--job")
    sub.add_parser("mail", help="match inbox replies to applications and file them")
    sub.add_parser("export", help="rewrite the Excel tracker")
    sub.add_parser("status", help="show pipeline counts")
    m = sub.add_parser("mark", help="set a job's status by hand")
    m.add_argument("job")
    m.add_argument("status")
    m.add_argument("--note", default="")
    r = sub.add_parser("run", help="search -> score -> tailor -> apply -> mail -> export")
    r.add_argument("--mode", choices=["review", "auto"])
    r.add_argument("--limit", type=int, help="max resumes/applications this run")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)-7s %(message)s")
    for noisy in ("httpx", "httpx2", "anthropic"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if args.cmd == "init":
        cmd_init(args.home or Path.cwd())
        return

    s = load_settings(args.home)
    if args.cmd == "check":
        from . import doctor

        sys.exit(doctor.run(s))
    db = DB(s.db_path)
    if args.cmd == "search":
        cmd_search(s, db)
    elif args.cmd == "score":
        cmd_score(s, db)
    elif args.cmd == "tailor":
        cmd_tailor(s, db, args.limit, args.job)
    elif args.cmd == "apply":
        cmd_apply(s, db, args.mode, args.limit, args.job)
    elif args.cmd == "mail":
        cmd_mail(s, db)
    elif args.cmd == "status":
        cmd_status(db)
    elif args.cmd == "mark":
        fields = {"notes": args.note} if args.note else {}
        if args.status == "applied":
            fields["applied_at"] = now()
        db.update(args.job, status=args.status, **fields)
    elif args.cmd == "run":
        cmd_search(s, db)
        cmd_score(s, db)
        cmd_tailor(s, db, args.limit, None)
        cmd_apply(s, db, args.mode, args.limit, None)
        cmd_mail(s, db)
    out = tracker.export(db, s.excel_path)
    print(f"tracker: {out}")
