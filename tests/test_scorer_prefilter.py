"""UK-focused tests for the scoring pre-filter and eligibility parser.

The former tests asserted US-only assumptions: they rejected junior roles,
non-US countries and UK right-to-work wording. Those expectations conflict
with this fork's UK-based junior-data-role search. Geography and work
authorisation are therefore assessed by the LLM prompt, not guessed by this
rule-based pre-filter.
"""

import pytest

from applypilot.scoring.scorer import _check_ineligible, _parse_score_response


def _job(title="Junior Data Analyst", location="Remote - United Kingdom", description="UK-based role."):
    return {"title": title, "location": location, "full_description": description}


@pytest.mark.parametrize("title", [
    "Junior Data Analyst",
    "Data Analyst Intern",
    "Graduate Data Engineer",
    "Entry Level SQL Analyst",
    "Data Operations Trainee",
    "Apprentice Data Technician",
    "Senior Data Engineer",
    "Principal Software Engineer",
])
def test_relevant_seniority_levels_are_not_pre_filtered(title):
    assert _check_ineligible(_job(title=title)) is None


@pytest.mark.parametrize("location", [
    "Remote - United Kingdom",
    "London, UK",
    "Dublin, Ireland",
    "Berlin, Germany",
    "Remote (Europe)",
    "Remote (US)",
    "New York, NY",
    "Remote - Canada",
    "Worldwide",
    "Location not specified",
])
def test_geography_is_deferred_to_uk_focused_eligibility_assessment(location):
    # A location string alone is not enough to establish whether UK applicants
    # are excluded; the full posting and LLM eligibility assessment decide.
    assert _check_ineligible(_job(location=location)) is None


@pytest.mark.parametrize("description", [
    "You must have the right to work in the United Kingdom.",
    "Applicants must be based in the UK.",
    "This role is remote within the United States only.",
    "Our company has offices in London, Tokyo and San Francisco.",
    "Candidates may work remotely from approved countries.",
    "Must be available to work UK business hours.",
])
def test_description_based_eligibility_is_not_guessed_by_prefilter(description):
    assert _check_ineligible(_job(description=description)) is None


def test_parser_extracts_eligibility_eligible():
    response = """ELIGIBILITY: eligible
SCORE: 9
KEYWORDS: SQL, Python, pandas
REASONING: Strong match for a junior data role."""
    parsed = _parse_score_response(response)
    assert parsed["score"] == 9
    assert parsed["eligibility"] == "eligible"


def test_parser_extracts_non_uk_only():
    response = """ELIGIBILITY: non_uk_only
SCORE: 8
KEYWORDS: backend
REASONING: Posting explicitly excludes UK-based applicants."""
    parsed = _parse_score_response(response)
    assert parsed["eligibility"] == "non_uk_only"


def test_parser_normalises_legacy_non_us_only_label():
    response = """ELIGIBILITY: non_us_only
SCORE: 8
KEYWORDS: backend
REASONING: Legacy label from the upstream US-focused version."""
    parsed = _parse_score_response(response)
    assert parsed["eligibility"] == "non_uk_only"


def test_parser_accepts_bracketed_non_uk_only_label():
    response = "ELIGIBILITY: [non_uk_only]\nSCORE: 7\nKEYWORDS: SQL\nREASONING: UK applicants excluded."
    parsed = _parse_score_response(response)
    assert parsed["eligibility"] == "non_uk_only"


def test_parser_missing_eligibility_defaults_to_eligible():
    response = "SCORE: 8\nKEYWORDS: SQL, Python\nREASONING: Good match."
    parsed = _parse_score_response(response)
    assert parsed["eligibility"] == "eligible"



def test_parser_extracts_full_time_only():
    response = """ELIGIBILITY: full_time_only
SCORE: 1
KEYWORDS: SQL, Python
REASONING: The posting explicitly states this is a full-time permanent role."""
    parsed = _parse_score_response(response)
    assert parsed["eligibility"] == "full_time_only"
    assert parsed["score"] == 1


def test_parser_extracts_non_remote_only():
    response = """ELIGIBILITY: non_remote_only
SCORE: 1
KEYWORDS: SQL
REASONING: The posting explicitly requires on-site attendance."""
    parsed = _parse_score_response(response)
    assert parsed["eligibility"] == "non_remote_only"
