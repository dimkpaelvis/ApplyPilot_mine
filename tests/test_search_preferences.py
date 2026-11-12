"""Regression tests for remote and non-full-time discovery preferences."""

import pandas as pd
import pytest

from applypilot.discovery.jobspy import (
    _employment_type_ok,
    _normalise_job_types,
    _remote_result_ok,
)


def test_freelance_maps_to_jobspy_contract_type():
    assert _normalise_job_types(["part-time", "freelance"]) == ["parttime", "contract"]


def test_default_types_are_part_time_and_contract():
    assert _normalise_job_types(None) == ["parttime", "contract"]


@pytest.mark.parametrize("job_type", ["fulltime", "full-time", "temporary", "internship"])
def test_full_time_and_unrequested_types_are_rejected(job_type):
    assert not _employment_type_ok(pd.Series({"job_type": job_type}), ["parttime", "contract"])


@pytest.mark.parametrize("job_type", ["parttime", "part-time", "contract", "freelance", None])
def test_requested_work_types_are_kept(job_type):
    assert _employment_type_ok(pd.Series({"job_type": job_type}), ["parttime", "contract"])


def test_remote_flag_must_not_be_false():
    assert not _remote_result_ok(pd.Series({"is_remote": False, "location": "London, UK"}))


def test_remote_flag_true_is_kept():
    assert _remote_result_ok(pd.Series({"is_remote": True, "location": "London, UK"}))


def test_remote_location_is_kept_when_flag_missing():
    assert _remote_result_ok(pd.Series({"location": "Remote - United Kingdom"}))


def test_unknown_nonremote_location_is_rejected():
    assert not _remote_result_ok(pd.Series({"location": "London, UK"}))
