"""SQLite tracker.

Tables
- jobs:   one row per unique job. Discovery upserts these; your status is never
          overwritten by a later discovery run.
- runs:   one row per `discover`/`daily` run, with its stats. A job's
          `first_run_id` tells us which run first saw it, which is how `daily`
          knows what's NEW since last time.
- events: every status change you make (new -> shortlisted -> filled ->
          submitted -> interview -> offer / rejected), with a timestamp.

Statuses: new, shortlisted, filled, submitted, interview, rejected, offer.

Company tiers: each job also stores `company_tier` (big_tech, top_tech, quant,
finance, enterprise, faang_plus, or NULL for "not established") and `tier_note`,
the plain-English reason. Views take `established_only`:
- None  -> exactly the jobs that passed the filter at discovery time
- True  -> only established companies, even if discovery kept startups
- False -> startups too, even if discovery dropped them as
           "not an established company"
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ..config import DB_PATH
from ..filter.tiers import NOT_ESTABLISHED
from ..models import Job

STATUSES = ["new", "shortlisted", "filled", "submitted", "interview", "rejected", "offer"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  started_at TEXT NOT NULL,
  stats TEXT
);
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  company TEXT NOT NULL,
  title TEXT NOT NULL,
  location TEXT,
  url TEXT NOT NULL,
  ats TEXT,
  data TEXT NOT NULL,            -- full Job JSON
  passed_filter INTEGER DEFAULT 0,
  filter_reason TEXT,
  families TEXT DEFAULT '[]',
  flags TEXT DEFAULT '[]',
  score INTEGER,
  variant TEXT,
  explanation TEXT,
  llm_score INTEGER,
  llm_reason TEXT,
  status TEXT NOT NULL DEFAULT 'new',
  interviewed INTEGER DEFAULT 0,
  first_run_id INTEGER,
  first_seen TEXT,
  last_seen TEXT,
  last_seen_run_id INTEGER,
  company_tier TEXT,
  tier_note TEXT
);
CREATE TABLE IF NOT EXISTS events (
  job_id TEXT NOT NULL,
  status TEXT NOT NULL,
  at TEXT NOT NULL
);
"""


def role_key(company: str, title: str, location: str) -> tuple[str, str]:
    """Company + title with the location's words removed, so per-city copies collide."""
    loc_words = set(re.findall(r"[a-z]+", location.lower()))
    words = [w for w in re.findall(r"[a-z0-9]+", title.lower()) if w not in loc_words]
    return company.lower().strip(), " ".join(words)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Tracker:
    def __init__(self, path: Path = DB_PATH):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self) -> None:
        """Add columns that older databases don't have yet."""
        have = {r["name"] for r in self.conn.execute("PRAGMA table_info(jobs)")}
        for col in ("company_tier", "tier_note"):
            if col not in have:
                self.conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} TEXT")
        self.conn.commit()

    # ---- runs -----------------------------------------------------------
    def start_run(self, kind: str) -> int:
        cur = self.conn.execute("INSERT INTO runs (kind, started_at) VALUES (?, ?)", (kind, now()))
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, stats: dict) -> None:
        self.conn.execute("UPDATE runs SET stats=? WHERE id=?", (json.dumps(stats, default=str), run_id))
        self.conn.commit()

    def previous_run_count(self, before_run_id: int) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM runs WHERE id < ?", (before_run_id,)).fetchone()[0]

    # ---- jobs -----------------------------------------------------------
    def upsert_job(self, job: Job, run_id: int, passed: bool, reason: str, families: list[str], flags: list[str],
                   tier: str | None = None, tier_note: str | None = None) -> None:
        ts = now()
        self.conn.execute(
            """INSERT INTO jobs (id, company, title, location, url, ats, data, passed_filter, filter_reason,
                                 families, flags, first_run_id, first_seen, last_seen, last_seen_run_id,
                                 company_tier, tier_note)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 company=excluded.company, title=excluded.title, location=excluded.location,
                 url=excluded.url, ats=excluded.ats,
                 data=CASE WHEN length(json_extract(excluded.data,'$.description')) >=
                                length(json_extract(jobs.data,'$.description'))
                           THEN excluded.data ELSE jobs.data END,
                 passed_filter=excluded.passed_filter, filter_reason=excluded.filter_reason,
                 families=excluded.families, flags=excluded.flags,
                 last_seen=excluded.last_seen, last_seen_run_id=excluded.last_seen_run_id,
                 company_tier=excluded.company_tier, tier_note=excluded.tier_note""",
            (job.id, job.company, job.title, job.location, job.url, job.ats, job.model_dump_json(),
             int(passed), reason, json.dumps(families), json.dumps(flags), run_id, ts, ts, run_id,
             tier, tier_note),
        )

    def update_description(self, job: Job) -> None:
        self.conn.execute("UPDATE jobs SET data=? WHERE id=?", (job.model_dump_json(), job.id))

    def commit(self) -> None:
        self.conn.commit()

    def get(self, job_id: str) -> sqlite3.Row | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:  # allow unique prefixes
            rows = self.conn.execute("SELECT * FROM jobs WHERE id LIKE ?", (job_id + "%",)).fetchall()
            row = rows[0] if len(rows) == 1 else None
        return row

    def job(self, row: sqlite3.Row) -> Job:
        return Job.model_validate_json(row["data"])

    @staticmethod
    def _passes(established_only: bool | None) -> str:
        """SQL condition for "kept", honoring the established-only switch (see module docstring)."""
        if established_only is None:
            return "passed_filter=1"
        either = f"(passed_filter=1 OR filter_reason='{NOT_ESTABLISHED}')"
        return f"{either} AND company_tier IS NOT NULL" if established_only else either

    def filtered(self, run_id: int | None = None, established_only: bool | None = None) -> list[sqlite3.Row]:
        """Jobs that pass the filter and were seen in the given run (default: latest run)."""
        if run_id is None:
            run_id = self.conn.execute("SELECT MAX(last_seen_run_id) FROM jobs").fetchone()[0]
        return self.conn.execute(
            f"SELECT * FROM jobs WHERE {self._passes(established_only)} AND last_seen_run_id=?", (run_id,)
        ).fetchall()

    def set_score(self, job_id: str, score: int, variant: str, explanation: str) -> None:
        self.conn.execute("UPDATE jobs SET score=?, variant=?, explanation=? WHERE id=?",
                          (score, variant, explanation, job_id))

    def set_llm(self, job_id: str, score: int, reason: str) -> None:
        self.conn.execute("UPDATE jobs SET llm_score=?, llm_reason=? WHERE id=?", (score, reason, job_id))

    def ranked(self, limit: int = 25, run_id: int | None = None, collapse: bool = True,
               family: str | None = None, established_only: bool | None = None) -> list[sqlite3.Row]:
        """Best first. With collapse=True, the same role posted once per city shows once."""
        rows = self.filtered(run_id, established_only)
        if family:
            rows = [r for r in rows if family in json.loads(r["families"])]
        rows = sorted(rows,
                      key=lambda r: -(r["llm_score"] if r["llm_score"] is not None else (r["score"] or 0)))
        if collapse:
            seen, out = set(), []
            for r in rows:
                key = role_key(r["company"], r["title"], r["location"] or "")
                if key not in seen:
                    seen.add(key)
                    out.append(r)
            rows = out
        return rows[:limit]

    def new_in_run(self, run_id: int, established_only: bool | None = None) -> list[sqlite3.Row]:
        return self.conn.execute(
            f"SELECT * FROM jobs WHERE {self._passes(established_only)} AND first_run_id=?", (run_id,)
        ).fetchall()

    # ---- status ---------------------------------------------------------
    def mark(self, job_id: str, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {', '.join(STATUSES)}")
        extra = ", interviewed=1" if status in ("interview", "offer") else ""
        self.conn.execute(f"UPDATE jobs SET status=?{extra} WHERE id=?", (status, job_id))
        self.conn.execute("INSERT INTO events (job_id, status, at) VALUES (?,?,?)", (job_id, status, now()))
        self.conn.commit()

    def by_status(self, statuses: list[str]) -> list[sqlite3.Row]:
        q = ",".join("?" * len(statuses))
        return self.conn.execute(f"SELECT * FROM jobs WHERE status IN ({q}) ORDER BY company", statuses).fetchall()
