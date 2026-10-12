"""One job, end to end: the shared engine behind `openapply add`, `autopilot`, and `apply --submit`.

Step by step (`process_job`):
1. Pick the resume: the matcher's choice for the job's role family (PM -> your
   PM resume, SWE/Data Science/ML -> your SWE/Data resume, per `families:` in
   config/profile.yaml).
2. Draft the cover letter and answers (`openapply tailor`) if there are none yet,
   which also writes the fabrication check.
3. Open the application and fill it (Greenhouse/Lever/Ashby: apply/filler.py;
   Workday: apply/workday.py, which stops at Review).
4. If auto-submit is ON (profile `auto_submit: true`) and this command asked for
   it, run every guardrail in apply/autosubmit.py. All pass -> submit, keep a
   receipt, mark the job submitted, and refresh the CSV / Google Sheet export.
   Anything fails -> nothing is submitted; the reason goes on the
   `openapply todo` list.
5. Text you one line: "Applied: Company – Role ✅" or
   "Needs you: Company – Role — reason" (only for `add` / `autopilot`, and only
   printed, not sent, with --dry-run).

`ingest_url` is step 0 for `openapply add`: fetch the posting from its ATS's
public API, store it, and score it alongside the latest run's jobs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .apply.answers import SavedAnswers
from .apply.autosubmit import Outcome, job_checks, settings, try_submit
from .apply.workday import is_workday_url
from .config import GENERATED_DIR, PROJECT_ROOT, load_filters
from .models import Job
from .tracker.db import Tracker

FORM_ATS = ("greenhouse", "lever", "ashby")


@dataclass
class RunResult:
    job_id: str
    company: str
    title: str
    status: str            # submitted | needs_you | would_submit | filled | error
    reason: str = ""
    warnings: list[str] = field(default_factory=list)
    receipt: str = ""
    message: str = ""      # the iMessage text
    report: object = field(default=None, repr=False)  # the filler's FillReport (captured choices for --learn)


def fill_kind(job: Job) -> str | None:
    if job.ats == "workday" or is_workday_url(job.url):
        return "workday"
    if job.ats in FORM_ATS:
        return "form"
    return None


def alert_text(r: RunResult) -> str:
    if r.status == "submitted":
        return f"Applied: {r.company} – {r.title} ✅"
    reason = (r.reason or "needs a human").replace("\n", " ")
    return f"Needs you: {r.company} – {r.title} — {reason[:160]}"


def sync_exports(tracker: Tracker, profile: dict) -> list[str]:
    """Refresh the CSV and (if configured) the Google Sheet with every submitted application."""
    import os

    from .tracker.export import CsvExporter, GoogleSheetsExporter, sheet_rows

    sheets_cfg = profile.get("sheets") or {}
    yes = sheets_cfg.get("yes_value", "Yes")
    rows = sheet_rows(tracker.by_status(["submitted", "interview", "rejected", "offer"]), yes=yes)
    out = []
    for e in (CsvExporter(PROJECT_ROOT / "generated" / "applications.csv"),
              GoogleSheetsExporter(os.environ.get("GOOGLE_SHEET_ID") or sheets_cfg.get("sheet_id", ""),
                                   sheets_cfg.get("worksheet") or None, yes)):
        try:
            out.append(str(e.export(rows)))
        except Exception as ex:  # the sheet stays queued: the next `openapply export` retries every submitted row
            out.append(f"{type(e).__name__}: {type(ex).__name__}: {ex} (will retry on next export)")
    return out


def ingest_url(tracker: Tracker, url: str, profile: dict, client=None, rescore: bool = True):
    """Fetch, filter (warn-only), store, and score one pasted link. Returns (row, warnings)."""
    from . import pipeline
    from .filter import apply_filters, load_tiers
    from .filter.rules import classify_families
    from .ingest import job_from_url

    job = job_from_url(url, client)
    existing = tracker.find_existing(job)
    if existing is not None:  # keep the stored id and the company name the tier list knows
        job.id, job.company = existing["id"], existing["company"] or job.company
    cfg = load_filters()
    res = apply_filters(job, cfg, needs_sponsorship=pipeline.needs_sponsorship(profile), tiers=load_tiers(),
                        established_only=False)
    warnings = []
    families = res.families or classify_families(job.title, cfg["role_families"])
    if not res.keep:
        warnings.append(f"filters would have dropped it ({res.reason}); kept because you added it")
    if job.closed:
        warnings.append("the posting says it's no longer accepting applications")
    if res.tier is not None and not res.tier.established:
        warnings.append(f"{job.company} is not on the established-company list (not blocking: you chose it)")
    run_id = tracker.latest_run_id() or tracker.start_run("add")
    tracker.upsert_job(job, run_id, True, "", families, res.flags, res.tier.tier if res.tier else None,
                       res.tier.note if res.tier else None)
    tracker.commit()
    if rescore:
        pipeline.rank(tracker, progress=lambda *_: None)
    return tracker.get(job.id), warnings


def _resume(row, profile):
    from .match.resume import load_resumes

    resumes = load_resumes(profile)
    if not resumes:
        raise RuntimeError("no resumes configured in config/profile.yaml")
    return resumes.get(row["variant"]) or next(iter(resumes.values()))


def process_job(tracker: Tracker, row, profile: dict, *, submit: bool = False, dry_run: bool = False,
                user_chose: bool = False, attended: bool = True, headless: bool = False,
                wait_seconds: int | None = None, notify: bool = False, say=print,
                fillers: dict | None = None, tailor: bool = True) -> RunResult:
    """Steps 1-5 above for one stored job. `fillers` lets tests swap in fake form/Workday fillers."""
    job = tracker.job(row)
    result = RunResult(row["id"], job.company, job.title, "error")
    kind = fill_kind(job)
    if kind is None:
        result.status, result.reason = "needs_you", "openapply can't pre-fill this site; apply by hand"
        return _finish(tracker, row, profile, result, notify, dry_run, say)
    resume = _resume(row, profile)
    gen = GENERATED_DIR / job.id
    if tailor and not (gen / "answers.json").exists():
        try:
            from .tailor.drafter import make_drafts

            make_drafts(job, resume, profile)
        except Exception as e:  # drafting is optional; never block filling on it
            say(f"(drafting skipped: {type(e).__name__}: {e})")
    answers = json.loads((gen / "answers.json").read_text()) if (gen / "answers.json").exists() else {}
    letter = (gen / "cover_letter.md").read_text() if (gen / "cover_letter.md").exists() else None

    s = settings(profile)
    want = submit and s.enabled
    pre, warnings = job_checks(row, tracker, profile, user_chose=user_chose) if (want or dry_run) else ([], [])
    result.warnings += warnings
    outcomes: list[Outcome] = []

    def on_ready(page, report) -> bool:
        if not (want or dry_run):
            return False
        o = try_submit(page, job, report, resume.pdf, pre, warnings, dry_run=dry_run or not s.enabled)
        outcomes.append(o)
        return o.status in ("submitted", "unconfirmed")

    saved = SavedAnswers.load()
    fillers = fillers or {}
    if kind == "workday":
        from .apply.workday import fill_workday

        report = fillers.get("workday", fill_workday)(
            job, profile, resume.pdf, saved, close_after=not attended, headless=headless,
            wait_seconds=wait_seconds, on_ready=on_ready)
    else:
        from .apply.filler import fill_application

        report = fillers.get("form", fill_application)(
            job, profile, resume.pdf, answers, letter, close_after=not attended, headless=headless,
            saved=saved, wait_seconds=wait_seconds, on_ready=on_ready)
    if outcomes:
        o = outcomes[-1]
        result.receipt = o.receipt
        if o.status == "submitted":
            result.status = "submitted"
        elif o.status == "would_submit":
            result.status, result.reason = "would_submit", "every guardrail passed (dry run: not submitted)"
        else:
            result.status, result.reason = "needs_you", o.reason
    elif submit and not s.enabled:
        result.status, result.reason = "needs_you", "auto_submit is off in config/profile.yaml; filled, not submitted"
    else:
        left = [f for f in report.not_filled if f.required and f.detail != "already had a value"]
        result.status = "filled"
        if report.stopped_at and report.stopped_at != "review":
            result.reason = f"stopped at {report.stopped_at}"
        elif left:
            result.reason = f"{len(left)} required field(s) left for you"
    if result.status != "submitted" and report.stopped_at == "signin":
        result.reason = "Workday sign-in needed (once per company): run `openapply apply " + job.id + "` and sign in"
    if report.error and result.status != "submitted":
        result.reason = (result.reason + "; " if result.reason else "") + report.error[:150]
    result.report = report
    return _finish(tracker, row, profile, result, notify, dry_run, say)


def _finish(tracker: Tracker, row, profile: dict, result: RunResult, notify: bool, dry_run: bool, say) -> RunResult:
    from .notify import send_imessage

    if result.status == "submitted" and not dry_run:
        tracker.mark(row["id"], "submitted")
        for line in sync_exports(tracker, profile):
            say(f"  export: {line}")
    else:
        if not dry_run and row["status"] in ("new", "shortlisted") and result.status in ("filled", "needs_you"):
            tracker.mark(row["id"], "filled")
        if result.status == "needs_you" and not dry_run:
            tracker.add_todo(row["id"], result.reason or "needs a human")
    result.message = alert_text(result)
    if notify:
        phone = str(profile.get("alert_phone", ""))
        try:
            say(send_imessage(phone, result.message, dry_run=dry_run))
        except Exception as e:
            say(f"(iMessage not sent: {type(e).__name__}: {e})")
    return result


def run_add(urls: list[str], *, dry_run: bool = False, headless: bool = False, notify: bool = True,
            wait_seconds: int | None = None,
            console=None, tracker: Tracker | None = None, profile: dict | None = None, fillers: dict | None = None,
            client=None) -> list[RunResult]:
    """`openapply add`: each link in turn, end to end. Unattended (close + todo when stuck) only when
    auto-submit is on; otherwise the browser waits for you like `openapply apply`."""
    from .config import load_profile

    say = (lambda m: console.print(m, markup=False)) if console else print
    tracker = tracker or Tracker()
    profile = profile if profile is not None else load_profile()
    auto = settings(profile).enabled
    if not auto:
        say("auto_submit is off in config/profile.yaml: each application is filled and left open for you.")
    results = []
    for url in urls:
        say(f"\n=== {url}")
        try:
            row, warnings = ingest_url(tracker, url, profile, client=client)
        except Exception as e:
            r = RunResult("-", "?", url, "error", f"couldn't add this link: {e}")
            say(r.reason)
            results.append(r)
            continue
        for w in warnings:
            say(f"warning: {w}")
        score = row["llm_score"] if row["llm_score"] is not None else row["score"]
        say(f"{row['company']} – {row['title']} ({row['id']}), score {score}, resume {row['variant']}")
        r = process_job(tracker, row, profile, submit=auto, dry_run=dry_run, user_chose=True,
                        attended=not auto, headless=headless, notify=False, say=say, fillers=fillers,
                        wait_seconds=wait_seconds)
        r.warnings = warnings + [w for w in r.warnings if w not in warnings]
        if not auto and not dry_run and r.status != "submitted":
            from .apply.session import ask_yes_no, stdin_is_tty

            if stdin_is_tty() and ask_yes_no("Did you click Submit?", default=False):
                tracker.mark(row["id"], "submitted")
                for line in sync_exports(tracker, profile):
                    say(f"  export: {line}")
                r.status, r.reason = "submitted", ""
            elif r.status != "needs_you":
                tracker.add_todo(row["id"], r.reason or "filled; waiting for you to review and submit")
                r.status = "needs_you"
                r.reason = r.reason or "filled; waiting for you to review and submit"
        r.message = alert_text(r)
        if notify:
            from .notify import send_imessage

            try:
                say(send_imessage(str(profile.get("alert_phone", "")), r.message, dry_run=dry_run))
            except Exception as e:
                say(f"(iMessage not sent: {type(e).__name__}: {e})")
        say(f"-> {r.status}" + (f": {r.reason}" if r.reason else ""))
        results.append(r)
    if console is not None:
        from .cli import _print_results

        _print_results(results)
    return results


def eligible_for_autopilot(tracker: Tracker, top: int, family: str | None = None) -> list:
    """Top-ranked established jobs we can fill, not applied to, not already on the todo list."""
    open_todo = {r["job_id"] for r in tracker.todos()}
    out = []
    for r in tracker.ranked(10_000, family=family, established_only=True):
        if r["status"] not in ("new", "shortlisted") or r["id"] in open_todo:
            continue
        if fill_kind(tracker.job(r)) is None or tracker.already_submitted(r["url"], r["id"]) is not None:
            continue
        out.append(r)
        if len(out) >= top:
            break
    return out


def run_autopilot(top: int, *, dry_run: bool = False, headless: bool = False, family: str | None = None,
                  notify: bool = True, console=None, tracker: Tracker | None = None, profile: dict | None = None,
                  fillers: dict | None = None) -> list[RunResult]:
    from .config import load_profile

    say = (lambda m: console.print(m, markup=False)) if console else print
    tracker = tracker or Tracker()
    profile = profile if profile is not None else load_profile()
    if not settings(profile).enabled and not dry_run:
        say("auto_submit is off in config/profile.yaml, so autopilot runs as a dry run (fills, checks, never submits).")
        dry_run = True
    rows = eligible_for_autopilot(tracker, top, family)
    say(f"Autopilot: {len(rows)} eligible job(s).")
    results = []
    for row in rows:
        say(f"\n=== {row['company']} – {row['title']} ({row['id']})")
        r = process_job(tracker, row, profile, submit=True, dry_run=dry_run, attended=False, headless=headless,
                        notify=notify and not dry_run, say=say, fillers=fillers)
        say(f"-> {r.status}" + (f": {r.reason}" if r.reason else ""))
        results.append(r)
    if console is not None:
        from .cli import _print_results

        _print_results(results)
    return results
