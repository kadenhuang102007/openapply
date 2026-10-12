"""The standard Workday questions: agreements, signature, dates, and compliance topics."""

from openapply.apply.answers import agree_option, answer_text, topic_for
from openapply.apply.workday import experience_entries, parse_full_date


def test_workday_standard_questions_map_to_topics():
    assert topic_for("Have you read and agree to the Non Disclosure Agreement?") == "agreements"
    assert topic_for("To be considered for employment, all applicants must agree to the Mutual Arbitration "
                     "Agreement.") == "agreements"
    assert topic_for("Please enter your name:") == "signature_name"
    assert topic_for("Please enter today's date:") == "todays_date"
    assert topic_for("Which of the following best describes your current status?") == "current_status"
    assert topic_for("In your current job, do you use or work on the Workday system?", "Workday") == "uses_workday"
    assert topic_for("Are you a current or former employee of the United States government?") == \
        "government_employee"
    assert topic_for("Are you related to a current Workday employee?", "Workday") == "related_to_employee"
    assert topic_for("To the best of your knowledge, are you related to an employee of a customer, or a government "
                     "official, who has direct business interactions with Workday?", "Workday") == "related_to_employee"
    assert topic_for("This information is sought for compliance with U.S. Export Control laws. Are you a current "
                     "citizen, national or resident of any of the following countries/regions: Iran, Cuba?") == \
        "export_control"
    # the truthfulness acknowledgement is still the privacy/acknowledgement topic
    assert topic_for("I acknowledge that I have read, understood and reviewed the above questions, and I have "
                     "answered them truthfully and accurately.") == "privacy_ack"
    assert topic_for("Expected Graduation Date") == "graduation_date"
    # ordinary name boxes are not signatures, and "design" is not "e-sign"
    assert topic_for("First Name") is None
    assert topic_for("Design portfolio URL") is None


def test_agree_option_picks_the_one_affirmative():
    assert agree_option(["Select One", "I agree", "I DO NOT agree"]) == "I agree"
    assert agree_option(["Yes", "No"]) == "Yes"
    assert agree_option(["Select One", "Decline"]) is None


def test_parse_full_date_and_today():
    assert parse_full_date("05/15/2028") == (5, 15, 2028)
    assert parse_full_date("2028-05-15") == (5, 15, 2028)
    assert parse_full_date("May 15, 2028") == (5, 15, 2028)
    assert parse_full_date("May 2028") == (5, None, 2028)
    month, day, year = parse_full_date("today")
    assert month and day and year >= 2026
    assert answer_text("today").count("/") == 2


def test_work_history_follows_the_resume_being_sent():
    profile = {
        "experience": [{"company": "Fallback Co", "title": "Fallback"}],
        "extra_experience": [{"company": "Always Inc", "title": "Volunteer"}],
        "resumes": {"variants": {
            "pm": {"pdf": "/x/Resume_PM.pdf", "experience": [{"company": "Acme", "title": "Growth Marketer"}]},
            "swe": {"pdf": "/x/Resume_SWE.pdf", "experience": [{"company": "Acme", "title": "GTM Engineer"}]},
        }},
    }
    assert [e["title"] for e in experience_entries(profile, "/elsewhere/Resume_SWE.pdf")] == ["GTM Engineer", "Volunteer"]
    assert [e["title"] for e in experience_entries(profile, "/x/Resume_PM.pdf")] == ["Growth Marketer", "Volunteer"]
    assert [e["title"] for e in experience_entries(profile, None)] == ["Fallback", "Volunteer"]
