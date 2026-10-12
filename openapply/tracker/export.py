"""Exporters: the same six columns as the Google Sheet tracker.

    Company | Role | Applied? | Interview? | Offer? | Application Link

Step by step:
1. `sheet_rows` turns tracker rows into those six columns:
   Applied?   = Yes once status is submitted, interview, rejected, or offer
   Interview? = Yes if you ever marked interview (or offer)
   Offer?     = Yes if status is offer
2. `CsvExporter` writes them to a CSV file.
3. `GoogleSheetsExporter` UPSERTS them into the live sheet, keyed by Application
   Link: existing rows get their Applied?/Interview?/Offer? cells updated (only
   ever from blank/No to Yes, never cleared), unknown links are appended at the
   bottom, and nothing is ever deleted. It needs gspread and a credentials file:
   GOOGLE_SERVICE_ACCOUNT_FILE (share the sheet with the service account email) or
   GOOGLE_OAUTH_CLIENT_FILE (desktop OAuth client; opens a browser once).
   No credentials -> it says so and skips; nothing else breaks.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Protocol

COLUMNS = ["Company", "Role", "Applied?", "Interview?", "Offer?", "Application Link"]
APPLIED = {"submitted", "interview", "rejected", "offer"}


def sheet_rows(rows, yes: str = "Yes", no: str = "") -> list[dict[str, str]]:
    out = []
    for r in rows:
        status = r["status"]
        out.append({
            "Company": r["company"],
            "Role": r["title"],
            "Applied?": yes if status in APPLIED else no,
            "Interview?": yes if (r["interviewed"] or status in ("interview", "offer")) else no,
            "Offer?": yes if status == "offer" else no,
            "Application Link": r["url"],
        })
    return out


class Exporter(Protocol):
    name: str

    def export(self, rows: list[dict[str, str]]) -> str: ...


class CsvExporter:
    name = "csv"

    def __init__(self, path: Path):
        self.path = path

    def export(self, rows: list[dict[str, str]]) -> str:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows(rows)
        return f"wrote {len(rows)} rows to {self.path}"


def _norm(link: str) -> str:
    from ..text import norm_link
    return norm_link(link) if link else ""


def plan_sheet_upsert(existing: list[list[str]], rows: list[dict[str, str]], yes: str = "Yes"):
    """Pure function (easy to test): decide cell updates and appends.

    Returns (updates, appends): updates is a list of (row_number_1_based, col_name, value),
    appends is a list of full rows (lists in COLUMNS order, or the sheet's own order).
    """
    if not existing:
        header = COLUMNS
        body: list[list[str]] = []
    else:
        header, body = existing[0], existing[1:]
    col = {name.strip(): i for i, name in enumerate(header)}
    missing = [c for c in COLUMNS if c not in col]
    if missing:
        raise ValueError(f"sheet header is missing columns: {missing}")
    link_i = col["Application Link"]
    by_link = {}
    for n, r in enumerate(body, start=2):
        if len(r) > link_i and r[link_i].strip():
            by_link.setdefault(_norm(r[link_i]), n)
    updates, appends = [], []
    for row in rows:
        n = by_link.get(_norm(row["Application Link"]))
        if n is None:
            full = [""] * len(header)
            for c in COLUMNS:
                full[col[c]] = row[c]
            appends.append(full)
            continue
        current = body[n - 2]
        for c in ("Applied?", "Interview?", "Offer?"):
            have = current[col[c]].strip() if len(current) > col[c] else ""
            if row[c] == yes and have.lower() not in ("yes", "true", "y", "x", yes.lower()):
                updates.append((n, c, yes))  # only ever upgrade to Yes
    return updates, appends, header


class GoogleSheetsExporter:
    name = "sheets"

    def __init__(self, sheet_id: str, worksheet: str | None = None, yes: str = "Yes"):
        self.sheet_id, self.worksheet, self.yes = sheet_id, worksheet, yes

    @staticmethod
    def credentials_available() -> bool:
        return bool(os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE") or os.environ.get("GOOGLE_OAUTH_CLIENT_FILE"))

    def _client(self):
        import gspread

        sa = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE")
        if sa:
            return gspread.service_account(filename=os.path.expanduser(sa))
        oauth = os.path.expanduser(os.environ["GOOGLE_OAUTH_CLIENT_FILE"])
        token = str(Path(oauth).with_name("google_authorized_user.json"))
        return gspread.oauth(credentials_filename=oauth, authorized_user_filename=token)

    def export(self, rows: list[dict[str, str]]) -> str:
        if not self.sheet_id:
            return "skipped Google Sheets: GOOGLE_SHEET_ID is not set"
        if not self.credentials_available():
            return ("skipped Google Sheets: no credentials. Set GOOGLE_SERVICE_ACCOUNT_FILE or "
                    "GOOGLE_OAUTH_CLIENT_FILE in .env (see README > Google Sheets sync)")
        try:
            import gspread  # noqa: F401
        except ImportError:
            return "skipped Google Sheets: run `pip install -e '.[sheets]'` first"
        from gspread.utils import rowcol_to_a1

        sh = self._client().open_by_key(self.sheet_id)
        ws = sh.worksheet(self.worksheet) if self.worksheet else sh.sheet1
        existing = ws.get_all_values()
        updates, appends, header = plan_sheet_upsert(existing, rows, self.yes)
        if not existing:
            ws.append_row(header, value_input_option="USER_ENTERED")
        col = {name.strip(): i for i, name in enumerate(header)}
        if updates:
            ws.batch_update([{"range": rowcol_to_a1(n, col[c] + 1), "values": [[v]]} for n, c, v in updates],
                            value_input_option="USER_ENTERED")
        if appends:
            ws.append_rows(appends, value_input_option="USER_ENTERED")
        return f"Google Sheet: {len(updates)} cells updated, {len(appends)} rows appended, 0 deleted"
