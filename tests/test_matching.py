"""Tests for applicant eligibility and candidate suitability routing."""
from applypilot.matching import evaluate_job_match


def profile(country="United Kingdom", years="1", skills=None):
    return {
        "personal": {"country": country},
        "experience": {"years_of_experience_total": years},
        "skills_boundary": {
            "programming_languages": skills or ["Python", "SQL"],
            "databases": ["MySQL"],
        },
        "work_authorization": {},
    }


def cfg(enabled=False):
    return {
        "matching": {
            "candidate_country": "United Kingdom",
            "strict": {"require_explicit_applicant_country": True},
            "flexible": {
                "role_keywords": ["junior data analyst", "data operations", "data quality"],
                "preferred_skills": ["sql", "python", "mysql"],
                "minimum_fit_score": 8,
            },
            "auto_apply": {"enabled": enabled},
        }
    }


def job(description, location="Remote - United Kingdom", title="Junior Data Analyst"):
    return {
        "title": title,
        "location": location,
        "employment_type": "contract",
        "workplace_type": "remote",
        "description": description,
    }


def test_uk_remote_contract_role_passes_applicant_gate():
    result = evaluate_job_match(job("UK-based applicants may apply."), profile(), 9, cfg())
    assert result["applicant_eligibility"] == "eligible"
    assert result["decision_route"] == "manual_review"


def test_us_only_role_is_rejected_for_uk_candidate():
    result = evaluate_job_match(
        job("Applicants must be based in the United States.", "Remote - USA"),
        profile(), 9, cfg()
    )
    assert result["applicant_eligibility"] == "rejected"
    assert result["decision_route"] == "rejected"


def test_us_remote_listing_without_uk_acceptance_is_unverified():
    result = evaluate_job_match(
        job("Fully remote role. Must sit in EST or CST time zones.", "Boston, MA, USA (Remote)"),
        profile(), 9, cfg()
    )
    assert result["applicant_eligibility"] == "unverified"
    assert "timezone_constraint_requires_candidate_confirmation" in result["applicant_eligibility_reason"]


def test_explicit_experience_mismatch_is_unsuitable():
    result = evaluate_job_match(
        job("10+ years experience required.", title="Senior Data Analyst"),
        profile(years="1"), 9, cfg()
    )
    assert result["candidate_suitability"] == "unsuitable"
    assert result["decision_route"] == "rejected"


def test_unknown_candidate_experience_requires_review_not_rejection():
    result = evaluate_job_match(
        job("10+ years experience required."),
        profile(years=""), 9, cfg()
    )
    assert result["candidate_suitability"] == "review"
    assert result["decision_route"] == "review_required"


def test_auto_apply_route_requires_all_configured_flexible_checks_and_opt_in():
    role = job("SQL and Python required.")
    passing = evaluate_job_match(role, profile(), 9, cfg(enabled=True))
    assert passing["decision_route"] == "auto_apply_queue"
    assert passing["auto_apply_qualified"] == 1

    weak = evaluate_job_match(
        job("Excel reporting.", title="Reporting Assistant"),
        profile(), 6, cfg(enabled=True)
    )
    assert weak["decision_route"] == "manual_review"
    assert weak["auto_apply_qualified"] == 0


def test_auto_apply_is_disabled_by_default():
    result = evaluate_job_match(job("SQL and Python required."), profile(), 9, cfg(enabled=False))
    assert result["decision_route"] == "manual_review"
    assert result["auto_apply_qualified"] == 0

def test_work_authorisation_requirement_without_country_specific_profile_is_unverified():
    result = evaluate_job_match(
        job("US work authorization required.", "Remote - USA"),
        profile(), 9, cfg()
    )
    assert result["applicant_eligibility"] == "unverified"
    assert "country_specific_work_authorisation_unverified" in result["applicant_eligibility_reason"]


def test_senior_title_without_explicit_years_requires_review():
    result = evaluate_job_match(
        job("Investment data analyst responsibilities.", title="Senior Data Analyst"),
        profile(years="1"), 9, cfg()
    )
    assert result["candidate_suitability"] == "review"
    assert result["decision_route"] == "review_required"
