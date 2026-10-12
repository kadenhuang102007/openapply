"""Filter rules: internship, family, season, US location, flags."""

from openapply.filter import apply_filters, classify_families, is_us_location
from openapply.models import Job


def job(title, location="San Francisco, CA", description="", **kw):
    return Job(id="x", company="Co", title=title, location=location, url="https://x", description=description, **kw)


def test_role_families(filters_cfg):
    fams = filters_cfg["role_families"]
    assert classify_families("Product Manager Intern", fams) == ["pm"]
    assert classify_families("Software Engineer Intern", fams) == ["swe"]
    assert "ds" in classify_families("Data Science Intern (2027)", fams)
    assert "ml" in classify_families("Machine Learning Engineer Intern", fams)
    assert classify_families("Tax Intern", fams) == []


def test_keeps_good_internship(filters_cfg):
    r = apply_filters(job("Software Engineer Intern - Summer 2027"), filters_cfg)
    assert r.keep and r.families == ["swe"] and "season_unconfirmed" not in r.flags


def test_drops(filters_cfg):
    assert apply_filters(job("Senior Software Engineer"), filters_cfg).reason == "not an internship"
    assert apply_filters(job("Account Executive Intern"), filters_cfg).reason == "not a wanted role family"
    assert apply_filters(job("PhD Intern, Data Science (2027)"), filters_cfg).reason == "excluded title keyword"
    assert not apply_filters(job("Software Engineer Intern (Summer 2026)"), filters_cfg).keep
    assert not apply_filters(job("Software Engineer Intern - Fall"), filters_cfg).keep
    assert apply_filters(job("Software Engineer Intern", "London, United Kingdom"), filters_cfg).reason == "outside the US"
    assert not apply_filters(job("Hardware Engineer Intern"), filters_cfg).keep


def test_season_unconfirmed_is_flagged_not_dropped(filters_cfg):
    r = apply_filters(job("Data Science Intern"), filters_cfg)
    assert r.keep and "season_unconfirmed" in r.flags
    r = apply_filters(job("Data Science Intern", description="Join us for Summer 2027!"), filters_cfg)
    assert "season_unconfirmed" not in r.flags
    r = apply_filters(job("Data Science Intern", season="Summer 2027"), filters_cfg)
    assert "season_unconfirmed" not in r.flags


def test_us_location():
    assert is_us_location("San Francisco, CA • New York, NY") is True
    assert is_us_location("Vancouver, BC, Canada; Seattle, WA") is True
    assert is_us_location("Toronto, ON") is False
    assert is_us_location("NYC") is True
    assert is_us_location("Remote in USA") is True
    assert is_us_location("Remote") is None
    assert is_us_location("Vienna, Austria ; Remote") is False
    assert is_us_location("") is None


def test_sponsorship_flags(filters_cfg):
    d = "We are unable to sponsor visas for this role. Must be a U.S. citizen. ITAR applies."
    r = apply_filters(job("Software Engineer Intern 2027", description=d), filters_cfg)
    assert r.keep and {"no_sponsorship", "us_citizenship", "export_control"} <= set(r.flags)
    # flags drop the job only for someone who needs sponsorship
    r2 = apply_filters(job("Software Engineer Intern 2027", description=d), filters_cfg, needs_sponsorship=True)
    assert not r2.keep
    plain = apply_filters(job("Software Engineer Intern 2027", description="Authorized to work in the US."), filters_cfg)
    assert "no_sponsorship" not in plain.flags
