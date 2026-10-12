"""Saved answers: answer the same dropdown / yes-no questions the way you did last time.

Step by step:
1. config/answers.yaml (gitignored; copy answers.yaml.example) has two parts.
   - `topics`: questions openapply already knows how to recognize, such as
     country, degree, graduation date, work authorization, sponsorship,
     "have you worked here before", "have you completed an internship", SMS
     opt-in, the privacy acknowledgement, and the EEO / self-identification set
     (gender, transgender, Hispanic/Latinx, race, veteran, disability), plus
     willingness to relocate and your major. You
     type the answer once.
   - `learned`: questions you answered by hand on a real form. `openapply apply`
     offers to save them after you finish (or saves them with --learn). The key
     is the question text, normalized (see step 2).
2. `normalize_question` turns a label into a stable key: lowercase, no
   punctuation, no "required" stars, and the company's own name replaced by
   "<company>", so "Have you worked for Amgen before?" and the same question at
   PIMCO share one key.
3. `SavedAnswers.lookup(label, company)` finds the saved answer for a question:
   first an exact (or near-exact) learned question, then the topic patterns.
4. `choose_option(answer, options)` picks the dropdown / radio option that best
   matches your saved answer: exact text first, then yes/no by the first word
   ("No" matches "No, I have not"), then "every word of the answer is in the
   option", then a close spelling. If two options tie or nothing is close, it
   returns None and the field is left for you.
5. Work authorization, sponsorship, and EEO / self-ID answers are SENSITIVE.
   They are only filled when config/profile.yaml says `use_saved_answers: true`.
   Everything else is filled whenever you've saved a non-empty answer.
6. `learn_candidates` compares the choices you made on the page with what's
   saved and returns only new or changed answers. `save_answers` writes them
   back into answers.yaml, editing the file's text so your comments survive.
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..config import CONFIG_DIR

ANSWERS_PATH = CONFIG_DIR / "answers.yaml"

# Labels openapply never fills from your profile. Choice questions in this set can
# still be answered from answers.yaml, but only with `use_saved_answers: true`.
SENSITIVE = re.compile(
    r"authori[sz]|sponsor|\bvisa|citizen|immigration|gender|\bsex\b|pronoun|\brace\b|ethnic|hispanic|latin[oax]|"
    r"veteran|disab|accommodat|criminal|conviction|salary expectation|ssn|social security|date of birth|"
    r"birth|\bage\b|password|religio|sexual orientation|lgbt|transgender|eeo|demographic",
    re.I,
)

# (topic key, pattern on the normalized question, sensitive?). First match wins,
# so the more specific topics come first ("transgender" before "gender").
TOPICS: list[tuple[str, str, bool]] = [
    ("transgender", r"transgender", True),
    ("hispanic_latinx", r"hispanic|latin[oax]", True),
    ("race", r"\brace\b|ethnicit", True),
    ("gender", r"\bgender\b|\bsex\b", True),
    ("veteran", r"veteran|military service|armed forces", True),
    ("disability", r"disabilit", True),
    ("require_sponsorship", r"sponsor|work visa|visa status|h ?1 ?b", True),
    ("authorized_to_work_us",
     r"(authori[sz]ed|eligible|permitted|able) to (legally )?work|work authori[sz]ation|right to work", True),
    ("previously_worked_here",
     r"(previously|ever|formerly|currently|before) (been )?(employed|worked|work)|former (employee|worker|contractor)"
     r"|previous(ly)? worker|worked (for|at|with) <company>|employed (by|at|with) <company>", False),
    ("completed_internship", r"(completed|had|done|participated in|held) (an |a |any )?(prior |previous )?intern", False),
    ("sms_opt_in", r"\bsms\b|text messag|opt ?in", False),
    ("privacy_ack",
     r"privacy|acknowledg|i (have read|agree|understand|consent|certify)|terms (and|&) conditions|\bconsent\b", False),
    ("willing_to_relocate", r"relocat", False),
    ("graduation_date", r"graduat", False),
    ("major", r"^(major|field of study|area of study|discipline|course of study)\b|what is your (major|field of study)", False),
    ("degree",
     r"^(highest |current )?(level of )?(degree|education)\b|degree (type|level|status|pursuing|program)"
     r"|highest level of education|what degree", False),
    ("country", r"^country\b|country of residence|which country|country region|^location country", False),
]
TOPIC_KEYS = [t[0] for t in TOPICS]
SENSITIVE_TOPICS = {k for k, _, s in TOPICS if s}

# "Without the need for sponsorship" is an authorization question, not a sponsorship one.
_WITHOUT_SPONSOR = re.compile(r"without (the )?(need (for|of) |requiring )?(any )?(visa |employer |employment )?sponsor")

# Option texts that mean "nothing chosen yet".
PLACEHOLDER = re.compile(r"^(select( one)?|select\.\.\.|choose( one)?|please select|--+|none selected|)$", re.I)


def _plain(s: str) -> str:
    s = s.lower().replace("&", " and ").replace("’", "'")
    s = re.sub(r"\(?\brequired\b\)?|\*", " ", s)
    s = re.sub(r"[^a-z0-9<>' ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_question(label: str, company: str = "") -> str:
    """Lowercase, strip punctuation/required markers, and replace the company's name with <company>."""
    text = _plain(label)
    if company:
        name = _plain(company)
        names = {name, re.sub(r"\s+(inc|llc|corp|corporation|co|company|group|holdings)$", "", name)}
        for n in sorted((n for n in names if len(n) > 2), key=len, reverse=True):
            text = re.sub(rf"\b{re.escape(n)}\b", "<company>", text)
    return text


def topic_for(label: str, company: str = "") -> str | None:
    """Which built-in topic a question is about, or None."""
    q = normalize_question(label, company)
    for key, pattern, _ in TOPICS:
        if key == "require_sponsorship" and _WITHOUT_SPONSOR.search(q):
            continue
        if re.search(pattern, q):
            return key
    return None


def answer_text(value: Any) -> str | list[str]:
    """YAML turns a bare yes/no into True/False; read those back as "Yes"/"No"."""
    if value is True:
        return "Yes"
    if value is False:
        return "No"
    if value is None:
        return ""
    if isinstance(value, list):
        return [str(answer_text(v)) for v in value if answer_text(v)]
    return str(value).strip()


@dataclass
class SavedMatch:
    key: str            # topic key, or the normalized learned question
    answer: str | list[str]
    sensitive: bool
    source: str         # "topic" | "learned"


@dataclass
class SavedAnswers:
    topics: dict[str, Any] = field(default_factory=dict)
    learned: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> "SavedAnswers":
        path = path or ANSWERS_PATH
        data: dict = {}
        if path.exists():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(dict(data.get("topics") or {}), dict(data.get("learned") or {}), path)

    def lookup(self, label: str, company: str = "") -> SavedMatch | None:
        """The saved answer for this question. A topic with an empty answer is returned with answer ""."""
        q = normalize_question(label, company)
        if not q:
            return None
        sensitive = bool(SENSITIVE.search(label))
        if q in self.learned and answer_text(self.learned[q]):
            return SavedMatch(q, answer_text(self.learned[q]), sensitive or bool(topic_for(label, company) in SENSITIVE_TOPICS), "learned")
        close = difflib.get_close_matches(q, list(self.learned), n=1, cutoff=0.92)
        if close and answer_text(self.learned[close[0]]):
            return SavedMatch(close[0], answer_text(self.learned[close[0]]), sensitive, "learned")
        topic = topic_for(label, company)
        if topic:
            return SavedMatch(topic, answer_text(self.topics.get(topic)), topic in SENSITIVE_TOPICS or sensitive, "topic")
        return None


def allowed(match: SavedMatch | None, profile: dict) -> bool:
    """Sensitive answers need `use_saved_answers: true` in the profile."""
    if match is None or not match.answer:
        return False
    return (not match.sensitive) or bool(profile.get("use_saved_answers"))


def why_not(match: SavedMatch | None) -> str:
    """Plain-English reason a choice field was left for you."""
    if match is None:
        return "no saved answer (add it to config/answers.yaml, or let `openapply apply` learn it)"
    if not match.answer:
        return f"saved answer `{match.key}` is empty in config/answers.yaml"
    return f"saved answer `{match.key}` is sensitive; set use_saved_answers: true in profile.yaml to auto-fill it"


def _opt(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", s.lower())).strip()


def choose_option(answer: str, options: list[str]) -> str | None:
    """The option whose text best matches the saved answer, or None when unsure."""
    a = _opt(answer)
    if not a:
        return None
    scored: list[tuple[float, str]] = []
    for o in options:
        n = _opt(o)
        if not n or PLACEHOLDER.match(o.strip()):
            continue
        words = n.split()
        if n == a:
            s = 1.0
        elif a in ("yes", "no"):
            s = 0.9 if words[0] == a else 0.0
        elif set(a.split()) <= set(words):
            s = 0.8 - 0.001 * (len(words) - len(a.split()))  # prefer the tighter option
        elif set(words) <= set(a.split()) and len(words) >= 2:
            s = 0.75
        else:
            r = difflib.SequenceMatcher(None, a, n).ratio()
            s = r * 0.8 if r >= 0.88 else 0.0
        if s > 0:
            scored.append((s, o))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    if len(scored) > 1 and abs(scored[0][0] - scored[1][0]) < 1e-9 and _opt(scored[0][1]) != _opt(scored[1][1]):
        return None  # two options fit equally well: let the human pick
    return scored[0][1]


def choose_many(answer: str | list[str], options: list[str]) -> list[str]:
    """For checkbox groups: every option matching one of the saved answers."""
    wanted = answer if isinstance(answer, list) else [a.strip() for a in re.split(r";|\|", answer) if a.strip()]
    picked: list[str] = []
    for w in wanted:
        hit = choose_option(w, options)
        if hit and hit not in picked:
            picked.append(hit)
    return picked


def is_yes(answer: str | list[str]) -> bool:
    if isinstance(answer, list):
        return False
    return _opt(answer).split()[:1] in (["yes"], ["true"], ["agree"], ["i"], ["accept"], ["checked"])


# ----------------------------------------------------------------------------- learning

@dataclass
class LearnItem:
    key: str        # topic key or normalized question
    label: str      # the question as it appeared on the page
    value: str | list[str]
    is_topic: bool
    previous: str | list[str] = ""


def learn_candidates(captured: list[dict], saved: SavedAnswers, company: str = "") -> list[LearnItem]:
    """New or changed choice answers from what you picked on the page."""
    out: dict[str, LearnItem] = {}
    for c in captured:
        label, value = (c.get("label") or "").strip(), c.get("value")
        if isinstance(value, list):
            value = [v for v in (str(x).strip() for x in value) if v and not PLACEHOLDER.match(v)]
        else:
            value = (value or "").strip()
            if PLACEHOLDER.match(value):
                value = ""
        if not label or not value or len(label) > 300:
            continue
        topic = topic_for(label, company)
        key = topic or normalize_question(label, company)
        if not key:
            continue
        prev = answer_text((saved.topics if topic else saved.learned).get(key))
        same = (sorted(_opt(v) for v in prev) == sorted(_opt(v) for v in value)) if isinstance(prev, list) and isinstance(value, list) \
            else (not isinstance(prev, list) and not isinstance(value, list) and _opt(prev) == _opt(value))
        if same:
            continue
        out[key] = LearnItem(key, label, value, bool(topic), prev)
    return list(out.values())


def _yaml_scalar(value: str | list[str]) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(json.dumps(v, ensure_ascii=False) for v in value) + "]"
    return json.dumps(value, ensure_ascii=False)  # a JSON string is a valid YAML scalar


def save_answers(items: list[LearnItem], path: Path | None = None) -> Path:
    """Write learned answers into answers.yaml, keeping the file's comments.

    Topic answers replace the `  key: ...` line under `topics:`. Learned answers
    replace their line under `learned:` or are appended there. If the text edit
    doesn't read back exactly right, the file is rewritten with plain YAML.
    """
    path = path or ANSWERS_PATH
    if not path.exists():
        example = path.with_name("answers.yaml.example")
        path.write_text(example.read_text(encoding="utf-8") if example.exists() else "topics: {}\nlearned: {}\n",
                        encoding="utf-8")
    text = path.read_text(encoding="utf-8")
    for it in items:
        key = it.key if it.is_topic else json.dumps(it.key, ensure_ascii=False)
        line = f"  {key}: {_yaml_scalar(it.value)}"
        pat = re.compile(rf"^  {re.escape(key)}:[^#\n]*?(\s+#.*)?$", re.M)
        if pat.search(text):  # replace the value, keep any trailing "# comment"
            text = pat.sub(lambda m: line + (m.group(1) or ""), text, count=1)
        else:
            text = _insert_under(text, "topics" if it.is_topic else "learned", line)
    try:
        data = yaml.safe_load(text) or {}
        ok = all(answer_text((data.get("topics" if it.is_topic else "learned") or {}).get(it.key)) == it.value
                 for it in items)
    except yaml.YAMLError:
        ok = False
    if not ok:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        data.setdefault("topics", {})
        data.setdefault("learned", {})
        data["topics"] = data["topics"] or {}
        data["learned"] = data["learned"] or {}
        for it in items:
            (data["topics"] if it.is_topic else data["learned"])[it.key] = it.value
        text = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    path.write_text(text, encoding="utf-8")
    return path


def _insert_under(text: str, header: str, line: str) -> str:
    """Add `line` at the end of the `header:` block (creating the block if needed)."""
    lines = text.rstrip("\n").split("\n")
    idx = next((i for i, ln in enumerate(lines) if re.match(rf"^{header}:\s*(\{{\}})?\s*(#.*)?$", ln)), None)
    if idx is None:
        return text.rstrip("\n") + f"\n{header}:\n{line}\n"
    lines[idx] = f"{header}:"
    end = idx
    for j in range(idx + 1, len(lines)):
        if lines[j].startswith("  "):
            end = j
        elif lines[j].strip() and not lines[j].lstrip().startswith("#"):
            break
    lines.insert(end + 1, line)
    return "\n".join(lines) + "\n"
