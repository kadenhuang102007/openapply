"""Saved answers: question matching, option choice, the opt-in for sensitive answers, and learning."""

import yaml

from openapply.apply.answers import (
    SavedAnswers, allowed, choose_many, choose_option, learn_candidates, normalize_question, save_answers, topic_for,
)


def test_topics_recognize_common_questions():
    assert topic_for("Are you legally authorized to work in the United States?") == "authorized_to_work_us"
    assert topic_for("Will you now or in the future require sponsorship for employment visa status?") == \
        "require_sponsorship"
    # "without sponsorship" is an authorization question
    assert topic_for("Are you authorized to work in the US without the need for visa sponsorship?") == \
        "authorized_to_work_us"
    assert topic_for("Have you previously worked for Amgen?", "Amgen") == "previously_worked_here"
    assert topic_for("Have you completed an internship before?") == "completed_internship"
    assert topic_for("Do you consent to receive SMS messages?") == "sms_opt_in"
    assert topic_for("Are you transgender?") == "transgender"
    assert topic_for("Gender") == "gender"
    assert topic_for("Are you Hispanic or Latino?") == "hispanic_latinx"
    assert topic_for("Race") == "race"
    assert topic_for("Veteran Status") == "veteran"
    assert topic_for("Disability Status") == "disability"
    assert topic_for("Country") == "country"
    assert topic_for("Expected graduation date") == "graduation_date"
    assert topic_for("Are you willing to relocate?") == "willing_to_relocate"
    assert topic_for("What is your favorite color?") is None


def test_normalize_replaces_company():
    assert normalize_question("Have you worked at PIMCO before? *", "PIMCO") == "have you worked at <company> before"


def test_choose_option_is_conservative():
    assert choose_option("No", ["Yes", "No, I have not"]) == "No, I have not"
    assert choose_option("United States", ["Canada", "United States of America"]) == "United States of America"
    assert choose_option("Male", ["Female", "Male", "Decline"]) == "Male"
    assert choose_option("Asian", ["Asian (Not Hispanic or Latino)", "White (Not Hispanic or Latino)"]) == \
        "Asian (Not Hispanic or Latino)"
    assert choose_option("Yes", ["Yes, I am a veteran", "Yes, I am a protected veteran"]) is None  # tie -> human
    assert choose_option("Purple", ["Red", "Green"]) is None
    assert choose_option("", ["Yes"]) is None
    assert choose_many(["Asian", "White"], ["Asian", "Black", "White"]) == ["Asian", "White"]


def test_sensitive_answers_need_opt_in():
    saved = SavedAnswers({"require_sponsorship": "No", "country": "United States"}, {})
    m = saved.lookup("Will you require visa sponsorship?")
    assert m.sensitive and not allowed(m, {}) and allowed(m, {"use_saved_answers": True})
    c = saved.lookup("Country")
    assert not c.sensitive and allowed(c, {})
    assert not allowed(saved.lookup("Gender"), {"use_saved_answers": True})  # empty answer


def test_learned_answers_win_and_yaml_booleans_read_back():
    saved = SavedAnswers({}, {"are you willing to work onsite in <company> s office": True})
    m = saved.lookup("Are you willing to work onsite in Acme's office?", "Acme")
    assert m and m.source == "learned" and m.answer == "Yes"


def test_learn_and_save_round_trip(tmp_path):
    path = tmp_path / "answers.yaml"
    path.write_text("# my answers\ntopics:\n  country: \"\"   # keep this comment\n  gender: \"\"\nlearned: {}\n")
    saved = SavedAnswers.load(path)
    captured = [
        {"label": "Country", "value": "United States", "kind": "select"},
        {"label": "Can you work onsite at Acme 3 days a week?", "value": "Yes", "kind": "radio"},
        {"label": "Pronouns", "value": "Select...", "kind": "select"},  # placeholder: ignored
    ]
    items = learn_candidates(captured, saved, "Acme")
    assert {i.key for i in items} == {"country", "can you work onsite at <company> 3 days a week"}
    save_answers(items, path)
    text = path.read_text()
    assert "# keep this comment" in text and "# my answers" in text
    data = yaml.safe_load(text)
    assert data["topics"]["country"] == "United States"
    assert data["learned"]["can you work onsite at <company> 3 days a week"] == "Yes"
    again = SavedAnswers.load(path)
    assert learn_candidates(captured, again, "Acme") == []  # nothing new the second time
