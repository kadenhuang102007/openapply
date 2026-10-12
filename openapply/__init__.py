"""openapply: a human-in-the-loop internship application assistant.

The pipeline, in the order you run it:

1. discovery/  pulls postings from public job-board APIs and community lists.
2. filter/     keeps internships in your role families, season, and the US.
3. match/      scores each posting against your resume and picks a resume variant.
4. tailor/     drafts a cover letter and answers, then checks them for invented facts.
5. apply/      opens the application in a real browser, fills what it can, and STOPS.
               You review and click Submit yourself. Always.
6. tracker/    remembers every job and its status, and exports to CSV or Google Sheets.
7. notify/     texts you (iMessage) when new matching roles show up.
"""

__version__ = "0.1.0"
