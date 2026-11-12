"""Regression tests for remote and non-full-time discovery preferences."""

import pandas as pd
import pytest

from applypilot.discovery.jobspy import (
    _employment_type_ok,
    _normalise_job_types,
    _remote_result_ok,
    _remote_country_ok,
    _jobspy_filter_results,
)


def test_freelance_maps_to_jobspy_contract_type():
    assert _normalise_job_types(["part-time", "freelance"]) == ["parttime", "contract"]


def test_default_types_are_part_time_and_contract():
    assert _normalise_job_types(None) == ["parttime", "contract"]


@pytest.mark.parametrize("job_type", ["fulltime", "full-time", "temporary", "internship"])
def test_full_time_and_unrequested_types_are_rejected(job_type):
    assert not _employment_type_ok(pd.Series({"job_type": job_type}), ["parttime", "contract"])


@pytest.mark.parametrize("job_type", ["parttime", "part-time", "contract", "freelance"])
def test_requested_work_types_are_kept(job_type):
    assert _employment_type_ok(pd.Series({"job_type": job_type}), ["parttime", "contract"])


def test_unknown_employment_type_is_rejected():
    assert not _employment_type_ok(pd.Series({"job_type": None}), ["parttime", "contract"])


def test_remote_flag_must_not_be_false():
    assert not _remote_result_ok(pd.Series({"is_remote": False, "location": "London, UK"}))


def test_remote_flag_true_is_kept():
    assert _remote_result_ok(pd.Series({"is_remote": True, "location": "London, UK"}))


def test_remote_location_is_kept_when_flag_missing():
    assert _remote_result_ok(pd.Series({"location": "Remote - United Kingdom"}))


def test_unknown_nonremote_location_is_rejected():
    assert not _remote_result_ok(pd.Series({"location": "London, UK"}))


def test_missing_workplace_information_is_rejected():
    assert not _remote_result_ok(pd.Series({"location": None, "is_remote": None}))


def test_explicit_remote_location_overrides_conflicting_false_flag():
    assert _remote_result_ok(pd.Series({"location": "Remote - UK", "is_remote": False}))


def test_false_remote_flag_without_explicit_remote_evidence_is_rejected():
    assert not _remote_result_ok(pd.Series({"location": "London, UK", "is_remote": False}))


def test_remote_country_allowlist_accepts_aliases_and_rejects_unknown():
    allowed = ["United Kingdom", "United States"]
    assert _remote_country_ok("Remote - UK", allowed)
    assert _remote_country_ok("Remote - USA", allowed)
    assert not _remote_country_ok("Remote - Germany", allowed)
    assert not _remote_country_ok("Remote / Worldwide", allowed)


def test_jobspy_filter_reports_each_rejection_stage_separately():
    frame = pd.DataFrame([
        {"title": "Part-time Analyst", "job_type": "parttime", "is_remote": True, "location": "Remote - UK"},
        {"title": "Full-time Analyst", "job_type": "fulltime", "is_remote": True, "location": "Remote - UK"},
        {"title": "Part-time Analyst", "job_type": "parttime", "is_remote": False, "location": "Remote - UK"},
        {"title": "Contract Analyst", "job_type": "contract", "is_remote": True, "location": "Remote - Germany"},
    ])
    kept, counts = _jobspy_filter_results(
        frame, True, ["parttime", "contract"], ["United Kingdom", "United States"]
    )
    assert len(kept) == 1
    assert counts == {"raw": 4, "remote": 1, "employment": 1, "country": 1, "location": 0}
