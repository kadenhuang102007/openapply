"""Tracker statuses, CSV columns, Sheets upsert planning, alert text, form-field rules."""

import csv

from openapply.apply.filler import classify_field, match_answer
from openapply.models import Job
from openapply.notify.imessage import build_alert
from openapply.tracker.db import Tracker
from openapply.tracker.export import COLUMNS, CsvExporter, plan_sheet_upsert, sheet_rows


def _job(i, title="Software Engineer Intern"):
    return Job(id=f"gh-{i}", company=f"Co{i}", title=title, url=f"https://job-boards.greenhouse.io/co/jobs/{i}")


def test_tracker_new_jobs_and_status(tmp_path):
    t = Tracker(tmp_path / "t.db")
    r1 = t.start_run("daily")
    t.upsert_job(_job(1), r1, True, "", ["swe"], [])
    t.commit()
    r2 = t.start_run("daily")
    t.upsert_job(_job(1), r2, True, "", ["swe"], [])
    t.upsert_job(_job(2), r2, True, "", ["swe"], [])
    t.commit()
    assert [r["id"] for r in t.new_in_run(r2)] == ["gh-2"]
    t.mark("gh-1", "interview")
    t.mark("gh-1", "rejected")
    row = t.get("gh-1")
    assert row["status"] == "rejected" and row["interviewed"] == 1
    # re-discovery never resets your status
    t.upsert_job(_job(1), r2, True, "", ["swe"], [])
    assert t.get("gh-1")["status"] == "rejected"


def test_csv_export_columns(tmp_path):
    t = Tracker(tmp_path / "t.db")
    r = t.start_run("x")
    t.upsert_job(_job(1), r, True, "", [], [])
    t.commit()
    t.mark("gh-1", "offer")
    rows = sheet_rows(t.by_status(["offer"]))
    out = tmp_path / "a.csv"
    CsvExporter(out).export(rows)
    got = list(csv.DictReader(out.open()))
    assert list(got[0].keys()) == COLUMNS
    assert got[0]["Applied?"] == got[0]["Interview?"] == got[0]["Offer?"] == "Yes"


def test_sheet_upsert_never_deletes_or_downgrades():
    existing = [
        COLUMNS,
        ["Co1", "SWE Intern", "Yes", "Yes", "", "https://job-boards.greenhouse.io/co/jobs/1"],
        ["Manual", "Row I typed", "", "", "", "https://example.com/x"],
    ]
    rows = [
        {"Company": "Co1", "Role": "SWE Intern", "Applied?": "Yes", "Interview?": "", "Offer?": "Yes",
         "Application Link": "https://job-boards.greenhouse.io/co/jobs/1?utm_source=x"},
        {"Company": "Co2", "Role": "DS Intern", "Applied?": "Yes", "Interview?": "", "Offer?": "",
         "Application Link": "https://job-boards.greenhouse.io/co/jobs/2"},
    ]
    updates, appends, _ = plan_sheet_upsert(existing, rows)
    assert updates == [(2, "Offer?", "Yes")]  # Interview? stays Yes, nothing cleared
    assert len(appends) == 1 and appends[0][0] == "Co2"


def test_alert_message_short_and_complete():
    top = [{"company": f"Company {i}", "title": "Software Engineer Intern, Platform Infrastructure " * 3,
            "score": 90 - i, "url": f"https://job-boards.greenhouse.io/c/jobs/{i}?utm_source=x"} for i in range(8)]
    msg = build_alert(42, top)
    assert len(msg) <= 1000
    assert msg.startswith("openapply: 42 new matching internships")
    assert "openapply rank" in msg and "utm_source" not in msg
    assert msg.count("\n1. ") == 1 and "6." not in msg


def test_form_field_rules():
    assert classify_field("First Name") == "first_name"
    assert classify_field("Last Name *") == "last_name"
    assert classify_field("Email") == "email"
    assert classify_field("LinkedIn Profile") == "linkedin"
    assert classify_field("Are you legally authorized to work in the US?") is None
    assert classify_field("Will you now or in the future require sponsorship?") is None
    assert classify_field("Gender") is None
    assert classify_field("Preferred Language") is None  # 'age' inside a word must not trip anything
    ans = {"Why are you interested in this role at Figma?": "Because..."}
    assert match_answer("Why are you interested in working at Figma?", ans, None)[1] == "Because..."
    assert match_answer("Cover Letter", ans, "Dear team")[1] == "Dear team"
