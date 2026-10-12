"""Discovery: pull job postings from public sources and normalize them into `Job`s.

Sources (all public, no login, no scraping of pages that forbid it):
- greenhouse.py  Greenhouse Job Board API  (boards-api.greenhouse.io)
- lever.py       Lever Postings API        (api.lever.co)
- ashby.py       Ashby Posting API         (api.ashbyhq.com)
- simplify.py    SimplifyJobs Summer 2027 README (raw.githubusercontent.com)
- zshah.py       zshah101's automated Summer 2027 list (CSV in its repo)
- run.py         runs them all, de-duplicates, and enriches descriptions
"""
