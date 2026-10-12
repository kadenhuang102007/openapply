"""Command-line interface (typer).

    openapply discover                 fetch + filter + store (+ enrich descriptions)
    openapply rank --top 25 [--llm]    score kept jobs against your resumes
                 [--established-only | --include-startups]   (default: profile's established_only)
    openapply show <job_id>            details, flags, and match explanation for one job
    openapply tailor <job_id> [--llm]  draft cover letter + answers, run the fabrication check
    openapply apply <job_id>           open the form, pre-fill it, STOP for you to submit
                 [--wait-seconds N] [--learn] [--submit]   (Greenhouse/Lever/Ashby/Workday)
    openapply add <url> [<url> ...]    ingest a posting you found -> score -> tailor -> fill
                                       (-> auto-submit if on and every guardrail passes) -> text you
    openapply autopilot --top N        the same for your top N eligible ranked jobs
    openapply todo                     jobs where add/autopilot stopped and needs you, with the reason
    openapply mark <job_id> <status>   new|shortlisted|filled|submitted|interview|rejected|offer
    openapply export [--to csv|sheets|both]
    openapply daily [--dry-run]        discover -> filter -> rank -> diff -> iMessage alert
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

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
    tiers = ", ".join(f"{_TIER_TAG.get(k, k)}={v}" for k, v in sorted(stats.tiers.items(), key=lambda x: -x[1]))
    console.print(f"Established companies: {stats.established} of {stats.kept_any_company} kept ({tiers}).")
    if stats.established_only:
        console.print("established_only is on: the rest are stored with reason 'not an established company' "
                      "(see them with `openapply rank --include-startups`).")
    console.print(f"Descriptions fetched for community-list jobs: {stats.enriched}")


_TIER_TAG = {"big_tech": "big tech", "top_tech": "top tech", "quant": "quant", "finance": "finance",
             "enterprise": "enterprise", "faang_plus": "FAANG+"}


def _established_view(flag: bool | None, profile: dict) -> bool:
    """The CLI flag wins; otherwise the profile's `established_only` setting."""
    return pipeline.established_only(profile) if flag is None else flag


def _print_ranked(rows) -> None:
    t = Table(title=f"Top {len(rows)} matches")
    for c in ("#", "id", "score", "company", "tier", "role", "location", "resume", "flags"):
        t.add_column(c, overflow="fold")
    for i, r in enumerate(rows, 1):
        score = r["llm_score"] if r["llm_score"] is not None else r["score"]
        flags = ",".join(f for f in json.loads(r["flags"]) if f != "season_unconfirmed")
        tier = _TIER_TAG.get(r["company_tier"], "-") if r["company_tier"] else "-"
        t.add_row(str(i), r["id"], str(score), r["company"], tier, r["title"], (r["location"] or "")[:40],
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
         shortlist: bool = typer.Option(False, help="Mark the shown jobs as 'shortlisted' (only if still 'new')."),
         established: Optional[bool] = typer.Option(
             None, "--established-only/--include-startups",
             help="Only established companies (config/company_tiers.yaml), or startups too. "
                  "Default: `established_only` in your profile.")):
    """Score kept jobs against your resumes and show the best matches."""
    tracker = Tracker()
    view = _established_view(established, load_profile())
    pipeline.rank(tracker, use_llm=llm, llm_top=top, progress=_say, established=view)
    rows = tracker.ranked(top, family=family, established_only=view)
    _print_ranked(rows)
    if view:
        console.print("[dim]Established companies only. Add --include-startups to see everyone.[/dim]")
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
    if r["filter_reason"]:
        console.print(f"Filter: {r['filter_reason']}")
    console.print(f"Company: {r['tier_note'] or 'tier unknown (run `openapply discover`)'}")
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


def _offer_to_learn(report, company: str, learn: bool) -> None:
    """Offer to save the choices you made on the page as saved answers (config/answers.yaml)."""
    from .apply.answers import ANSWERS_PATH, SavedAnswers, learn_candidates, save_answers
    from .apply.session import ask_yes_no, stdin_is_tty

    captured = list((getattr(report, "captured", None) or {}).values())
    items = learn_candidates(captured, SavedAnswers.load(), company)
    if not items:
        return
    console.print(f"\n[bold]{len(items)} answer(s) you picked aren't saved yet:[/bold]")
    for it in items:
        was = f"  (was {it.previous!r})" if it.previous else ""
        console.print(f"  {it.label[:80]}  ->  {it.value!r}{was}", markup=False)
    if learn or (stdin_is_tty() and ask_yes_no(f"Save them to {ANSWERS_PATH.relative_to(PROJECT_ROOT)}?", False)):
        path = save_answers(items)
        console.print(f"Saved {len(items)} answer(s) to {path.relative_to(PROJECT_ROOT)}.")
    elif not stdin_is_tty():
        console.print("[dim]Not saved (no terminal to ask). Re-run with --learn to save them.[/dim]")


@app.command()
def apply(job_id: str,
          close_after: bool = typer.Option(False, help="Close the browser right after filling (for testing)."),
          headless: bool = typer.Option(False, help="Run without a visible window (testing only)."),
          wait_seconds: Optional[int] = typer.Option(
              None, "--wait-seconds", help="Keep the browser open N seconds, then close (for non-interactive use)."),
          learn: bool = typer.Option(False, "--learn", help="Save the choices you made as saved answers without asking."),
          submit: bool = typer.Option(False, "--submit",
                                      help="Auto-submit if `auto_submit: true` in your profile AND every guardrail passes.")):
    """Open the application, pre-fill it, and STOP. You review and click Submit (unless --submit and all guardrails pass)."""
    from .apply.session import ask_yes_no, stdin_is_tty
    from .runner import fill_kind, process_job

    tracker, profile = Tracker(), load_profile()
    row = _row_or_exit(tracker, job_id)
    job = tracker.job(row)
    kind = fill_kind(job)
    if kind is None:
        console.print("[yellow]This posting isn't on Greenhouse/Lever/Ashby/Workday; openapply can't pre-fill it.[/yellow]")
        console.print(f"Open it yourself: {job.url}")
        raise typer.Exit(1)
    if kind == "workday":
        console.print("Workday: using the saved browser profile in data/browser-profile/ (sign in once per company).")
    elif not (GENERATED_DIR / job.id / "answers.json").exists():
        console.print("[dim]No drafted answers yet (run `openapply tailor` first to fill long-answer boxes).[/dim]")
    if wait_seconds is None and not close_after and not stdin_is_tty():
        console.print("[dim]stdin is not a terminal: the browser stays open until you close it "
                      "(or pass --wait-seconds N).[/dim]")
    console.print(f"Opening {job.url}")
    result = process_job(tracker, row, profile, submit=submit, attended=not close_after, headless=headless, tailor=False,
                         wait_seconds=wait_seconds, say=lambda m: console.print(m, markup=False))
    report = result.report
    for w in result.warnings:
        console.print(f"[yellow]warning:[/yellow] {w}")
    if close_after and report is not None:
        console.print(f"Filled {len(report.filled)}, left {len(report.not_filled)}; captcha={report.captcha_present}; "
                      + (f"stopped at: {report.stopped_at}; " if report.stopped_at else "")
                      + f"log: generated/{job.id}/fill_log.json" + (f"; error: {report.error}" if report.error else ""))
    if result.status == "submitted":
        console.print(f"[green]Submitted.[/green] Receipt: {result.receipt}")
    elif submit:
        console.print(f"[yellow]Not auto-submitted:[/yellow] {result.reason}")
    if report is not None and not close_after:
        _offer_to_learn(report, job.company, learn)
    if result.status != "submitted" and not close_after and stdin_is_tty():
        if ask_yes_no("Did you click Submit?", default=False):
            tracker.mark(row["id"], "submitted")
            console.print("Marked submitted.")


def _print_results(results) -> None:
    t = Table(title="Results")
    for c in ("id", "company", "role", "result", "reason"):
        t.add_column(c, overflow="fold")
    for r in results:
        style = {"submitted": "green", "would_submit": "cyan"}.get(r.status, "yellow")
        t.add_row(r.job_id, r.company, r.title[:50], f"[{style}]{r.status}[/{style}]", r.reason[:120])
    console.print(t)
    sub = sum(r.status == "submitted" for r in results)
    console.print(f"Submitted {sub}; needs you {sum(r.status == 'needs_you' for r in results)}; "
                  f"other {len(results) - sub - sum(r.status == 'needs_you' for r in results)}. "
                  "See `openapply todo` for what needs you.")


@app.command()
def add(urls: list[str] = typer.Argument(..., help="One or more Greenhouse/Lever/Ashby/Workday posting links."),
        dry_run: bool = typer.Option(False, "--dry-run",
                                     help="Do everything except submit; print the text message instead of sending it."),
        headless: bool = typer.Option(False, help="Run the browser without a window."),
        no_text: bool = typer.Option(False, "--no-text", help="Don't send the iMessage."),
        wait_seconds: Optional[int] = typer.Option(
            None, "--wait-seconds", help="When the browser waits for you, close it after N seconds.")):
    """Ingest posting links you found -> score -> tailor -> fill -> (auto-submit if on and safe) -> text you."""
    from .runner import run_add

    run_add(urls, dry_run=dry_run, headless=headless, notify=not no_text, console=console,
            wait_seconds=wait_seconds)


@app.command()
def autopilot(top: int = typer.Option(10, help="How many of your top-ranked eligible jobs to process."),
              dry_run: bool = typer.Option(False, "--dry-run", help="Fill and check guardrails, never submit or text."),
              headless: bool = typer.Option(False, help="Run the browser without a window."),
              family: str = typer.Option(None, help="Only one role family: pm | swe | ds | ml."),
              no_text: bool = typer.Option(False, "--no-text", help="Don't send iMessages.")):
    """Run `apply --submit` over your top N eligible jobs; print submitted vs needs-you."""
    from .runner import run_autopilot

    run_autopilot(top, dry_run=dry_run, headless=headless, family=family, notify=not no_text, console=console)


@app.command()
def todo(done: Optional[str] = typer.Option(None, "--done", help="Clear one job from the list.")):
    """Jobs where add/autopilot stopped and needs you (sign in, CAPTCHA, an unanswered question...)."""
    tracker = Tracker()
    if done:
        row = _row_or_exit(tracker, done)
        tracker.resolve_todo(row["id"])
        console.print(f"Cleared {row['id']}.")
        return
    rows = tracker.todos()
    if not rows:
        console.print("Nothing needs you. 🎉")
        return
    t = Table(title=f"Action required ({len(rows)})")
    for c in ("id", "company", "role", "why", "since"):
        t.add_column(c, overflow="fold")
    for r in rows:
        t.add_row(r["job_id"], r["company"] or "", (r["title"] or "")[:50], r["reason"], r["at"][:16])
    console.print(t)
    console.print("Clear one with `openapply apply <id>` (it opens the form so you can finish), "
                  "then `openapply mark <id> submitted`, or `openapply todo --done <id>`.")


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
    view = pipeline.established_only(profile)
    stats = pipeline.discover_and_filter(tracker, "daily", enrich_limit, progress=_say)
    pipeline.rank(tracker, use_llm=llm, progress=_say, established=view)
    threshold = min_score if min_score is not None else int((profile.get("alerts") or {}).get("min_score", 0))
    new_rows = [r for r in tracker.new_in_run(stats.run_id, established_only=view) if (r["score"] or 0) >= threshold]
    new_ids = {r["id"] for r in new_rows}
    fresh = [r for r in tracker.ranked(10_000, established_only=view) if r["id"] in new_ids]
    baseline = tracker.previous_run_count(stats.run_id) == 0
    top = [{"company": r["company"], "title": r["title"], "url": r["url"],
            "score": r["llm_score"] if r["llm_score"] is not None else r["score"]} for r in fresh[:5]]
    msg = build_alert(len(fresh), top, baseline=baseline)
    console.print(f"run {stats.run_id}: {stats.kept} kept, {len(fresh)} new (score >= {threshold})")
    console.print(send_imessage(str(profile.get("alert_phone", "")), msg, dry_run=dry_run), markup=False)


if __name__ == "__main__":
    app()
