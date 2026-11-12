"""Eligibility and cross-source discovery integration tests.

Network calls are mocked: these tests exercise real source normalizers and
the shared pre-insert/database path without depending on third-party uptime.
"""
import sqlite3

import pandas as pd
import pytest

from applypilot.discovery import ashby, ats_common, greenhouse, jobspy, lever, workday
from applypilot.discovery.eligibility import (
    classify_job_eligibility,
    normalize_employment_type,
    normalize_workplace_type,
)

@pytest.fixture
def db():
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE jobs (
            url TEXT PRIMARY KEY, title TEXT, salary TEXT, description TEXT,
            location TEXT, site TEXT, strategy TEXT, discovered_at TEXT,
            posted_at TEXT, full_description TEXT, application_url TEXT,
            detail_scraped_at TEXT, detail_error TEXT, state TEXT, employment_type TEXT,
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
    ("Remote or Hybrid", "hybrid"),
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



def test_workday_store_uses_detail_employment_and_remote_fields(db):
    eligible = {
        "apply_url": "https://jobs.example/workday/part-time-remote",
        "title": "Part-time Data Analyst",
        "location": "Remote - United Kingdom",
        "time_type": "Part Time",
        "remote_type": "Remote",
        "full_description": "Part-time remote data analyst position.",
    }
    unknown = {
        "apply_url": "https://jobs.example/workday/unknown",
        "title": "Data Analyst",
        "location": "",
        "time_type": "",
        "remote_type": "",
        "full_description": "Data analyst position.",
    }
    assert workday.store_results(db, [eligible, unknown], {}) == (1, 0)
    row = db.execute(
        "SELECT employment_type, workplace_type, eligibility_status FROM jobs"
    ).fetchone()
    assert row == ("part_time", "remote", "eligible")


def test_jobspy_store_filters_and_persists_normalized_fields(db):
    frame = pd.DataFrame([{
        "job_url": "https://jobs.example/jobspy/part-time-remote",
        "title": "Part-time Data Analyst",
        "job_type": "parttime",
        "is_remote": True,
        "location": "United Kingdom",
        "description": "Part-time remote data analyst role with SQL.",
        "site": "Indeed",
    }, {
        "job_url": "https://jobs.example/jobspy/unknown",
        "title": "Data Analyst",
        "job_type": None,
        "is_remote": None,
        "location": None,
        "description": "Data analyst role.",
        "site": "Indeed",
    }])
    assert jobspy.store_jobspy_results(db, frame, "Indeed") == (1, 0)
    row = db.execute(
        "SELECT employment_type, workplace_type, eligibility_status FROM jobs"
    ).fetchone()
    assert row == ("part_time", "remote", "eligible")



def test_workday_detail_fields_flow_into_normalized_storage(monkeypatch, db):
    detail = {
        "jobPostingInfo": {
            "jobDescription": "<p>Part-time remote data analyst position.</p>",
            "externalUrl": "https://jobs.example/workday/detail",
            "timeType": "Part Time",
            "remoteType": "Remote",
            "startDate": "2026-11-10",
        }
    }
    monkeypatch.setattr(workday, "workday_detail", lambda *args, **kwargs: detail)
    job = {
        "external_path": "/job/123",
        "location": "Remote - United Kingdom",
        "employer_name": "Example",
    }
    normalized = workday._fetch_one_detail({"name": "Example"}, job)
    assert normalized["time_type"] == "Part Time"
    assert normalized["remote_type"] == "Remote"
    assert workday.store_results(db, [normalized], {}) == (1, 0)
    row = db.execute(
        "SELECT employment_type, workplace_type FROM jobs"
    ).fetchone()
    assert row == ("part_time", "remote")



def test_hybrid_label_overrides_conflicting_remote_boolean():
    assert normalize_workplace_type("Hybrid", True) == "hybrid"
