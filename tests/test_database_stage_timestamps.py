"""Tests for database pipeline-stage timestamp filtering."""

from datetime import datetime, timedelta, timezone

from applypilot.database import get_jobs_by_stage


def test_pending_score_retries_expired_iso_timestamp(tmp_db, seed_job):
    conn = tmp_db()
    now = datetime.now(timezone.utc)
    expired = (now - timedelta(minutes=5)).isoformat()
    url = seed_job(
        conn,
        url_suffix="expired-score-retry",
        fit_score=None,
        score_error="Temporary LLM failure",
        score_attempts=1,
        score_next_retry_at=expired,
    )["url"]

    jobs = get_jobs_by_stage(conn=conn, stage="pending_score", max_age_days=14, limit=0)

    assert [job["url"] for job in jobs] == [url]


def test_pending_score_does_not_retry_before_iso_retry_timestamp(tmp_db, seed_job):
    conn = tmp_db()
    now = datetime.now(timezone.utc)
    future = (now + timedelta(minutes=5)).isoformat()
    seed_job(
        conn,
        url_suffix="future-score-retry",
        fit_score=None,
        score_error="Temporary LLM failure",
        score_attempts=1,
        score_next_retry_at=future,
    )

    jobs = get_jobs_by_stage(conn=conn, stage="pending_score", max_age_days=14, limit=0)

    assert jobs == []


def test_pending_score_age_filter_excludes_stale_iso_timestamp(tmp_db, seed_job):
    conn = tmp_db()
    stale = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
    seed_job(
        conn,
        url_suffix="stale-score-job",
        fit_score=None,
        score_error=None,
        score_attempts=0,
        discovered_at=stale,
    )

    jobs = get_jobs_by_stage(conn=conn, stage="pending_score", max_age_days=14, limit=0)

    assert jobs == []
