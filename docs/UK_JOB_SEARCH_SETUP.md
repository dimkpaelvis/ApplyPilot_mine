# UK job-search setup

## 1. Configure job searches

Copy the example search profile into the user configuration directory:

```bash
mkdir -p ~/.applypilot
cp src/applypilot/config/searches.uk.example.yaml ~/.applypilot/searches.yaml
```

Edit the queries and location filters before discovery. The configuration prioritises junior data analysis, data operations, data quality, SQL reporting and data administration roles. Junior data engineering is included as a stretch target, not the primary target.

## 2. Profile schema required by this repository

ApplyPilot reads `~/.applypilot/profile.json`. The current application code expects these top-level keys:

- `personal`: full_name, email, phone, city, country, linkedin_url
- `work_authorization`: legally_authorized, needs_sponsorship
- `compensation`: salary_expectation, salary_currency, salary_range_min, salary_range_max
- `experience`: years_of_experience_total, education_level, current_title
- `skills_boundary`: lists of genuine programming_languages, frameworks and tools
- `resume_facts`: preserved_companies, preserved_projects, preserved_school, real_metrics
- `availability`: earliest_start_date

A profile using keys such as `personal_info` and `application_defaults` is not schema-compatible. Do not simply rename the keys without reviewing every value. In particular, never guess work authorisation, sponsorship, qualifications, dates, employment history or salary answers.

The master resume must also exist at `~/.applypilot/resume.txt` (plain text). Keep the source CV as the factual source of truth.

## 3. Safe operating sequence

Run one stage at a time:

```bash
applypilot run discover --source jobspy
applypilot run enrich
applypilot run score
applypilot run tailor --min-score 7
applypilot status
```

Review the complete job description, fit reasoning and generated resume before applying. Do not use a score alone as proof of eligibility. The browser workflow is designed to stop for explicit human approval before submission; complete login, MFA and CAPTCHA yourself.

## 4. Export confirmed applications

```bash
applypilot export-applications
```

Default output: `~/.applypilot/tracking/applications.csv`.

The CSV includes the job title, company, job-description URL, application URL, application date, status and known contact emails. Contact emails are blank unless the tracking database has actually recorded one. The export does not guess company email addresses.

## 5. Current limitations to resolve before live use

- The current scoring prompt contains US-specific assumptions and must be made UK-aware and tested before trusting eligibility scores.
- Verify the profile schema and all right-to-work/sponsorship answers manually.
- Verify the browser workflow on a test application page and use manual approval for every submission.
- Job boards may block scraping, return stale listings or omit employer contact emails. Discovery is not guaranteed to be comprehensive.
