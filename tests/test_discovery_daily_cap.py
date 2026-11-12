"""Regression tests for the global daily unique-listing quota."""


def test_daily_unique_job_cap_applies_across_store_jobs_calls(tmp_db, monkeypatch):
    from applypilot import config
    from applypilot.database import store_jobs

    monkeypatch.setattr(
        config,
        "load_search_config",
        lambda: {"defaults": {"daily_unique_job_limit": 2, "daily_unique_timezone": "Europe/London"}},
    )
    conn = tmp_db()

    first, duplicates = store_jobs(
        conn,
        [
            {"url": "https://example.com/jobs/1", "title": "Analyst 1"},
            {"url": "https://example.com/jobs/2", "title": "Analyst 2"},
        ],
        "test",
        "api",
    )
    assert (first, duplicates) == (2, 0)

    second, duplicates = store_jobs(
        conn,
        [
            {"url": "https://example.com/jobs/1", "title": "Analyst 1"},
            {"url": "https://example.com/jobs/3", "title": "Analyst 3"},
        ],
        "test",
        "api",
    )
    assert (second, duplicates) == (0, 1)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2


def test_daily_unique_job_cap_can_be_disabled(tmp_db, monkeypatch):
    from applypilot import config
    from applypilot.database import store_jobs

    monkeypatch.setattr(
        config,
        "load_search_config",
        lambda: {"defaults": {"daily_unique_job_limit": 0}},
    )
    conn = tmp_db()
    jobs = [
        {"url": f"https://example.com/jobs/{i}", "title": f"Analyst {i}"}
        for i in range(3)
    ]

    new, duplicates = store_jobs(conn, jobs, "test", "api")

    assert new == 3
    assert duplicates == 0

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
