"""Command-line interface (typer).

    openapply discover                 fetch + filter + store (+ enrich descriptions)
    openapply rank --top 25 [--llm]    score kept jobs against your resumes
    openapply show <job_id>            details, flags, and match explanation for one job
    openapply tailor <job_id> [--llm]  draft cover letter + answers, run the fabrication check
    openapply apply <job_id>           open the form, pre-fill it, STOP for you to submit
    openapply mark <job_id> <status>   new|shortlisted|filled|submitted|interview|rejected|offer
    openapply export [--to csv|sheets|both]
    openapply daily [--dry-run]        discover -> filter -> rank -> diff -> iMessage alert
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from . import pipeline
from .config import GENERATED_DIR, PROJECT_ROOT, load_profile
from .tracker.db import STATUSES, Tracker

app = typer.Typer(add_completion=False, help="Human-in-the-loop internship application assistant. You click Submit.")
console = Console()


def _say(msg: str) -> None:
    console.print(msg)


def _row_or_exit(tracker: Tracker, job_id: str):
    row = tracker.get(job_id)
    if row is None:
        console.print(f"[red]No job with id {job_id!r}.[/red] Run `openapply rank` to see ids.")
        raise typer.Exit(1)
    return row


def _season_label() -> str:
    from .config import load_filters

    season = load_filters().get("season", {})
    return f"{season.get('term', 'summer').title()} {season.get('year', '')}"


def _print_discovery(stats: pipeline.PipelineStats) -> None:
    t = Table(title="Discovery", show_lines=False)
    t.add_column("source")
    t.add_column("postings", justify="right")
    for src, n in sorted(stats.per_source.items()):
        t.add_row(src, str(n))
    console.print(t)
    if stats.simplify_url:
        console.print(f"SimplifyJobs README: {stats.simplify_url}")
    for e in stats.errors:
        console.print(f"[yellow]warning:[/yellow] {e}")
    console.print(
        f"Fetched {stats.fetched_raw} postings -> {stats.unique} unique after de-dup -> "
        f"[bold]{stats.kept} internships kept[/bold] (US, {_season_label()}, wanted role families)."
    )
    console.print("Kept by family: " + ", ".join(f"{k}={v}" for k, v in sorted(stats.families.items())))
    console.print("Flags on kept jobs: " + ", ".join(f"{k}={v}" for k, v in sorted(stats.flags.items(), key=lambda x: -x[1])))
    top_drops = sorted(stats.drop_reasons.items(), key=lambda x: -x[1])[:6]
    console.print("Top drop reasons: " + ", ".join(f"{k} ({v})" for k, v in top_drops))
    console.print(f"Descriptions fetched for community-list jobs: {stats.enriched}")


def _print_ranked(rows) -> None:
    t = Table(title=f"Top {len(rows)} matches")
    for c in ("#", "id", "score", "company", "role", "location", "resume", "flags"):
        t.add_column(c, overflow="fold")
    for i, r in enumerate(rows, 1):
        score = r["llm_score"] if r["llm_score"] is not None else r["score"]
        flags = ",".join(f for f in json.loads(r["flags"]) if f != "season_unconfirmed")
        t.add_row(str(i), r["id"], str(score), r["company"], r["title"], (r["location"] or "")[:40],
                  r["variant"] or "", flags)
    console.print(t)


@app.command()
def discover(enrich_limit: int = typer.Option(400, help="Max descriptions to fetch for community-list jobs.")):
    """Fetch postings from every source, filter them, and store them."""
    tracker = Tracker()
    stats = pipeline.discover_and_filter(tracker, "discover", enrich_limit, progress=_say)
    _print_discovery(stats)


@app.command()
def rank(top: int = typer.Option(25, help="How many to show."),
         llm: bool = typer.Option(False, "--llm", help="Rerank the top N with Claude (needs ANTHROPIC_API_KEY)."),
         family: str = typer.Option(None, help="Only one role family: pm | swe | ds | ml."),
         shortlist: bool = typer.Option(False, help="Mark the shown jobs as 'shortlisted' (only if still 'new').")):
    """Score kept jobs against your resumes and show the best matches."""
    tracker = Tracker()
    pipeline.rank(tracker, use_llm=llm, llm_top=top, progress=_say)
    rows = tracker.ranked(top, family=family)
    _print_ranked(rows)
    if shortlist:
        for r in rows:
            if r["status"] == "new":
                tracker.mark(r["id"], "shortlisted")
        console.print(f"Shortlisted {len(rows)} jobs.")


@app.command()
def show(job_id: str):
    """Show everything openapply knows about one job."""
    tracker = Tracker()
    r = _row_or_exit(tracker, job_id)
    job = tracker.job(r)
    console.print(f"[bold]{job.company} - {job.title}[/bold]  ({r['id']})")
    console.print(f"Location: {job.location}\nLink: {job.url}\nATS: {job.ats}  Sources: {', '.join(job.sources)}")
    console.print(f"Status: {r['status']}  Families: {r['families']}  Flags: {r['flags']}")
    console.print(f"Score: {r['score']} (resume: {r['variant']})  {r['explanation'] or ''}")
    if r["llm_score"] is not None:
        console.print(f"Claude: {r['llm_score']} - {r['llm_reason']}")
    console.print(f"\nDescription: {len(job.description)} chars" + (f"\n{job.description[:800]}..." if job.description else ""))


def _resume_for(row, profile):
    from .match.resume import load_resumes

    resumes = load_resumes(profile)
    if not resumes:
        console.print("[red]No resumes configured.[/red]")
        raise typer.Exit(1)
    return resumes.get(row["variant"]) or next(iter(resumes.values()))


@app.command()
def tailor(job_id: str, llm: bool = typer.Option(False, "--llm", help="Draft with Claude instead of extractively.")):
    """Draft a cover letter and answers, then check them for anything not in your resume."""
    from .tailor.drafter import make_drafts

    tracker, profile = Tracker(), load_profile()
    row = _row_or_exit(tracker, job_id)
    job = tracker.job(row)
    resume = _resume_for(row, profile)
    if llm and not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
        console.print("[yellow]No ANTHROPIC_API_KEY; using extractive drafts.[/yellow]")
        llm = False
    d = make_drafts(job, resume, profile, use_llm=llm)
    console.print(f"[bold]Drafts for {job.company} - {job.title}[/bold] (resume: {resume.variant}, mode: {d.mode})\n")
    console.print(d.cover_letter)
    console.print("\n[bold]Fabrication check[/bold]")
    console.print(d.report.summary(), style="green" if d.report.clean else "red")
    for f in d.files:
        console.print(f"saved {f.relative_to(PROJECT_ROOT)}")


@app.command()
def apply(job_id: str,
          close_after: bool = typer.Option(False, help="Close the browser right after filling (for testing)."),
          headless: bool = typer.Option(False, help="Run without a visible window (testing only).")):
    """Open the application, pre-fill it, and STOP. You review and click Submit."""
    from .apply.filler import fill_application

    tracker, profile = Tracker(), load_profile()
    row = _row_or_exit(tracker, job_id)
    job = tracker.job(row)
    if job.ats == "other":
        console.print("[yellow]This posting isn't on Greenhouse/Lever/Ashby; openapply can't pre-fill it.[/yellow]")
        console.print(f"Open it yourself: {job.url}")
        raise typer.Exit(1)
    resume = _resume_for(row, profile)
    gen = GENERATED_DIR / job.id
    answers = json.loads((gen / "answers.json").read_text()) if (gen / "answers.json").exists() else {}
    letter = (gen / "cover_letter.md").read_text() if (gen / "cover_letter.md").exists() else None
    if not answers:
        console.print("[dim]No drafted answers yet (run `openapply tailor` first to fill long-answer boxes).[/dim]")
    console.print(f"Opening {job.url}\nResume: {resume.pdf}")
    report = fill_application(job, profile, resume.pdf, answers, letter, close_after=close_after, headless=headless)
    if close_after:
        console.print(f"Filled {len(report.filled)}, left {len(report.not_filled)}; captcha={report.captcha_present}; "
                      f"log: generated/{job.id}/fill_log.json" + (f"; error: {report.error}" if report.error else ""))
    if row["status"] in ("new", "shortlisted"):
        tracker.mark(row["id"], "filled")
    if not close_after:
        if typer.confirm("Did you click Submit?", default=False):
            tracker.mark(row["id"], "submitted")
            console.print("Marked submitted.")


@app.command()
def mark(job_id: str, status: str = typer.Argument(..., help="|".join(STATUSES))):
    """Record a status change (e.g. submitted, interview, offer)."""
    tracker = Tracker()
    row = _row_or_exit(tracker, job_id)
    tracker.mark(row["id"], status)
    console.print(f"{row['company']} - {row['title']}: {status}")


@app.command()
def export(to: str = typer.Option("csv", help="csv | sheets | both"),
           out: Path = typer.Option(PROJECT_ROOT / "generated" / "applications.csv", help="CSV path."),
           all_statuses: bool = typer.Option(False, help="Include shortlisted/filled, not just applied."),
           ):
    """Export your applications in the Google Sheet's columns (CSV and/or the live sheet)."""
    from .tracker.export import CsvExporter, GoogleSheetsExporter, sheet_rows

    tracker, profile = Tracker(), load_profile()
    statuses = STATUSES[1:] if all_statuses else ["submitted", "interview", "rejected", "offer"]
    sheets_cfg = profile.get("sheets") or {}
    yes = sheets_cfg.get("yes_value", "Yes")
    rows = sheet_rows(tracker.by_status(statuses), yes=yes)
    exporters = []
    if to in ("csv", "both"):
        exporters.append(CsvExporter(out))
    if to in ("sheets", "both"):
        exporters.append(GoogleSheetsExporter(os.environ.get("GOOGLE_SHEET_ID") or sheets_cfg.get("sheet_id", ""),
                                              sheets_cfg.get("worksheet") or None, yes))
    for e in exporters:
        console.print(e.export(rows))


@app.command()
def daily(dry_run: bool = typer.Option(False, "--dry-run", help="Print the alert instead of sending it."),
          llm: bool = typer.Option(False, "--llm", help="Also rerank with Claude."),
          min_score: int = typer.Option(None, help="Only alert on new jobs scoring at least this (default from profile)."),
          enrich_limit: int = typer.Option(400)):
    """discover -> filter -> rank -> find NEW roles since last run -> iMessage alert."""
    from .notify import build_alert, send_imessage

    tracker, profile = Tracker(), load_profile()
    stats = pipeline.discover_and_filter(tracker, "daily", enrich_limit, progress=_say)
    pipeline.rank(tracker, use_llm=llm, progress=_say)
    threshold = min_score if min_score is not None else int((profile.get("alerts") or {}).get("min_score", 0))
    new_rows = [r for r in tracker.new_in_run(stats.run_id) if (r["score"] or 0) >= threshold]
    new_ids = {r["id"] for r in new_rows}
    fresh = [r for r in tracker.ranked(10_000) if r["id"] in new_ids]
    baseline = tracker.previous_run_count(stats.run_id) == 0
    top = [{"company": r["company"], "title": r["title"], "url": r["url"],
            "score": r["llm_score"] if r["llm_score"] is not None else r["score"]} for r in fresh[:5]]
    msg = build_alert(len(fresh), top, baseline=baseline)
    console.print(f"run {stats.run_id}: {stats.kept} kept, {len(fresh)} new (score >= {threshold})")
    console.print(send_imessage(str(profile.get("alert_phone", "")), msg, dry_run=dry_run), markup=False)


if __name__ == "__main__":
    app()
