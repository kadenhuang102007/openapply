"""Parsers turn each source's real (saved) payload into Job objects."""

from openapply.ats_links import parse_ats_url
from openapply.discovery.ashby import parse_ashby
from openapply.discovery.greenhouse import parse_greenhouse
from openapply.discovery.lever import parse_lever
from openapply.discovery.run import dedupe
from openapply.discovery.simplify import parse_simplify_readme
from openapply.discovery.zshah import parse_zshah_csv


def test_greenhouse(fixture_json):
    jobs = parse_greenhouse("figma", fixture_json("greenhouse_figma.json"), "Figma")
    assert len(jobs) == 4
    ds = next(j for j in jobs if j.title == "Data Science Intern (2027)")
    assert ds.id == f"gh-{ds.ats_job_id}" and ds.ats == "greenhouse" and ds.ats_board == "figma"
    assert ds.url == f"https://job-boards.greenhouse.io/figma/jobs/{ds.ats_job_id}"
    assert "San Francisco" in ds.location
    assert "<" not in ds.description and len(ds.description) > 1000  # HTML stripped, unescaped


def test_lever(fixture_json):
    jobs = parse_lever("palantir", fixture_json("lever_palantir.json"), "Palantir")
    assert len(jobs) == 3
    j = jobs[0]
    assert j.ats == "lever" and j.id.startswith("lv-")
    assert j.url.endswith("/apply") and "jobs.lever.co/palantir/" in j.url
    assert j.description


def test_ashby(fixture_json):
    jobs = parse_ashby("sentry", fixture_json("ashby_sentry.json"), "Sentry")
    intern = next(j for j in jobs if "Intern" in j.title)
    assert intern.ats == "ashby" and intern.url.endswith("/application")
    assert "Remote" in intern.location


def test_simplify_readme(fixture_text):
    jobs = parse_simplify_readme(fixture_text("simplify_readme.md"))
    companies = [j.company for j in jobs]
    # "↳" rows inherit the company above
    assert companies[:3] == ["Electronic Arts"] * 3
    # closed (🔒) rows are skipped, but the ↳ row after it still gets the right company
    assert "Software Engineer Intern" not in [j.title for j in jobs if j.company == "ClosedCo"]
    ds = next(j for j in jobs if j.company == "ClosedCo")
    assert ds.title == "Data Science Intern" and ds.source_flags == ["us_citizenship"]
    assert ds.ats == "lever" and "utm_source" not in ds.url
    # Greenhouse links are recognized and canonicalized
    gcm = next(j for j in jobs if j.company == "GCM Grosvenor")
    assert gcm.id == "gh-8015792003" and gcm.ats_board == "gcmgrosvenor"
    # multi-location <details> cells become a ;-joined list
    ey = next(j for j in jobs if j.company == "Ernst & Young")
    assert ey.location.count(";") == 3
    # section heading becomes the category, 🎓 becomes a flag
    mba = next(j for j in jobs if "MBA" in j.title)
    assert mba.category == "Product Management" and "advanced_degree" in mba.source_flags


def test_zshah_csv(fixture_text):
    jobs = parse_zshah_csv(fixture_text("zshah_internships.csv"))
    assert all(j.season != "Fall 2026" for j in jobs)
    assert not any("Fall 2026" in j.title for j in jobs)
    ar = jobs[0]
    assert ar.ats == "greenhouse" and ar.ats_board == "arcesiumllc" and "Java" in ar.skills_hint
    assert any("us_citizenship" in j.source_flags for j in jobs)


def test_parse_ats_url_variants():
    assert parse_ats_url("https://boards.greenhouse.io/embed/job_app?token=8175510&for=coinbase").job_id == "8175510"
    assert parse_ats_url("https://careers.example.com/jobs?gh_jid=123").ats == "greenhouse"
    r = parse_ats_url("https://jobs.ashbyhq.com/harvey/06d64648-b84b-48ae-94a2-d9c06dfdcb5d/application?embed=true")
    assert (r.ats, r.board) == ("ashby", "harvey")
    assert parse_ats_url("https://amd.wd1.myworkdayjobs.com/x") is None


def test_dedupe_merges_api_and_community(fixture_json):
    api = parse_greenhouse("figma", fixture_json("greenhouse_figma.json"), "Figma")
    from openapply.discovery._community import community_job

    ds = next(j for j in api if j.title.startswith("Data Science"))
    dup = community_job(company="Figma", title=ds.title, location="SF",
                        link=f"https://job-boards.greenhouse.io/figma/jobs/{ds.ats_job_id}?utm_source=Simplify",
                        source="simplify", season="Summer 2027")
    same_link = community_job(company="Figma, Inc.", title=ds.title, location="SF",
                              link=f"https://job-boards.greenhouse.io/figma/jobs/{ds.ats_job_id}", source="zshah")
    out = dedupe(api + [dup, same_link])
    assert len(out) == len(api)
    merged = next(j for j in out if j.id == ds.id)
    assert set(merged.sources) == {"greenhouse_api", "simplify", "zshah"}
    assert merged.season == "Summer 2027" and len(merged.description) > 1000
