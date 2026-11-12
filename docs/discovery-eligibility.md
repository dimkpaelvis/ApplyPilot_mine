# Discovery eligibility rules

ApplyPilot's UK job-search branch applies one shared eligibility rule before a listing is written to the jobs table.

## Eligible listings

A listing is admitted only when both fields are positively identified:

- `employment_type`: `part_time` or `contract`
- `workplace_type`: `remote`

Freelance and independent-contractor wording normalizes to `contract`.

## Rejected or unverified listings

- Full-time, hybrid, on-site, temporary, internship and other unsupported types are rejected.
- Missing or ambiguous employment type becomes `unknown`.
- Missing or ambiguous workplace type becomes `unknown`.
- Unknown values are not eligible. They are not silently treated as part-time or remote.

The `jobs` table records normalized values in `employment_type`, `workplace_type`, `eligibility_status`, and `eligibility_reason` for admitted listings. Rejected discovery candidates are not inserted. Some sources log filtered counts or reasons, but those counters are not yet consistent across every adapter.

## Source coverage

The common rule is applied to JobSpy, Greenhouse, Lever, Ashby, Workday, Amazon, Costco, BuiltIn, Smart Extract, and Hacker News before insertion. JobSpy uses its structured `job_type` and `is_remote` fields where available. ATS sources prefer their structured employment/workplace fields and fall back to explicit title/description/location wording.

A source that does not expose enough evidence will return fewer eligible jobs. This is intentional: the system favours precision over recall for the user's strict remote, part-time/contract preference.

## Pipeline gate

The scorer records an explicit eligibility decision. Missing or unrecognized LLM eligibility output is `unverified`, not `eligible`. Only rows whose legacy scoring field is exactly `eligible` can enter tailoring, cover-letter generation, or application stages. Legacy rows with NULL eligibility are therefore held back.

## Verification

Run the complete automated suite:

```bash
pytest tests/ -q
```

The GitHub Actions CI workflow runs the full suite on pushes to this feature branch and on pull requests. Source-specific integration tests mock public board responses; they do not depend on external job-board uptime.

A live crawl and inspection of the user's local SQLite database must be run in the Codespace where the user's configuration and database exist. Do not treat mocked integration tests as proof that a third-party board is currently reachable or returning listings.
