"""Eligibility and cross-source discovery integration tests.

Network calls are mocked: these tests exercise real source normalizers and
the shared pre-insert/database path without depending on third-party uptime.
"""
import sqlite3

import pytest

from applypilot.discovery.eligibility import (
    classify_job_eligibility,
    normalize_employment_type,
    normalize_workplace_type,
)
from applypilot.discovery import ats_common, greenhouse, lever, ashby


@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE jobs (
            url TEXT PRIMARY KEY, title TEXT, salary TEXT, description TEXT,
            location TEXT, site TEXT, strategy TEXT, discovered_at TEXT,
            posted_at TEXT, full_description TEXT, application_url TEXT,
            detail_scraped_at TEXT, state TEXT, employment_type TEXT,
            workplace_type TEXT, eligibility_status TEXT, eligibility_reason TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE job_state_transitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, job_url TEXT,
            from_state TEXT, to_state TEXT, at TEXT, reason TEXT, metadata TEXT
        )
    """)
    yield conn
    conn.close()


@pytest.mark.parametrize(("raw", "expected"), [
    ("Part-time", "part_time"),
    ("parttime", "part_time"),
    ("Freelance", "contract"),
    ("Independent contractor", "contract"),
    ("Full-time", "full_time"),
    ("Internship", "other"),
    (None, "unknown"),
])
def test_employment_type_normalization(raw, expected):
    assert normalize_employment_type(raw) == expected


@pytest.mark.parametrize(("raw", "expected"), [
    ("Remote", "remote"),
    ("Hybrid", "hybrid"),
    ("On-site", "onsite"),
    (None, "unknown"),
])
def test_workplace_type_normalization(raw, expected):
    assert normalize_workplace_type(raw) == expected


def test_unknown_employment_or_workplace_is_not_eligible():
    result = classify_job_eligibility({"title": "Data Analyst", "location": ""})
    assert result["eligibility_status"] == "rejected"
    assert result["employment_type"] == "unknown"
    assert result["workplace_type"] == "unknown"


def test_only_remote_part_time_or_contract_is_eligible():
    for employment in ("Part-time", "Contract", "Freelance"):
        result = classify_job_eligibility({
            "employment_type": employment,
            "workplace_type": "Remote",
            "title": "Data Analyst",
        })
        assert result["eligibility_status"] == "eligible"


def test_full_time_and_hybrid_are_rejected():
    result = classify_job_eligibility({
        "employment_type": "Full-time",
        "workplace_type": "Hybrid",
        "title": "Data Analyst",
    })
    assert result["eligibility_status"] == "rejected"


def test_greenhouse_normalizer_extracts_and_filters(monkeypatch):
    payload = {"jobs": [{
        "title": "Part-time Data Analyst",
        "absolute_url": "https://jobs.example/greenhouse/1",
        "location": {"name": "Remote - United Kingdom"},
        "content": "<p>Part-time remote data analyst role.</p>",
        "metadata": [{"name": "Employment Type", "value": "Part-time"}],
    }]}
    monkeypatch.setattr(ats_common, "fetch_with_retry", lambda *a, **k: (payload, None))
    jobs, error = greenhouse.scrape_one_employer("example", {"name": "Example"}, [])
    assert error is None
    assert len(jobs) == 1
    assert jobs[0]["employment_type"] == "Part-time"


def test_lever_normalizer_extracts_structured_fields(monkeypatch):
    payload = [{
        "text": "Freelance Data Analyst",
        "hostedUrl": "https://jobs.example/lever/1",
        "categories": {"commitment": "Freelance", "location": "United Kingdom"},
        "workplaceType": "remote",
        "description": "Remote freelance data analyst role.",
    }]
    monkeypatch.setattr(lever, "fetch_with_retry", lambda *a, **k: (payload, None))
    jobs, error = lever.scrape_one_employer("example", {"name": "Example"}, [])
    assert error is None
    assert jobs[0]["employment_type"] == "Freelance"
    assert jobs[0]["workplace_type"] == "remote"


def test_ashby_normalizer_extracts_structured_fields(monkeypatch):
    payload = {"jobs": [{
        "isListed": True,
        "title": "Part-time Data Analyst",
        "jobUrl": "https://jobs.example/ashby/1",
        "employmentType": "Part-time",
        "workplaceType": "Remote",
        "isRemote": True,
        "location": "United Kingdom",
        "descriptionPlain": "Part-time remote data analyst role.",
    }]}
    monkeypatch.setattr(ashby, "fetch_with_retry", lambda *a, **k: (payload, None))
    jobs, error = ashby.scrape_one_employer("example", {"name": "Example"}, [])
    assert error is None
    assert jobs[0]["employment_type"] == "Part-time"
    assert jobs[0]["workplace_type"] == "Remote"


def test_ats_insert_filters_before_database_and_deduplicates_cross_board(db):
    eligible = {
        "url": "https://jobs.example/shared-role",
        "title": "Part-time Data Analyst",
        "location": "Remote - UK",
        "description": "Part-time remote role.",
        "employment_type": "Part-time",
        "workplace_type": "Remote",
    }
    unknown = {
        "url": "https://jobs.example/unknown-role",
        "title": "Data Analyst",
        "location": None,
    }
    assert ats_common.insert_normalized_jobs(db, [eligible, unknown], "Greenhouse", "greenhouse_api") == (1, 0)
    assert ats_common.insert_normalized_jobs(db, [eligible], "Lever", "lever_api") == (0, 1)
    rows = db.execute("SELECT url, employment_type, workplace_type FROM jobs").fetchall()
    assert rows == [("https://jobs.example/shared-role", "part_time", "remote")]
