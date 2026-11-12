# Applicant eligibility and candidate suitability

ApplyPilot evaluates four separate dimensions after the LLM scoring stage.

## Discovery eligibility

The existing discovery gate checks vacancy employment type, workplace type and configured remote-market scope. This determines whether a listing belongs in the search dataset; it does not establish whether a UK-based applicant can apply.

## Applicant eligibility

The matching layer compares the candidate's configured work location with explicit restrictions or acceptance language in the job posting.

- A listing located in the UK, or explicitly open to UK-based applicants, can pass.
- A listing explicitly restricted to another country is rejected.
- A US-based remote listing with no statement about UK applicants is unverified, not eligible.
- EST/CST-only requirements are flagged for review by default. Set `matching.timezone_policy: reject` only if those schedules are unacceptable.
- Country-specific work-authorisation requirements remain unverified unless the profile records an answer for that same country. Citizenship is never used as a proxy for work authorisation.

The candidate's location is configured separately from the employer's listing location using `matching.candidate_country` in `searches.yaml`, or `profile.personal.country` as a fallback.

## Candidate suitability

The first deterministic suitability gate detects explicit minimum years-of-experience requirements and compares them with `experience.years_of_experience_total` in the candidate profile.

- Known shortfall: `unsuitable`.
- Missing candidate experience data where a minimum is stated: `review`.
- No explicit minimum, or a known threshold met: `suitable`.

This layer does not infer years of experience from a job title such as Senior. Skill fit and role fit are separately assessed by the LLM score and flexible-preference checks.

## Flexible preferences and routing

The optional `matching.flexible` configuration defines role keywords, preferred skills and a minimum LLM fit score. A job only qualifies for the auto-apply queue when every configured flexible category is met and applicant eligibility and candidate suitability both pass.

| Route | Meaning |
|---|---|
| `auto_apply_queue` | All configured gates pass and auto-apply is explicitly enabled |
| `manual_review` | Hard gates pass, but flexible preferences are not all met or auto-apply is disabled |
| `review_required` | Applicant eligibility or candidate suitability is uncertain |
| `rejected` | An explicit applicant-location restriction or experience shortfall fails |

Auto-apply is disabled by default. The application launcher rechecks applicant eligibility and candidate suitability before selecting a job. The existing final submission confirmation remains in place; this change does not silently submit applications.

## Configuration example

```yaml
matching:
  candidate_country: "United Kingdom"
  strict:
    require_explicit_applicant_country: true
  timezone_policy: "review"
  flexible:
    role_keywords:
      - "junior data analyst"
      - "data operations"
      - "data quality"
      - "reporting analyst"
      - "sql analyst"
      - "junior data engineer"
    preferred_skills: ["sql", "python", "mysql", "excel", "pandas"]
    minimum_fit_score: 8
  auto_apply:
    enabled: false
```

Do not enable unattended submission until the candidate profile, applicant-location extraction, suitability checks and application-form handling have been validated against a manually reviewed sample.
