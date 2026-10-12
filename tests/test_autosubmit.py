"""Auto-submit guardrails (one test per guardrail), receipts, the add/todo flow, and non-TTY waiting.

Every browser test loads local HTML (set_content or routed fixtures) with all other
requests aborted. Nothing here submits anything real or sends an iMessage.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

import openapply.apply.autosubmit as asub
from openapply.apply.autosubmit import job_checks, page_checks, receipts_today, settings, try_submit
from openapply.apply.filler import FillReport
from openapply.apply.session import Waiter, ask_yes_no
from openapply.models import Job
from openapply.tracker.db import Tracker

URL = "https://job-boards.greenhouse.io/acme/jobs/123"
FIX = Path(__file__).parent / "fixtures" / "workday"

FORM = """<!doctype html><html><body>
<form id="f" onsubmit="event.preventDefault(); window.__submitted = true;
      document.body.innerHTML = '<h1>Thank you for applying!</h1>';">
  <label for="fn">First Name *</label><input id="fn" required value="{fn}">
  <label for="em">Email *</label><input id="em" type="email" required value="jane@example.edu">
  <label for="why">Why Acme?</label><textarea id="why">{why}</textarea>
  <fieldset><legend>Are you legally authorized to work in the US? *</legend>
    <input type="radio" name="auth" id="a1" required {checked}><label for="a1">Yes</label>
    <input type="radio" name="auth" id="a2"><label for="a2">No</label></fieldset>
  {captcha}
  <button type="submit">Submit Application</button>
</form></body></html>"""


def form_html(fn="Jane", why="", checked="checked", captcha=""):
    return FORM.format(fn=fn, why=why, checked=checked, captcha=captcha)


PROFILE = {"auto_submit": True, "auto_submit_min_score": 60, "auto_submit_daily_limit": 2}


@pytest.fixture
def gen(tmp_path, monkeypatch):
    d = tmp_path / "generated"
    d.mkdir()
    monkeypatch.setattr(asub, "GENERATED_DIR", d)
    return d


@pytest.fixture
def tracker(tmp_path):
    return Tracker(tmp_path / "t.db")


def store(tracker, job_id="gh-123", url=URL, tier="top_tech", score=80, status=None):
    job = Job(id=job_id, company="Acme", title="Software Engineer Intern", url=url, ats="greenhouse",
              ats_board="acme", ats_job_id=job_id.split("-")[-1])
    tracker.upsert_job(job, 1, True, "", ["swe"], [], tier, "note")
    tracker.set_score(job_id, score, "swe_data", "")
    tracker.commit()
    if status:
        tracker.mark(job_id, status)
    return tracker.get(job_id), job


@pytest.fixture
def page(browser):
    ctx = browser.new_context()
    ctx.route("**/*", lambda route: route.abort())
    pg = ctx.new_page()
    yield pg
    ctx.close()


# ----------------------------------------------------------------------------- settings

def test_auto_submit_is_off_unless_profile_says_so():
    assert settings({}).enabled is False
    assert settings({"auto_submit": "false"}).enabled is False
    s = settings({"auto_submit": True})
    assert s.enabled and s.min_score == 60 and s.daily_limit == 10


def test_example_profile_ships_with_auto_submit_off():
    import yaml

    example = yaml.safe_load((Path(__file__).parents[1] / "config" / "profile.yaml.example").read_text())
    assert example["auto_submit"] is False and example["use_saved_answers"] is False


# ----------------------------------------------------------------------------- job guardrails

def test_guardrail_established_company(tracker, gen):
    row, _ = store(tracker, tier=None)
    reasons, warnings = job_checks(row, tracker, PROFILE, gen)
    assert any("established" in r for r in reasons)
    reasons, warnings = job_checks(row, tracker, PROFILE, gen, user_chose=True)  # pasted link: warn only
    assert not any("established" in r for r in reasons) and any("established" in w for w in warnings)


def test_guardrail_min_score(tracker, gen):
    row, _ = store(tracker, score=59)
    assert any("below auto_submit_min_score" in r for r in job_checks(row, tracker, PROFILE, gen)[0])
    row, _ = store(tracker, score=60)
    assert job_checks(row, tracker, PROFILE, gen)[0] == []


def test_guardrail_daily_cap(tracker, gen):
    row, _ = store(tracker)
    today = datetime.now().date().isoformat()
    for i in range(2):
        (gen / f"gh-{i}").mkdir()
        (gen / f"gh-{i}" / "receipt.json").write_text(json.dumps({"submitted_at": today + "T09:00:00"}))
    assert receipts_today(gen) == 2
    assert any("daily auto-submit limit" in r for r in job_checks(row, tracker, PROFILE, gen)[0])


def test_guardrail_not_already_submitted_by_url(tracker, gen):
    store(tracker, job_id="gh-999", url=URL + "?gh_src=abc", status="submitted")  # same link, other id
    row, _ = store(tracker)
    assert any("already applied" in r for r in job_checks(row, tracker, PROFILE, gen)[0])


def test_guardrail_existing_receipt_blocks_resubmit(tracker, gen):
    row, _ = store(tracker)
    (gen / "gh-123").mkdir()
    (gen / "gh-123" / "receipt.json").write_text(json.dumps({"submitted_at": "2020-01-01T00:00:00"}))
    assert any("receipt already exists" in r for r in job_checks(row, tracker, PROFILE, gen)[0])


def test_guardrail_fabrication_check(tracker, gen):
    row, _ = store(tracker)
    d = gen / "gh-123"
    d.mkdir()
    (d / "answers.json").write_text("{}")
    assert any("no fabrication check" in r for r in job_checks(row, tracker, PROFILE, gen)[0])
    (d / "fabrication_check.txt").write_text("FLAGGED: 3 numbers not in your resume")
    assert any("did not pass" in r for r in job_checks(row, tracker, PROFILE, gen)[0])
    (d / "fabrication_check.txt").write_text("mode: extractive\nPASS: every number and name traces back")
    assert job_checks(row, tracker, PROFILE, gen)[0] == []


# ----------------------------------------------------------------------------- page guardrails

def test_guardrail_required_fields(page):
    page.set_content(form_html(fn="", checked=""))
    snap = page.evaluate(asub.FORM_SNAPSHOT_JS)
    reasons = page_checks(snap, False)
    assert reasons and "2 required field(s) empty" in reasons[0]
    assert "First Name" in reasons[0] and "legally authorized" in reasons[0]


def test_guardrail_captcha(page):
    page.set_content(form_html(captcha='<div class="g-recaptcha"></div>'))
    assert page.evaluate(asub.BOT_CHECK_JS) is True
    assert any("CAPTCHA" in r for r in page_checks(page.evaluate(asub.FORM_SNAPSHOT_JS), True))


def test_guardrail_your_words_placeholder(page):
    page.set_content(form_html(why="I love it. [YOUR WORDS: why Acme specifically]"))
    assert any("[YOUR WORDS" in r for r in page_checks(page.evaluate(asub.FORM_SNAPSHOT_JS), False))


def test_guardrail_workday_only_from_review_without_errors():
    rep = FillReport("x-1", URL, ats="workday", stopped_at="application_questions")
    assert any("didn't reach Review" in r for r in page_checks([], False, rep))
    rep.stopped_at = "review"
    assert page_checks([], False, rep) == []
    assert any("errors" in r for r in page_checks([], False, rep, {"errors": ["Error: field required"]}))


# ----------------------------------------------------------------------------- submitting + receipts

def _job():
    return Job(id="gh-123", company="Acme", title="SWE Intern", url=URL, ats="greenhouse")


def test_submits_only_when_every_guardrail_passes_and_keeps_a_receipt(page, gen, tmp_path):
    page.set_content(form_html())
    rep = FillReport("gh-123", URL, ats="greenhouse")
    out = try_submit(page, _job(), rep, tmp_path / "r.pdf", [], [])
    assert out.status == "submitted", out.reasons
    assert page.evaluate("() => window.__submitted") is True
    receipt = json.loads((gen / "gh-123" / "receipt.json").read_text())
    assert receipt["confirmed"] and "Thank you for applying" in receipt["confirmation"]
    assert {"label": "First Name", "value": "Jane"} in receipt["fields"]
    assert receipt["resume"].endswith("r.pdf") and receipt["submitted_at"]
    assert all(Path(p).exists() for p in receipt["screenshots"])


@pytest.mark.parametrize("html,pre", [
    (form_html(fn=""), []),                                          # required field empty
    (form_html(captcha='<iframe src="https://www.google.com/recaptcha/api2"></iframe>'), []),  # captcha
    (form_html(why="[YOUR WORDS: ...]"), []),                        # placeholder
    (form_html(), ["match score 40 is below auto_submit_min_score 60"]),  # job-level guardrail
])
def test_any_failed_guardrail_means_no_click(page, gen, html, pre):
    page.set_content(html)
    out = try_submit(page, _job(), FillReport("gh-123", URL, ats="greenhouse"), None, pre, [])
    assert out.status == "needs_you" and out.reasons
    assert page.evaluate("() => !!window.__submitted") is False
    assert not (gen / "gh-123" / "receipt.json").exists()


def test_dry_run_never_clicks(page, gen):
    page.set_content(form_html())
    out = try_submit(page, _job(), FillReport("gh-123", URL, ats="greenhouse"), None, [], [], dry_run=True)
    assert out.status == "would_submit"
    assert page.evaluate("() => !!window.__submitted") is False


def _serve_workday(pg):
    widgets = (FIX / "widgets.js").read_text()

    def serve(route):
        name = route.request.url.split("?")[0].rsplit("/", 1)[-1]
        html = (FIX / name).read_text().replace("</body>", f"<script>{widgets}</script></body>")
        route.fulfill(status=200, content_type="text/html", body=html)

    pg.route("https://acme.wd5.myworkdayjobs.test/**", serve)


def test_workday_submit_from_review_only(page, gen):
    import openapply.apply.workday as wd

    _serve_workday(page)
    job = Job(id="x-wd", company="Acme", title="SWE Intern", url="https://acme.wd5.myworkdayjobs.test/job",
              ats="workday")
    page.goto("https://acme.wd5.myworkdayjobs.test/self_identify.html")
    out = try_submit(page, job, FillReport("x-wd", job.url, ats="workday", stopped_at="self_identify"), None, [], [])
    assert out.status == "needs_you"
    with pytest.raises(RuntimeError):
        asub.click_submit(page, "workday")  # not on Review: refused even if called directly
    page.goto("https://acme.wd5.myworkdayjobs.test/review.html")
    assert wd.read_state(page)[1] == "review"
    out = try_submit(page, job, FillReport("x-wd", job.url, ats="workday", stopped_at="review"), None, [], [])
    assert out.status == "submitted", out.reasons
    assert "Congratulations" in json.loads((gen / "x-wd" / "receipt.json").read_text())["confirmation"]


# ----------------------------------------------------------------------------- add / todo / alerts

def _fake_form_filler(browser, html):
    def filler(job, profile, resume_pdf, answers=None, letter=None, close_after=False, headless=False,
               saved=None, wait_seconds=None, on_ready=None):
        ctx = browser.new_context()
        ctx.route("**/*", lambda route: route.abort())
        pg = ctx.new_page()
        pg.set_content(html)
        rep = FillReport(job.id, job.url, ats=job.ats)
        on_ready(pg, rep)
        ctx.close()
        return rep
    return filler


def _profile(tmp_path, auto=True):
    tex = tmp_path / "r.tex"
    tex.write_text("Python SQL pandas")
    pdf = tmp_path / "r.pdf"
    pdf.write_bytes(b"%PDF")
    return {"auto_submit": auto, "auto_submit_min_score": 60, "auto_submit_daily_limit": 10,
            "alert_phone": "+15555550100",
            "resumes": {"variants": {"swe_data": {"path": str(tex), "pdf": str(pdf), "families": ["swe"]}}}}


def test_add_submits_marks_and_texts_when_safe(browser, tracker, gen, tmp_path, monkeypatch):
    import openapply.runner as runner

    monkeypatch.setattr(runner, "GENERATED_DIR", gen)
    monkeypatch.setattr(runner, "sync_exports", lambda t, p: ["csv ok"])
    row, _ = store(tracker)
    sent = []
    monkeypatch.setattr("openapply.notify.send_imessage", lambda phone, msg, dry_run=True: sent.append((msg, dry_run)) or "ok")
    r = runner.process_job(tracker, row, _profile(tmp_path), submit=True, attended=False, notify=True,
                           tailor=False, fillers={"form": _fake_form_filler(browser, form_html())}, say=lambda m: None)
    assert r.status == "submitted", r.reason
    assert tracker.get("gh-123")["status"] == "submitted"
    assert sent == [("Applied: Acme – Software Engineer Intern ✅", False)]


def test_add_stops_and_queues_todo_when_a_guardrail_fails(browser, tracker, gen, tmp_path, monkeypatch):
    import openapply.runner as runner

    monkeypatch.setattr(runner, "GENERATED_DIR", gen)
    row, _ = store(tracker)
    r = runner.process_job(tracker, row, _profile(tmp_path), submit=True, attended=False, tailor=False,
                           fillers={"form": _fake_form_filler(browser, form_html(captcha='<div class="h-captcha"></div>'))},
                           say=lambda m: None)
    assert r.status == "needs_you" and "CAPTCHA" in r.reason
    assert r.message.startswith("Needs you: Acme – Software Engineer Intern — ")
    todo = tracker.todos()
    assert [t["job_id"] for t in todo] == ["gh-123"] and "CAPTCHA" in todo[0]["reason"]
    tracker.mark("gh-123", "submitted")  # finishing it by hand clears the queue
    assert tracker.todos() == []


def test_auto_submit_off_never_submits(browser, tracker, gen, tmp_path, monkeypatch):
    import openapply.runner as runner

    monkeypatch.setattr(runner, "GENERATED_DIR", gen)
    row, _ = store(tracker)
    r = runner.process_job(tracker, row, _profile(tmp_path, auto=False), submit=True, attended=False, tailor=False,
                           fillers={"form": _fake_form_filler(browser, form_html())}, say=lambda m: None)
    assert r.status == "needs_you" and "auto_submit is off" in r.reason
    assert tracker.get("gh-123")["status"] != "submitted"


def test_add_ingests_a_pasted_link_offline(tracker, tmp_path, monkeypatch, browser):
    import openapply.pipeline as pipeline
    import openapply.runner as runner

    class FakeClient:
        def get_json(self, url):
            assert url.startswith("https://boards-api.greenhouse.io/v1/boards/acme/jobs/123")
            return {"id": 123, "title": "Software Engineer Intern, Summer 2027", "company_name": "Acme Robotics",
                    "location": {"name": "San Francisco, CA"}, "content": "&lt;p&gt;Python and SQL&lt;/p&gt;",
                    "absolute_url": URL}

        def close(self):
            pass

    def fake_rank(tr, **kw):
        tr.set_score("gh-123", 75, "swe_data", "")
        tr.commit()

    monkeypatch.setattr(pipeline, "rank", fake_rank)
    monkeypatch.setattr(runner, "GENERATED_DIR", tmp_path)
    results = runner.run_add([URL, "https://example.com/careers/1"], dry_run=True, notify=False, tracker=tracker,
                             profile=_profile(tmp_path), client=FakeClient(),
                             fillers={"form": _fake_form_filler(browser, form_html())})
    first, second = results
    assert first.company == "Acme Robotics" and first.status == "would_submit"
    assert any("established" in w for w in first.warnings)  # warns, doesn't block a link you chose
    assert second.status == "error" and "not a Greenhouse, Lever, Ashby, or Workday" in second.reason
    assert tracker.get("gh-123")["status"] == "new"  # dry run changes nothing


def test_workday_links_are_ingested(monkeypatch):
    from openapply.ingest import company_from_workday, parse_workday, workday_api_url

    url = "https://amgen.wd1.myworkdayjobs.com/en-US/careers/job/United-States---Remote/SWE-Intern_R-255719"
    assert workday_api_url(url) == ("https://amgen.wd1.myworkdayjobs.com/wday/cxs/amgen/careers/job/"
                                    "United-States---Remote/SWE-Intern_R-255719")
    job = parse_workday(url, {"jobPostingInfo": {"title": "SWE Intern", "location": "Remote",
                                                 "jobDescription": "<p>Python</p>"},
                              "hiringOrganization": {"name": "1001 Amgen Inc."}})
    assert job.ats == "workday" and job.company == "Amgen Inc." and job.id.startswith("x-")
    assert company_from_workday("", "pimco") == "Pimco"


# ----------------------------------------------------------------------------- non-TTY hand-off

class _FakePage:
    def __init__(self):
        self.waits = 0

    def is_closed(self):
        return False

    def wait_for_timeout(self, ms):
        self.waits += 1


def test_non_tty_does_not_abort_instantly(monkeypatch):
    w = Waiter(wait_seconds=None, interactive=False)
    assert w.mode == "window"  # keeps the browser open until the window is closed
    w = Waiter(wait_seconds=1, interactive=False)
    assert w.mode == "seconds"
    pg = _FakePage()
    import time

    start = time.monotonic()
    w.hand_off(pg)
    assert time.monotonic() - start >= 0.9 or pg.waits >= 1
    monkeypatch.setattr("openapply.apply.session.stdin_is_tty", lambda: False)
    assert ask_yes_no("Save?", default=False) is False


def test_apply_cli_without_tty_uses_wait_seconds(monkeypatch, tmp_path):
    """`openapply apply` under a pipe passes --wait-seconds through and doesn't prompt."""
    from typer.testing import CliRunner

    import openapply.cli as cli
    import openapply.runner as runner

    calls = {}

    def fake_process(tracker, row, profile, **kw):
        calls.update(kw)
        return runner.RunResult(row["id"], "Acme", "SWE", "filled", report=FillReport(row["id"], URL))

    t = Tracker(tmp_path / "c.db")
    store(t)
    monkeypatch.setattr(cli, "Tracker", lambda: t)
    monkeypatch.setattr(cli, "load_profile", lambda: {})
    monkeypatch.setattr(runner, "process_job", fake_process)
    res = CliRunner().invoke(cli.app, ["apply", "gh-123", "--wait-seconds", "5"], input="")
    assert res.exit_code == 0, res.output
    assert calls["wait_seconds"] == 5 and calls["attended"] is True and calls["submit"] is False
    assert "Aborted" not in res.output
