# openapply

An open-source, human-in-the-loop internship application assistant.

openapply finds internships, filters them to the ones you'd actually apply to, ranks them against your resume, drafts honest application text, and pre-fills the application form on Greenhouse, Lever, Ashby, and Workday.

Then it **stops**. You review every field and click Submit yourself.

(There's an opt-in [auto-submit](#auto-submit-opt-in) mode for people who want it. It's off unless you turn it on, and it only submits when every guardrail passes.)

Built for the Summer 2027 recruiting season (PM, SWE, Data Science, ML), runs locally, MIT licensed.

---

## The rules it never breaks

- **Never clicks Submit on its own.** The form-filler types into fields and attaches your resume. On Greenhouse/Lever/Ashby the only click is on a dropdown suggestion, behind a guard that refuses buttons, links, and anything labelled submit/apply/next. On Workday it has to click Apply, Add, dropdowns, and "Save and Continue", through a guard that refuses anything labelled submit, sign in, create account, or delete, and it stops at the Review step. The one exception is [auto-submit](#auto-submit-opt-in), which you have to turn on yourself.

- **Never presses Enter in a form.** Enter can submit a form. The single exception is inside Workday's own search boxes (School, Field of Study), which only search on Enter and aren't part of a submittable form.

- **Never touches CAPTCHAs or bot detection.** If a CAPTCHA is present it's logged, and you handle it.

- **Never creates accounts, never stores passwords.** There is no field for one. On Workday you sign in or create the account yourself; openapply waits.

- **Never guesses work authorization, sponsorship, or demographic answers.** It only uses answers you saved yourself in `config/answers.yaml`, and only when your profile says `use_saved_answers: true`. Free-text questions on those topics are always left for you.

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

## Workday

About 1,000 of the kept community-list roles live on Workday (`*.myworkdayjobs.com`). `openapply apply <id>` works on them too.

What happens:

1. openapply opens Chromium with a **saved browser profile** in `data/browser-profile/` (gitignored). Your Workday logins live there.

2. It opens the posting, clicks **Apply**, and picks **Apply Manually** ("Autofill with Resume" only if that's the only choice).

3. If Workday shows **Create Account / Sign In**, openapply prints a message and waits. You sign in or create the account yourself in that window. It never types into the email or password boxes and never clicks Sign In or Create Account. Because the profile is saved, this is **once per company**.

4. It walks the wizard, recognizing each step by Workday's stable `data-automation-id` attributes and the progress bar's step title:

   - **My Information:** name, email, phone (digits), phone type, address, country, state. "How did you hear about us?" is left alone unless you set `how_did_you_hear` in your profile.
   - **My Experience:** uploads your resume PDF, then clicks **Add** once per entry and fills Work Experience (from the profile's `experience:` list: title, company, location, dates, "I currently work here", description), Education (school, degree, field of study, years), Websites, and LinkedIn.
   - **Application Questions, Voluntary Disclosures, Self Identify:** only [saved answers](#saved-answers). Anything still empty is listed, and openapply waits for you to fill it and press Enter.

5. Between steps it clicks **Save and Continue**. If Workday shows errors and stays put, it waits for you to fix them.

6. At **Review** it stops. It never clicks Submit (unless auto-submit is on and every guardrail passes). A per-step log of filled vs. left fields is printed and saved to `generated/<id>/fill_log.json`.

Add a structured history to `config/profile.yaml` for step 4:

```yaml
address: {line1: "", city: Berkeley, state: CA, postal_code: "", country: United States}
education:
  - {school: "University of California, Berkeley", degree: B.S., field_of_study: Data Science, start: August 2025, end: May 2028}
experience:
  - company: Example Corp
    title: Software Engineering Intern
    location: ""
    start: June 2026
    end: Present          # ticks "I currently work here"
    description: Built an internal dashboard in Python and SQL.
```

**Live check (October 11, 2026):** on a real Amgen posting, openapply clicked Apply and Apply Manually, detected the Create Account / Sign In step, reported `Stopped at: signin`, and closed, all in about 7 seconds. It typed nothing. The wizard steps after sign-in are tested against saved HTML fixtures built from Workday's markup (see Tests); they haven't been run against a signed-in account yet.

---

## Saved answers

Most forms ask the same dropdown and yes/no questions: country, degree, graduation date, "have you worked here before?", sponsorship, EEO. Save your answers once:

```bash
cp config/answers.yaml.example config/answers.yaml   # gitignored; then fill in what you want auto-answered
```

- **Topics** are questions openapply already recognizes: country, degree, major, graduation date, completed an internship, previously worked at the company, willing to relocate, SMS opt-in, privacy acknowledgement, work authorization, sponsorship, gender, transgender, Hispanic/Latinx, race, veteran, and disability.

- **Matching is conservative.** The question label is matched to a topic (or a learned question), then the dropdown or radio option that best matches your answer is picked: exact text, then yes/no by the first word, then "all your words are in the option". If two options fit equally, or nothing fits clearly, the field is left for you.

- **Sensitive answers are opt-in.** Work authorization, sponsorship, and EEO / self-ID answers are only filled when `config/profile.yaml` says `use_saved_answers: true`. The example ships with `false` and empty answers.

- **It learns.** While the browser is open, openapply notes the choices you make. When you're done, `openapply apply` lists any answers that aren't saved yet and asks `Save them? [y/N]`. Pass `--learn` to save without asking. Learned questions are stored with the company name replaced by `<company>`, so "Have you worked for Amgen before?" also answers the same question at PIMCO.

This works on Greenhouse, Lever, Ashby, and Workday.

---

## Auto-submit (opt-in)

Off by default. To use it, set `auto_submit: true` in `config/profile.yaml` **and** run a command that asks for it: `openapply apply <id> --submit`, `openapply add <url>`, or `openapply autopilot`.

openapply submits only if **every** guardrail passes:

- Every required field has a value (from your profile or saved answers).
- No CAPTCHA or bot check on the page. openapply never solves or bypasses one.
- Nothing typed into the form still contains a `[YOUR WORDS` placeholder, and the fabrication check for the drafts says PASS.
- The company is on the established-company list (for a link you pasted with `openapply add`, this only warns), and the match score is at least `auto_submit_min_score` (default 60).
- Fewer than `auto_submit_daily_limit` (default 10) auto-submissions today.
- You haven't already applied to the same link.
- On Workday: it's on the Review step with no validation errors.

If anything fails, nothing is submitted. You get the reason, and the job goes on the `openapply todo` list.

When it does submit, it saves `generated/<id>/receipt.json` (time, link, resume, every field and value, the confirmation text) plus before/after screenshots, marks the job submitted, and refreshes the CSV and Google Sheet export.

In practice, many forms have a required acknowledgement checkbox or an open-ended question, so they'll land on the todo list for you to finish. That's by design.

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
| Workday | Yes: walks the wizard to Review; you sign in once per company yourself | Yes |
| iCIMS, Oracle, SmartRecruiters, etc. | **No.** These jobs are found and ranked, but you apply by hand | Broader coverage |
| Remembers your answers to repeat questions | Yes, `config/answers.yaml`, learns from what you pick; sensitive ones opt-in | Yes |
| Add any posting link you find | Yes, `openapply add <url>` (Greenhouse/Lever/Ashby/Workday) | Yes |
| "Action required" queue | Yes, `openapply todo` | Yes |
| Submits for you | **Off by default.** Opt-in `auto_submit`, only when every guardrail passes, with a receipt | Yes, that's the product |
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

Fill in the `work_authorization` fields yourself. They're only used to filter out "no sponsorship" postings if you need sponsorship. Answers typed into forms come from `config/answers.yaml` instead (see [Saved answers](#saved-answers)).

## Daily use

```bash
source .venv/bin/activate
openapply discover                 # fetch + filter + store (about 1-2 min)
openapply rank --top 25            # best matches; add --family pm|swe|ds|ml, --llm, --shortlist
openapply rank --include-startups  # ignore established_only for this command (or --established-only)
openapply show gh-7850795003       # details, flags, matched/missing skills
openapply tailor gh-7850795003     # drafts + fabrication check -> generated/<id>/
openapply apply gh-7850795003      # opens Chromium, fills, STOPS; you submit (Workday too)
openapply apply x-8893184f01 --wait-seconds 300   # no terminal (scripts): keep the browser open 5 min
openapply apply gh-7850795003 --learn             # save the dropdown answers you picked, no prompt
openapply mark gh-7850795003 submitted   # also: interview, rejected, offer
openapply add <url> [<url> ...]    # a posting you found: ingest -> score -> tailor -> fill -> text you
openapply autopilot --top 10       # same for your top 10 (auto-submits only if auto_submit: true)
openapply todo                     # jobs that stopped and need you, with the reason
openapply export --to both         # CSV + Google Sheet
openapply daily --dry-run          # what tomorrow's 8 AM text would say
```

Job ids are short. `gh-` means Greenhouse, `lv-` Lever, `ab-` Ashby, and `x-` other sites, including Workday. Workday jobs can be pre-filled; the rest are ranked, but you apply by hand.

When the terminal isn't interactive (stdin isn't a TTY, e.g. a script), `openapply apply` no longer closes the browser instantly: it keeps it open until you close the window, or for `--wait-seconds N`.

### Adding a posting you found

```bash
openapply add https://job-boards.greenhouse.io/acme/jobs/123 https://acme.wd1.myworkdayjobs.com/careers/job/X_R-1
```

Each link, in order: fetch the posting from the ATS's public API, score it and pick the resume (PM roles get your PM resume, SWE/Data/ML get the SWE resume), draft text, fill the form, then (only with `auto_submit: true` and every guardrail passing) submit. You get an iMessage either way: "Applied: Company – Role ✅" or "Needs you: Company – Role — reason". `--dry-run` does everything except submit and prints the text instead of sending it. Links you paste skip the established-company filter (it warns instead).

With auto-submit off, the browser stays open for you on each link, just like `openapply apply`.

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

Personal files are all gitignored: `config/profile.yaml`, `config/answers.yaml`, `.env`, `data/` (the database and the Workday browser profile), `generated/`, `logs/`, and `resumes/`. Only the `*.example` files are committed.

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

- Workday, against saved HTML fixtures for every step (`tests/fixtures/workday/`: job page, sign-in, My Information, My Experience, Application Questions, Voluntary Disclosures, Self Identify, Review). They're built from Workday's real `data-automation-id` markup; a small script stands in for its dropdowns, Add buttons, and navigation. Covered: step detection, field detection, filling, the sign-in gate, the sensitive-answer opt-in, and that Review is never clicked.

- Saved answers: question matching, option choice, and learning without losing your comments.

- Auto-submit: one test per guardrail, receipts, dry runs, `add` / `todo`, and the non-terminal hand-off.

Browser tests run headless against local fixtures with every other request blocked, so nothing touches the internet. One live smoke test (the Amgen sign-in gate) is skipped unless you ask for it: `.venv/bin/pytest -m live`.

## Known limits

- **Matching is lexical.** "Product management" can show as missing for a PM role if your resume never uses those words, even when the experience is relevant. `--llm` helps here.

- **Scores are relative to the day.** The similarity half of the score is a percentile within the day's batch, so compare scores within one run, not across runs.

- **Workday** postings found by discovery are still scored on title and tags only (no description is fetched during discovery; `openapply add` does fetch one). The wizard after sign-in is tested on fixtures, not yet on a signed-in account, and tenants customize their questions, so expect some fields left for you. Other sites (iCIMS, Oracle, ...) can't be pre-filled.

- **Dropdowns:** School and City are auto-picked when the suggestion clearly matches; everything else comes only from saved answers, and only when an option clearly matches.

- **The tier list is a judgment call.** It's hand-curated and will miss some established companies (or include some you wouldn't). Unlisted companies are excluded in established-only mode, so check `--include-startups` now and then.

- **Upstream list errors pass through.** Community lists can contain errors. One example: a placeholder company name, which openapply replaces with the ATS board name when it can.

## License

MIT
