# openapply

An open-source, human-in-the-loop internship application assistant.

openapply finds internships, filters them to the ones you'd actually apply to, ranks them against your resume, drafts honest application text, and pre-fills the application form.

Then it **stops**. You review every field and click Submit yourself.

Built for the Summer 2027 recruiting season (PM, SWE, Data Science, ML), runs locally, MIT licensed.

---

## The rules it never breaks

- **Never clicks Submit.** The form-filler types into fields and attaches your resume. The only click in the code is on a dropdown suggestion (School, City), behind a guard that refuses buttons, links, and anything labelled submit/apply/next. It never presses Enter, because Enter can submit a form.

- **Never touches CAPTCHAs or bot detection.** If a CAPTCHA is present it's logged, and you handle it.

- **Never stores passwords.** There is no field for one.

- **Never answers work authorization, sponsorship, visa, or demographic questions.** Those are always left for you.

- **Never invents experience.** Drafts only reuse your resume. A checker flags any number or name that doesn't trace back to your resume, profile, or the posting.

- **Polite to every server.** It uses public APIs only, sends an honest User-Agent, waits between requests to the same host, and backs off on 429/5xx.

---

## Architecture

```
                         config/companies.yaml          SimplifyJobs README     zshah101 CSV
                                  |                       (Summer 2027, dev)     (main/data)
                                  v                              |                    |
  +--------------------------------------------------------------+--------------------+--+
  |  discovery/   Greenhouse API   Lever API   Ashby API   simplify.py   zshah.py       |
  |               -> one Job model (pydantic) -> dedupe on ATS id / link / (co,title,link)|
  +---------------------------------------+-----------------------------------------------+
                                          v
  +----------------------------------------------------------------------------------------+
  |  filter/      internship?  role family (PM/SWE/DS/ML)?  excluded?  Summer 2027?  US?     |
  |               flags: no_sponsorship, us_citizenship, export_control, season_unconfirmed  |
  |               company tier: big tech / top tech / quant / finance / enterprise / none   |
  +---------------------------------------+------------------------------------------------+
                                          |  enrich: fetch full descriptions from public ATS APIs
                                          v
  +----------------------------------------------------------------------------------------+
  |  match/       resume .tex -> plain text  |  skill coverage + TF-IDF percentile = 0-100   |
  |               picks resume variant (PM vs SWE/Data)  |  optional Claude rerank (--llm)   |
  +---------------------------------------+------------------------------------------------+
                                          v
  +-------------------+     +-------------------------------+     +------------------------+
  |  tracker/ SQLite  |<--->|  tailor/  cover letter +      |---->|  apply/  Playwright    |
  |  status + history |     |  answers -> fabrication check |     |  fill, upload, STOP    |
  +----+---------+----+     +-------------------------------+     +-----------+------------+
       |         |                                                            |
       v         v                                                            v
   CSV export  Google Sheet upsert                                   YOU review + click Submit
       |
  notify/  `openapply daily` (launchd, 8:00 AM) -> new matches -> iMessage
```

Every module opens with a plain-English docstring that walks through what it does, step by step.

---

## Established companies only

Community lists are heavy on seed and Series A/B startups. If you only want internships at established companies, turn on one setting in `config/profile.yaml`:

```yaml
established_only: true
```

Here's what happens then.

- Every posting's company is looked up in `config/company_tiers.yaml`, a hand-curated list of about 470 companies in five tiers: **big tech** (Google, Meta, Apple, Amazon, Microsoft, NVIDIA, TikTok, ...), **top tech** (well-known public tech plus elite late-stage private companies like Stripe, Databricks, OpenAI, Anthropic, SpaceX, Anduril, Ramp), **quant** (Jane Street, Citadel, HRT, Jump, Five Rings, IMC, ...), **finance** (major banks, asset managers, exchanges, insurers, payment networks), and **enterprise** (roughly Fortune 500 companies that hire tech/product/data interns).

- Companies not on the list are excluded. They stay in the database with the reason `not an established company`, so `openapply show <id>` tells you why.

- `openapply rank` shows a **tier** column. `openapply daily` only texts you about established companies.

- Two flags override the setting for one command: `openapply rank --include-startups` shows everyone, and `openapply rank --established-only` filters even when the setting is off. No re-discovery is needed either way.

Two extra signals are recorded in each job's explanation:

- **SimplifyJobs 🔥 (FAANG+).** SimplifyJobs marks some companies with a 🔥. openapply keeps that marker, and it counts as established on its own.

- **Workday links (`myworkdayjobs.com`).** Startups rarely pay for Workday, so this is noted as a weak signal. It never makes a company established by itself.

### Editing the tier list

Open `config/company_tiers.yaml`. Each entry is either a plain name or `Name: [alias, alias]`:

```yaml
  finance:
    label: finance
    companies:
      - JPMorgan Chase: [JPMorgan, J.P. Morgan, Chase]
      - Two Sigma
```

You don't need every spelling. Both sides are normalized before comparing: case, accents, punctuation and spaces are ignored, "&" counts as "and", a leading "The" is dropped, and so are trailing legal words like Inc, LLC, Corp, Co, Holdings, Group. So "JPMorgan Chase & Co." and "JPMorganChase" already match `JPMorgan Chase`.

Matching is exact after that, so "Meta" never matches "Metadata". If a company shows up under a different name (a subsidiary, an old name), add it as an alias.

The loader refuses two entries that normalize to the same name, so a typo can't silently move a company between tiers. To see which companies were excluded in your last run:

```bash
sqlite3 data/openapply.db "SELECT company, COUNT(*) FROM jobs WHERE filter_reason='not an established company' GROUP BY company ORDER BY 2 DESC"
```

---

## openapply vs. Tsenta

The Tsenta column reflects Tsenta's public positioning as a hosted AI application agent, not hands-on testing. Check tsenta.com for its current features.

| | openapply | Tsenta (hosted product) |
|---|---|---|
| Finds jobs | Greenhouse, Lever, Ashby APIs for your company list, plus SimplifyJobs and zshah101 lists | Its own job feed |
| Filters by role, season, US, sponsorship language | Yes, regex rules you can edit | Yes |
| Ranks against your resume, with reasons | Yes: matched/missing skills, optional Claude score and 2-sentence reason | Yes |
| Tailored cover letter / answers | Yes, extractive by default, Claude optional, with a fabrication check | Yes |
| Fills Greenhouse / Lever / Ashby forms | Yes (tested live on all three) | Yes |
| Workday, iCIMS, Oracle, SmartRecruiters, etc. | **No.** These jobs are found and ranked, but you apply by hand | Broader coverage |
| Submits for you | **Never, by design** | Yes, that's the product |
| Reads your email inbox for interview/rejection updates | **Not yet.** You run `openapply mark` | Yes |
| Phone alerts for new roles | iMessage via macOS Messages (Mac only) | App/email |
| Where your data lives | Your laptop (SQLite, gitignored profile) | Their servers |
| Cost | Free; Claude features use your own API key | Subscription |

---

## Results (real run, October 11, 2026)

These numbers come from running `openapply discover` and then `openapply rank` against live sources on 2026-10-11.

**Fetched:** 7,205 postings.

- 4,213 came from 13 company boards. Anduril alone has 2,492; then Datadog 435, Waymo 371, Roblox 265, Coinbase 222, Lyft 187, Figma 151, Sentry 46, Five Rings 17, OpusClip 9, and Chicago Trading Company, Quadrillion Labs, and Melius at 6 each.

- 1,852 came from SimplifyJobs' open Summer 2027 roles.

- 1,140 came from zshah101's list (Summer 2027 and season-not-stated; Fall 2026 skipped).

**Unique after de-duplication:** 6,790.

**Internships kept after filtering:** 1,324. That's US-only, Summer 2027 or unconfirmed season, with PhD/MBA/hardware/co-op roles excluded.

- By family: SWE 730, Data Science 341, ML 240, PM 85. A job can be in more than one family.

- That's 1,130 distinct roles once per-city copies of the same posting are collapsed in the ranking.

- Flags on kept jobs: 140 mention US citizenship or clearance, 107 say no sponsorship, 6 mention export control, 238 have an unconfirmed season.

- 257 kept jobs have a full description: either from the company APIs, or fetched for list jobs hosted on Greenhouse, Lever, or Ashby. The rest (mostly Workday) are scored on title and skill tags only, and marked "thin description".

**Top 10 matches** (baseline scorer, no LLM):

| # | Score | Company | Role | Resume | Link |
|---|---|---|---|---|---|
| 1 | 87 | Interplay | AI Labs Intern (NYC) | SWE/Data | https://jobs.ashbyhq.com/interplay/bdf67758-1f20-4a01-8bb3-ccebfa79e9ac/application |
| 2 | 85 | Semgrep | Software Engineer Intern, Cloud Platform (SF) | SWE/Data | https://jobs.ashbyhq.com/semgrep/8e64dc7f-e925-4361-86d5-b01ee518c987/application |
| 3 | 84 | Harvey | Software Engineer Intern (NYC) | SWE/Data | https://jobs.ashbyhq.com/harvey/06d64648-b84b-48ae-94a2-d9c06dfdcb5d/application |
| 4 | 84 | Harvey | Software Engineer Intern, Summer 2027 (SF) | SWE/Data | https://jobs.ashbyhq.com/harvey/3a34578d-d42e-45bb-ac5c-0c3357e8cbb7/application |
| 5 | 83 | Gecko Robotics | Full Stack Software Engineer Intern (NYC) | SWE/Data | https://jobs.ashbyhq.com/gecko-robotics/01138338-ff3c-4982-8ba3-5401386bf082/application |
| 6 | 82 | ShopBack | Software Engineer Intern (SF) | SWE/Data | https://jobs.lever.co/shopback-2/640ac3fb-dae5-4738-95b9-9cb80cc7ad15/apply |
| 7 | 82 | ConductorAI | Software Engineer Intern (NYC), flagged us_citizenship | SWE/Data | https://jobs.ashbyhq.com/conductorai/d6a1b110-10ad-4b5e-83a0-88c5fd7bc891/application |
| 8 | 81 | Sigma Computing | Software Engineer Intern, Summer 2027 (SF/NYC) | SWE/Data | https://job-boards.greenhouse.io/sigmacomputing/jobs/7850795003 |
| 9 | 81 | Sigma Computing | Software Engineer Intern (NYC), flagged no_sponsorship | SWE/Data | https://job-boards.greenhouse.io/sigmacomputing/jobs/8001295003 |
| 10 | 81 | CSC Generation | Software Engineer, Legacy Apps & Modernization, Intern/Part Time (Houston) | SWE/Data | https://jobs.lever.co/cscgeneration-2/3a04b45f-a2eb-438a-a8ac-b07324223813/apply |

**With `established_only: true`** (same day, re-run on the new filter): 765 of the 1,324 kept internships survive, from 204 companies (607 distinct roles once per-city copies collapse).

- By tier: enterprise 419, top tech 148, finance 134, big tech 39, quant 25.

- By family: SWE 435, Data Science 183, ML 131, PM 52.

- Only 90 can be pre-filled (81 Greenhouse, 8 Ashby, 1 Lever). The other 675 are on Workday (462) and other sites, so you apply to those by hand.

- The top established matches were Harvey, Klaviyo, Figma, Ramp, DoorDash, Verkada, Robinhood, and Glean for SWE/Data, and Datadog, Roblox, and Coinbase for PM.

The top 10 are all SWE because the SWE/Data resume names many concrete tools that postings also name. Use `openapply rank --family pm` to see PM roles on their own. The best PM matches were ONE Finance Product Intern (65), OpusClip AI Product Management Intern (62), Datadog Product Management Intern (60), and Bedrock Robotics Product Intern (60).

**Form-filler, live test, no submit:** On the Sigma Computing Greenhouse form, openapply filled 10 fields: name, preferred name, email, phone, City and School dropdowns, LinkedIn, resume PDF, and the cover letter box. It left 12 for the human: Country, Degree, graduation date, pronouns, the onsite question, the acknowledgment, H-1B, visa status, gender, Hispanic/Latino, and veteran status. A CAPTCHA was detected and not touched.

The Ashby (Semgrep) and Lever (ShopBack) forms filled 5 and 7 fields.

---

## Setup

You need macOS or Linux, and Python 3.11 or newer. iMessage alerts need macOS.

```bash
git clone <this repo> openapply && cd openapply
python3.11 -m venv .venv
.venv/bin/pip install -e '.[sheets,dev]'
.venv/bin/playwright install chromium

cp config/profile.yaml.example config/profile.yaml   # then edit it
cp .env.example .env                                 # optional keys
```

In `config/profile.yaml`, point `resumes:` at your resume files. Each variant needs a `.tex` or `.txt` file for matching, and a PDF to upload. The `families` list decides which resume goes to which role family. You can also drop files into `resumes/`.

Fill in the `work_authorization` fields yourself. They're only used to filter out "no sponsorship" postings if you need sponsorship. They are never typed into a form.

## Daily use

```bash
source .venv/bin/activate
openapply discover                 # fetch + filter + store (about 1-2 min)
openapply rank --top 25            # best matches; add --family pm|swe|ds|ml, --llm, --shortlist
openapply rank --include-startups  # ignore established_only for this command (or --established-only)
openapply show gh-7850795003       # details, flags, matched/missing skills
openapply tailor gh-7850795003     # drafts + fabrication check -> generated/<id>/
openapply apply gh-7850795003      # opens Chromium, fills, STOPS; you submit
openapply mark gh-7850795003 submitted   # also: interview, rejected, offer
openapply export --to both         # CSV + Google Sheet
openapply daily --dry-run          # what tomorrow's 8 AM text would say
```

Job ids are short. `gh-` means Greenhouse, `lv-` Lever, `ab-` Ashby, and `x-` other sites. Other sites are ranked, but you apply by hand.

### Claude features (optional)

Set `ANTHROPIC_API_KEY` in `.env`. `rank --llm` re-scores the top N with `claude-opus-5-5`, giving a 0-100 score and a two-sentence reason, and caches the result per job. `tailor --llm` drafts with Claude under strict no-invention rules, then runs the same fabrication check.

Without a key, everything still works on the baseline scorer and the extractive drafts.

### Daily 8 AM alerts

```bash
./scripts/install_daily.sh      # copies the launchd plist to ~/Library/LaunchAgents and loads it
```

The job runs `openapply daily` (discover, filter, rank, then diff against the last run) and texts `alert_phone` the number of new matches plus the top 5. Logs go to `logs/daily.out.log`.

The first real send makes macOS ask for permission to control Messages. To uninstall:

```bash
launchctl bootout gui/$(id -u)/com.openapply.daily
```

### Google Sheets sync

`openapply export --to sheets` upserts into your tracker sheet using these columns:

`Company | Role | Applied? | Interview? | Offer? | Application Link`

Rows are matched by Application Link. Existing rows only ever get cells upgraded to "Yes". New applications are appended. Nothing is deleted.

1. `pip install -e '.[sheets]'` (already done if you followed Setup).

2. Choose one way to sign in:

   - **Service account:** In Google Cloud Console, create a project, enable the Google Sheets API, and create a service account key (JSON). Share the sheet with the service account's email as Editor. Then set `GOOGLE_SERVICE_ACCOUNT_FILE=/path/key.json` in `.env`.

   - **OAuth:** Create an OAuth client ID of type "Desktop app" and download the JSON. Then set `GOOGLE_OAUTH_CLIENT_FILE=/path/client.json` in `.env`. The first export opens a browser once.

3. Set `GOOGLE_SHEET_ID` in `.env`. If your sheet uses checkboxes, set `sheets.yes_value: "TRUE"` in your profile.

With no credentials, the export prints a "skipped" message and the CSV export still works.

---

## Run your own

openapply has nothing hard-coded to one person.

1. **Companies:** Edit `config/companies.yaml`. A slug is the part of the board URL after the host. For `job-boards.greenhouse.io/figma` it's `figma`, for `jobs.lever.co/palantir` it's `palantir`, and for `jobs.ashbyhq.com/sentry` it's `sentry`.

2. **Roles and season:** Edit `config/filters.yaml`. It holds the role-family regexes, title exclusions, season year and term, location rules, and the flag patterns.

3. **Which companies count as established:** Edit `config/company_tiers.yaml` (see [Editing the tier list](#editing-the-tier-list)).

4. **You:** Fill in `config/profile.yaml` (gitignored) and your resumes.

5. **Skills vocabulary:** `openapply/match/skills.py` is the list the matcher uses for explanations. Add your field's tools.

Personal files are all gitignored: `config/profile.yaml`, `.env`, `data/*.db`, `generated/`, `logs/`, and `resumes/`. Only the `*.example` files are committed.

## Tests

```bash
.venv/bin/pytest -q
```

The tests cover:

- Parsers, run against saved real payloads in `tests/fixtures/` (Greenhouse, Lever, Ashby, the SimplifyJobs README including ↳, 🔒 and 🔥 rows, and the zshah101 CSV).

- Company-tier matching (aliases like "JPMorgan Chase & Co." and "Alphabet Inc.", near-misses like "Metadata" and "Sigma Computing"), the 🔥 and Workday signals, and the established-only filter and views.

- De-duplication and filter rules.

- LaTeX stripping and scoring.

- The fabrication checker.

- Tracker statuses, CSV columns, and the Sheets upsert plan.

- Alert length and the form-field safety rules.

## Known limits

- **Matching is lexical.** "Product management" can show as missing for a PM role if your resume never uses those words, even when the experience is relevant. `--llm` helps here.

- **Scores are relative to the day.** The similarity half of the score is a percentile within the day's batch, so compare scores within one run, not across runs.

- **Workday and other non-Greenhouse/Lever/Ashby postings** have no fetched description and can't be pre-filled.

- **Dropdowns:** only School and City are auto-picked, and only when the suggestion clearly matches. Degree, dates, and every yes/no question are left for you.

- **The tier list is a judgment call.** It's hand-curated and will miss some established companies (or include some you wouldn't). Unlisted companies are excluded in established-only mode, so check `--include-startups` now and then.

- **Upstream list errors pass through.** Community lists can contain errors. One example: a placeholder company name, which openapply replaces with the ATS board name when it can.

## License

MIT
