# Discovery eligibility rules

ApplyPilot's remote job-search branch applies one shared eligibility rule before a listing is written to the jobs table.

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

The common rule is applied to JobSpy, Greenhouse, Lever, Ashby, Workday, Amazon, Costco, BuiltIn, Smart Extract, and Hacker News before insertion. JobSpy uses its structured `job_type` and `is_remote` fields where available. Greenhouse, Lever, Ashby and Workday map their source-specific fields into the canonical types. BuiltIn maps its `work_mode` field; Hacker News extraction requests an explicit employment type. Other sources fall back to explicit title/description/location wording.

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


## Remote country scope

Across the shared discovery eligibility gate, remote listings must also identify a country in the configurable `allowed_remote_countries` list in `~/.applypilot/searches.yaml`. The shipped default is the core English-speaking markets: United Kingdom, Ireland, United States, Canada, Australia, New Zealand, South Africa and Singapore. Add or remove countries explicitly to suit your target market. Country matching uses the listing's location field or explicit work-location wording in the description (for example, 'must be based in Canada'). Generic `Remote` or `Worldwide` labels remain unverified unless the description explicitly states an eligible work country.

This is a search-scope preference only. It does not establish that an employer permits cross-border remote work or that the applicant has legal work authorisation in that country. Review both in the full posting before applying.

Job-board coverage is not uniform: the current JobSpy Indeed connector remains UK-scoped (`country_indeed: uk`), while LinkedIn's broad remote search may return other allowlisted markets. Direct ATS sources are also constrained by their configured employer registries. The allowlist controls which discovered listings can enter the pipeline; it does not guarantee exhaustive coverage of every listed country.

JobSpy logs now report raw results and disjoint rejection counts for remote status, employment type, country scope and (for non-remote searches) location. Diagnostic samples contain only public listing fields: title, board, location, job type and remote flag. HTTP 429 responses remain source-side rate limits; ApplyPilot does not attempt to bypass them.


## Applicant eligibility and candidate suitability

Discovery eligibility does not prove that a UK-based candidate may apply. After scoring, ApplyPilot now records applicant eligibility, candidate suitability, flexible-preference results and a separate decision route. A remote US listing without explicit UK acceptance is held as unverified; an explicit US-only restriction is rejected. Timezone and country-specific work-authorisation constraints are flagged for review by default.

See [Applicant eligibility and candidate suitability](applicant-suitability.md) for the decision model, configuration and route meanings.
