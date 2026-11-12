# Google Sheets review queue

ApplyPilot keeps SQLite as its source of truth. Google Sheets is a review surface only; it does not submit applications.

## Secrets

Set these Codespaces repository secrets:

- `GOOGLE_SERVICE_ACCOUNT_JSON`: the complete service-account JSON document.
- `GOOGLE_SHEET_ID`: the spreadsheet ID (not the full URL). The integration also accepts the canonical spreadsheet URL.

Share the spreadsheet with the service account's `client_email` as an Editor. Never commit the JSON key or print it in logs.

## Verify write access

After restarting the Codespace so secrets are injected:

```bash
applypilot sheets-test
```

This creates a uniquely named temporary tab, writes and clears a test cell, then deletes that tab. It does not modify the review tab.

## Synchronise the review queue

```bash
applypilot sheets-sync --dry-run
applypilot sheets-sync
```

The default tab is `Job Review`; override it with `--tab "My Review Tab"`.

The integration imports reviewer decisions first, then refreshes the ApplyPilot-owned columns. It upserts by stable job URL, so repeat syncs update existing rows instead of appending duplicates.

### Sheet ownership

- ApplyPilot owns columns A:P and S.
- The reviewer owns column Q (Review Decision) and R (Review Notes).
- Allowed decisions are `approve`, `reject`, and `hold`.
- Blank or unrecognised decisions are ignored.
- SQLite remains authoritative for job data and application status.

## Application gate

A job must pass discovery eligibility, applicant eligibility and candidate suitability. A `manual_review` job also requires an explicit `approved` decision imported from Sheets. An `auto_apply_queue` job must have the configured auto-apply qualification enabled. In either case, the Playwright application flow still asks for the exact `SUBMIT` confirmation for each individual application. Editing a cell never submits an application.

## Operational notes

- Start with a small sample and inspect the reasons for each route.
- A generic "Remote" label does not establish UK applicant eligibility.
- Unknown applicant-country or work-authorisation evidence stays in review.
- The spreadsheet is not a secure place for passwords, national IDs, or other secrets; the exported queue contains job and decision metadata only.
