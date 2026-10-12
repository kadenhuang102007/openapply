"""Phone alerts through the macOS Messages app (iMessage).

Step by step:
1. `build_alert` writes a short text: how many new matching roles, the top 5
   (company, role, score, link), and a pointer to `openapply rank`. It stays
   under ~1,000 characters by trimming role names and dropping rows if needed.
2. `send_imessage` hands that text to Messages via `osascript`. The message and
   phone number are passed as arguments to the AppleScript (never pasted into
   the script source), so odd characters in job titles can't break or inject
   anything.
3. With dry_run=True it only prints. The first real send will make macOS ask
   for permission to control Messages; allow it once.
"""

from __future__ import annotations

import subprocess

from ..text import strip_tracking

MAX_CHARS = 1000

_SCRIPT = """
on run argv
  set theMessage to item 1 of argv
  set theRecipient to item 2 of argv
  tell application "Messages"
    set theService to 1st account whose service type = iMessage
    set theBuddy to participant theRecipient of theService
    send theMessage to theBuddy
  end tell
end run
"""


def _short(url: str) -> str:
    return strip_tracking(url).replace("https://", "").replace("http://", "").rstrip("?")


def build_alert(new_count: int, top: list[dict], baseline: bool = False, max_chars: int = MAX_CHARS) -> str:
    """top: dicts with company, title, score, url (already sorted best-first)."""
    if baseline:
        head = f"openapply: first run, now tracking {new_count} matching internships."
    elif new_count == 0:
        return "openapply: no new matching internships today."
    else:
        head = f"openapply: {new_count} new matching internship{'s' if new_count != 1 else ''} today."
    tail = "Run `openapply rank` for the rest."
    rows = top[:5]
    title_cap = 60
    while True:
        lines = [head, ""]
        for i, j in enumerate(rows, 1):
            title = j["title"] if len(j["title"]) <= title_cap else j["title"][: title_cap - 1] + "…"
            lines.append(f"{i}. {j['company']} - {title} ({j['score']})\n{_short(j['url'])}")
        lines += ["", tail]
        msg = "\n".join(lines)
        if len(msg) <= max_chars:
            return msg
        if title_cap > 30:
            title_cap -= 10
        elif len(rows) > 1:
            rows = rows[:-1]
        else:
            return msg[: max_chars - 1] + "…"


def send_imessage(phone: str, message: str, dry_run: bool = True) -> str:
    if dry_run:
        return f"[dry run] would text {phone}:\n{message}"
    if not phone:
        raise ValueError("alert_phone is not set in config/profile.yaml")
    subprocess.run(["osascript", "-e", _SCRIPT, message, phone], check=True, capture_output=True, text=True)
    return f"sent iMessage to {phone}"
