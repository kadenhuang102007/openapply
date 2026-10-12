"""Workday: step detection, field detection, and the wizard flow, against saved HTML fixtures.

The fixtures in tests/fixtures/workday/ are served from a fake *.myworkdayjobs.com
host with Playwright's request routing, and widgets.js stands in for Workday's
dropdowns, search boxes, Add buttons and footer navigation. Nothing goes online.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openapply.apply.answers import SavedAnswers
from openapply.apply.filler import FillReport
from openapply.apply.session import Waiter
from openapply.apply.workday import (
    WorkdayFlow, classify_state, degree_candidates, field_role, fill_my_experience, fill_my_information,
    is_workday_url, parse_month_year, read_state, run_flow, step_from_title, workday_values,
)

FIX = Path(__file__).parent / "fixtures" / "workday"
# ".test" never resolves, and every other request is aborted, so nothing can reach the internet.
HOST = "https://acme.wd5.myworkdayjobs.test/en-US/careers/"
WIDGETS = (FIX / "widgets.js").read_text()

PROFILE = {
    "name": {"first": "Jane", "last": "Doe"},
    "email": "jane@example.edu", "phone": "(555)-555-0100",
    "linkedin": "https://www.linkedin.com/in/janedoe", "github": "https://github.com/janedoe",
    "location": "Berkeley, CA", "school": "University of California, Berkeley", "degree": "B.S.",
    "majors": ["Data Science"], "graduation": "May 2028",
    "address": {"line1": "1 Main St", "city": "Berkeley", "state": "CA", "postal_code": "94704",
                "country": "United States"},
    "education": [{"school": "University of California, Berkeley", "degree": "B.S.",
                   "field_of_study": "Data Science", "start": "August 2025", "end": "May 2028"}],
    "experience": [
        {"company": "Example Corp", "title": "Software Engineering Intern", "location": "",
         "start": "June 2026", "end": "Present", "description": "Built a data pipeline."},
        {"company": "Other Co", "title": "Research Assistant", "location": "Berkeley, CA",
         "start": "January 2026", "end": "May 2026", "description": "Ran regressions."},
    ],
}

TOPICS = {"country": "United States", "previously_worked_here": "No", "completed_internship": "Yes",
          "graduation_date": "May 2028", "sms_opt_in": "", "authorized_to_work_us": "Yes",
          "require_sponsorship": "No", "gender": "Female", "hispanic_latinx": "No", "race": "Asian",
          "veteran": "I am not a protected veteran", "disability": "No, I do not have a disability"}


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    import openapply.apply.workday as wd

    monkeypatch.setattr(wd, "PATIENCE", 0.1)


@pytest.fixture
def page(browser):
    ctx = browser.new_context()
    ctx.route("**/*", lambda route: route.abort())  # offline: anything not served below fails
    pg = ctx.new_page()
    pg.signed_in = False

    def serve(route):
        name = route.request.url.split("?")[0].split("#")[0].rsplit("/", 1)[-1] or "job_page.html"
        if name == "signin.html" and pg.signed_in:
            name = "my_information.html"
        path = FIX / name
        if not path.exists():
            return route.fulfill(status=404, body="not found")
        html = path.read_text().replace("</body>", f"<script>{WIDGETS}</script></body>")
        route.fulfill(status=200, content_type="text/html", body=html)

    pg.route("https://acme.wd5.myworkdayjobs.test/**", serve)
    yield pg
    ctx.close()


def flow_for(pg, profile=None, topics=None, use_saved=True, close_after=True, resume=None):
    profile = dict(profile or PROFILE)
    profile["use_saved_answers"] = use_saved
    saved = SavedAnswers(dict(TOPICS if topics is None else topics), {})
    report = FillReport("x-test", HOST, ats="workday")
    waiter = Waiter(wait_seconds=0, interactive=False)
    return WorkdayFlow(pg, profile, saved, resume, report, waiter, company="Acme", close_after=close_after)


def open_(pg, name):
    pg.goto(HOST + name)
    pg.wait_for_selector("body")


# ----------------------------------------------------------------------------- pure helpers

def test_workday_url_and_titles():
    assert is_workday_url("https://amgen.wd1.myworkdayjobs.com/careers/job/X_R-1")
    assert not is_workday_url("https://job-boards.greenhouse.io/figma/jobs/1")
    assert step_from_title("current step 1 of 6\nCreate Account/Sign In") == "signin"
    assert step_from_title("My Information") == "my_information"
    assert step_from_title("My Experience") == "my_experience"
    assert step_from_title("Application Questions") == "application_questions"
    assert step_from_title("Voluntary Disclosures") == "voluntary_disclosures"
    assert step_from_title("Self Identify") == "self_identify"
    assert step_from_title("Review") == "review"
    assert classify_state({"activeStep": "Something Custom"}) == ("wizard", "other")
    assert classify_state({"options": ["applyManually"], "applyButton": True}) == ("apply_options", "")
    assert classify_state({"applyButton": True}) == ("job_page", "")
    assert classify_state({"signin": True, "activeStep": "My Information"}) == ("signin", "signin")


def test_dates_degrees_values():
    assert parse_month_year("September 2026") == (9, 2026, False)
    assert parse_month_year("Present") == (None, None, True)
    assert parse_month_year("2025") == (None, 2025, False)
    assert parse_month_year("2026-06") == (6, 2026, False)
    assert "Bachelor's Degree" in degree_candidates("B.S.")
    v = workday_values(PROFILE)
    assert v["phone"] == "5555550100" and v["state"] == "California" and v["city"] == "Berkeley"
    assert v["how_did_you_hear"] == ""  # left alone unless the profile sets it


# ----------------------------------------------------------------------------- step routing

@pytest.mark.parametrize("name,expected", [
    ("job_page.html", ("job_page", "")),
    ("signin.html", ("signin", "signin")),
    ("my_information.html", ("wizard", "my_information")),
    ("my_experience.html", ("wizard", "my_experience")),
    ("application_questions.html", ("wizard", "application_questions")),
    ("voluntary_disclosures.html", ("wizard", "voluntary_disclosures")),
    ("self_identify.html", ("wizard", "self_identify")),
    ("review.html", ("wizard", "review")),
])
def test_detects_each_step(page, name, expected):
    open_(page, name)
    assert read_state(page)[:2] == expected


def test_apply_popup_is_detected(page):
    open_(page, "job_page.html")
    page.click('[data-automation-id="adventureButton"]')
    state, _, info = read_state(page)
    assert state == "apply_options"
    assert info["options"] == ["applyManually", "autofillWithResume", "useMyLastApplication"]


def test_field_detection_my_information(page):
    open_(page, "my_information.html")
    fields = flow_for(page).collect()
    roles = {field_role(f): f for f in fields if field_role(f)}
    for r in ("first_name", "last_name", "address_line1", "city", "state", "postal_code", "email",
              "phone", "phone_type", "country", "how_did_you_hear"):
        assert r in roles, r
    assert roles["state"]["kind"] == "dropdown" and roles["how_did_you_hear"]["kind"] == "multiselect"
    radio = [f for f in fields if f["kind"] == "radio"]
    assert radio and radio[0]["question"].startswith("Have you previously worked for Acme")


# ----------------------------------------------------------------------------- filling

def test_fill_my_information(page):
    open_(page, "my_information.html")
    flow = flow_for(page)
    need = fill_my_information(flow)
    val = lambda sel: page.locator(sel).input_value()  # noqa: E731
    assert val("#name--legalName--firstName") == "Jane"
    assert val("#name--legalName--lastName") == "Doe"
    assert val("#address--addressLine1") == "1 Main St"
    assert val("#address--postalCode") == "94704"
    assert val("#phoneNumber--phoneNumber") == "5555550100"
    assert page.inner_text("#address--countryRegion") == "California"
    assert page.inner_text("#country--country") == "United States of America"
    assert page.inner_text("#phoneNumber--phoneType") == "Mobile"
    assert page.is_checked("#candidateIsPreviousWorker-1")  # "No", from saved answers
    assert page.locator('[data-automation-id="formField-source"] [data-automation-id="selectedItem"]').count() == 0
    left = {f["label"] for f in need}
    assert any("Hear" in x for x in left)  # how-did-you-hear left for the user
    logs = flow.report.steps["my_information"]
    assert any(l.detail.startswith("left alone") for l in logs["left"])


def test_fill_my_experience_repeating_sections(page, tmp_path):
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    open_(page, "my_experience.html")
    flow = flow_for(page, resume=pdf)
    fill_my_experience(flow)
    val = lambda sel: page.locator(sel).input_value()  # noqa: E731
    assert val("#workExperience-1--jobTitle") == "Software Engineering Intern"
    assert val("#workExperience-1--companyName") == "Example Corp"
    assert page.is_checked("#workExperience-1--currentlyWorkHere")
    assert val("#workExperience-1--startDate-dateSectionMonth-input") == "06"
    assert val("#workExperience-1--startDate-dateSectionYear-input") == "2026"
    assert val("#workExperience-1--endDate-dateSectionYear-input") == ""  # current job: no end date
    assert val("#workExperience-2--companyName") == "Other Co"
    assert val("#workExperience-2--endDate-dateSectionMonth-input") == "05"
    assert val("#workExperience-2--roleDescription") == "Ran regressions."
    pills = page.locator('[data-automation-id="selectedItem"]').all_inner_texts()
    assert "University of California, Berkeley" in pills and "Data Science" in pills
    assert page.inner_text("#education-1--degree") == "Bachelor's Degree"
    assert val("#education-1--firstYearAttended-dateSectionYear-input") == "2025"
    assert val("#education-1--lastYearAttended-dateSectionYear-input") == "2028"
    assert val("#webAddress-1--url") == "https://github.com/janedoe"
    assert val("#socialNetworkAccounts--linkedInAccount") == "https://www.linkedin.com/in/janedoe"
    assert page.locator('[data-automation-id="file-upload-successful"]').count() == 1
    assert page.locator('[data-automation-id="panel-set-delete-button"]').count() == 4  # 2 work + 1 edu + 1 site; none deleted


# ----------------------------------------------------------------------------- the whole wizard

def test_full_flow_stops_at_review_and_never_submits(page, tmp_path):
    pdf = tmp_path / "resume.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    page.signed_in = True  # pretend the user already signed in to this tenant
    open_(page, "job_page.html")
    # use_saved_answers on; the open-ended "why Acme" box and self-ID name/date still need a human,
    # so run with a "human" who has already handled them: an answers set that covers every choice,
    # and a waiter that lets the flow continue past text questions by filling them here.
    flow = flow_for(page, resume=pdf, close_after=False)
    flow.waiter.pause = lambda pg, msg, until=None: _human_fills_text(pg)  # type: ignore[method-assign]
    run_flow(flow)
    assert flow.report.stopped_at == "review"
    assert read_state(page)[1] == "review"
    assert page.evaluate("() => sessionStorage.getItem('submitted')") is None
    steps = flow.report.steps
    for s in ("my_information", "my_experience", "application_questions", "voluntary_disclosures", "self_identify"):
        assert s in steps, s
    filled_q = {l.label for l in steps["application_questions"]["filled"]}
    assert any("legally authorized" in q for q in filled_q)
    assert any("sponsorship" in q for q in filled_q)
    assert any(l.detail.endswith("'Asian (Not Hispanic or Latino) (United States of America)'")
               for l in steps["voluntary_disclosures"]["filled"])
    # privacy acknowledgement has no saved answer -> left for the human
    assert any("privacy notice" in l.label for l in steps["voluntary_disclosures"]["left"])


def _human_fills_text(pg) -> bool:
    for sel in ("#primaryQuestionnaire--q6", "#selfIdentifiedDisabilityData--name"):
        if pg.locator(sel).count():
            pg.fill(sel, "typed by the human")
    return True


def test_sensitive_answers_need_opt_in(page):
    open_(page, "application_questions.html")
    flow = flow_for(page, use_saved=False)
    run_flow(flow)
    assert flow.report.stopped_at == "application_questions"
    assert page.inner_text("#primaryQuestionnaire--q1") == "Select One"  # authorization untouched
    assert page.inner_text("#primaryQuestionnaire--q2") == "Select One"  # sponsorship untouched
    assert page.inner_text("#primaryQuestionnaire--q3") == "Yes"  # internship: not sensitive
    assert page.inner_text("#primaryQuestionnaire--q4") == "May 2028"
    details = " ".join(l.detail for l in flow.report.steps["application_questions"]["left"])
    assert "use_saved_answers" in details


def test_signin_gate_waits_and_never_types(page):
    open_(page, "job_page.html")
    flow = flow_for(page)
    run_flow(flow)
    assert flow.report.stopped_at == "signin"
    assert page.locator("#input-4").input_value() == ""  # email untouched
    assert page.locator("#input-5").input_value() == ""  # password untouched
    assert page.evaluate("() => !!window.__signinClicked") is False


def test_review_page_is_never_clicked(page):
    from openapply.apply.workday import _click_next

    open_(page, "review.html")
    flow = flow_for(page)
    run_flow(flow)
    assert flow.report.stopped_at == "review"
    with pytest.raises(RuntimeError):
        _click_next(page, "review")
    with pytest.raises(RuntimeError):  # even if the caller thinks it's another step
        _click_next(page, "self_identify")
    assert page.evaluate("() => sessionStorage.getItem('submitted')") is None


def test_honeypot_and_passwords_are_not_fields(page):
    open_(page, "signin.html")
    kinds = {(f["aid"], f["kind"]) for f in flow_for(page).collect()}
    assert ("beecatcher", "text") not in kinds


@pytest.mark.live
def test_live_workday_reaches_signin_gate(tmp_path):
    """Opt-in (`pytest -m live`): opens a real Amgen Workday posting, clicks Apply -> Apply Manually,
    and expects the sign-in gate. Never types anything, never signs in, closes right away."""
    from openapply.apply.workday import fill_workday
    from openapply.models import Job

    url = ("https://amgen.wd1.myworkdayjobs.com/careers/job/United-States---Remote/Undergrad-Intern---Software-"
           "Engineer---Amgen-s-Technology---Medical-Organizations--Summer-2027-_R-255719")
    job = Job(id="x-live-smoke", company="Amgen", title="SWE Intern", url=url, ats="workday")
    rep = fill_workday(job, {}, None, close_after=True, headless=True, profile_dir=tmp_path / "profile")
    assert rep.stopped_at in ("signin", "my_information"), rep.error
