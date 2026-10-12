"""Apply: open the application page, fill what we safely can, and STOP.

- filler.py:     Greenhouse, Lever, Ashby forms.
- workday.py:    Workday's multi-step wizard (persistent login, stops at Review).
- answers.py:    saved answers for repeat dropdown / yes-no questions (config/answers.yaml).
- session.py:    waiting for you (Enter, --wait-seconds, or the window closing) and noticing your choices.
- autosubmit.py: the OPT-IN auto-submit guardrails and receipts (off unless `auto_submit: true`).

Without auto-submit, openapply never clicks Submit and never touches CAPTCHAs. Work
authorization, sponsorship, and EEO answers come only from answers you saved, and only
with `use_saved_answers: true`.
"""
