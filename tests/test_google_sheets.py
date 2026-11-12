"""Unit tests for the Google Sheets review queue integration."""
from applypilot.integrations.google_sheets import (
    HEADERS,
    _job_values,
    _normalise_decision,
    spreadsheet_id_from_value,
)


def test_spreadsheet_id_accepts_raw_id():
    assert spreadsheet_id_from_value("abc_123-xyz") == "abc_123-xyz"


def test_spreadsheet_id_extracts_id_from_google_url():
    value = "https://docs.google.com/spreadsheets/d/abc_123-xyz/edit?gid=0#gid=0"
    assert spreadsheet_id_from_value(value) == "abc_123-xyz"


def test_spreadsheet_id_rejects_unrecognised_url():
    try:
        spreadsheet_id_from_value("https://example.com/not-a-sheet")
    except ValueError as exc:
        assert "spreadsheet ID" in str(exc)
    else:
        raise AssertionError("Expected invalid spreadsheet URL to be rejected")


def test_review_decisions_are_normalised_conservatively():
    assert _normalise_decision("approve") == "approved"
    assert _normalise_decision("APPROVED") == "approved"
    assert _normalise_decision("reject") == "rejected"
    assert _normalise_decision("hold") == "hold"
    assert _normalise_decision("maybe") is None
    assert _normalise_decision("") is None


def test_sheet_contract_has_reviewer_owned_columns():
    assert HEADERS[0] == "Job ID"
    assert HEADERS[15] == "Application Status"
    assert HEADERS[16] == "Review Decision"
    assert HEADERS[17] == "Review Notes"
    assert HEADERS[18] == "Last Synced At"


def test_job_export_contains_stable_url_and_does_not_export_decision():
    row = {
        "url": "https://example.com/jobs/123",
        "title": "Junior Data Analyst",
        "company": "Example Ltd",
        "location": "Remote - UK",
        "employment_type": "part_time",
        "workplace_type": "remote",
        "fit_score": 9,
        "applicant_eligibility": "eligible",
        "applicant_eligibility_reason": "explicit_candidate_country_acceptance",
        "candidate_suitability": "suitable",
        "suitability_reason": "no_explicit_minimum_experience_detected",
        "flexible_score": 85,
        "decision_route": "manual_review",
        "discovered_at": "2026-11-12T12:00:00+00:00",
        "apply_status": None,
    }
    values = _job_values(row, "2026-11-12T12:01:00+00:00")
    assert values[0] == row["url"]
    assert values[13] == row["url"]
    assert values[15] == ""
    assert values[16] == ""
    assert values[17] == ""
    assert len(values) == len(HEADERS)

def test_sheets_sync_dry_run_does_not_require_google_credentials(tmp_db, seed_job, monkeypatch):
    from applypilot.integrations import google_sheets

    conn = tmp_db()
    seed_job(conn, decision_route="manual_review")

    def should_not_build_service():
        raise AssertionError("dry-run must not access Google")

    monkeypatch.setattr(google_sheets, "_build_service", should_not_build_service)
    result = google_sheets.sync_review_queue(conn=conn, dry_run=True)

    assert result["eligible_rows"] == 1
    assert result["dry_run"] is True
