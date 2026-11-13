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

def test_daily_cap_is_shared_with_ats_adapters_and_duplicates_still_repair(tmp_db, monkeypatch):
    from applypilot import config
    from applypilot.database import store_jobs
    from applypilot.discovery.ats_common import insert_normalized_jobs

    monkeypatch.setattr(
        config,
        "load_search_config",
        lambda: {"defaults": {"daily_unique_job_limit": 1, "daily_unique_timezone": "Europe/London"}},
    )
    conn = tmp_db()
    first_url = "https://example.com/jobs/first"
    second_url = "https://example.com/jobs/second"

    assert store_jobs(
        conn, [{"url": first_url, "title": "Existing listing"}], "test", "jobspy"
    ) == (1, 0)

    duplicate_and_new = [
        {
            "url": first_url,
            "title": "Existing listing",
            "employer_name": "Acme Analytics",
            "description": "Part-time remote data role",
            "full_description": "Part-time remote data role. " * 10,
            "location": "Remote - United Kingdom",
            "employment_type": "part_time",
            "workplace_type": "remote",
        },
        {
            "url": second_url,
            "title": "New listing",
            "employer_name": "Other Analytics",
            "description": "Part-time remote data role",
            "full_description": "Part-time remote data role. " * 10,
            "location": "Remote - United Kingdom",
            "employment_type": "part_time",
            "workplace_type": "remote",
        },
    ]

    new, existing = insert_normalized_jobs(
        conn, duplicate_and_new, "greenhouse", "greenhouse_api"
    )

    assert (new, existing) == (0, 1)
    row = conn.execute(
        "SELECT company, site FROM jobs WHERE url = ?", (first_url,)
    ).fetchone()
    assert row["company"] == "Acme Analytics"
    assert row["site"] == "test"
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_daily_cap_does_not_count_manual_tracking_stubs(tmp_db, monkeypatch):
    from datetime import datetime, timezone

    from applypilot import config
    from applypilot.database import store_jobs

    monkeypatch.setattr(
        config,
        "load_search_config",
        lambda: {"defaults": {"daily_unique_job_limit": 1, "daily_unique_timezone": "Europe/London"}},
    )
    conn = tmp_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO jobs (url, title, site, discovered_at) VALUES (?, ?, ?, ?)",
        ("manual://tracked/1", "Manually tracked role", "manual", now),
    )
    conn.commit()

    new, duplicates = store_jobs(
        conn,
        [{"url": "https://example.com/jobs/1", "title": "Discovered role"}],
        "test",
        "jobspy",
    )

    assert (new, duplicates) == (1, 0)
    assert conn.execute(
        "SELECT COUNT(*) FROM jobs WHERE strategy IS NOT NULL AND discovered_at >= ?",
        (now[:10],),
    ).fetchone()[0] == 1


def test_daily_cap_is_serialized_across_concurrent_discovery_writers(tmp_db, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from applypilot import config, database
    from applypilot.database import store_jobs

    monkeypatch.setattr(
        config,
        "load_search_config",
        lambda: {"defaults": {"daily_unique_job_limit": 1, "daily_unique_timezone": "Europe/London"}},
    )
    conn = tmp_db()
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    barrier = Barrier(2)

    def insert_one(index):
        worker_conn = database.get_connection(db_path)
        barrier.wait(timeout=5)
        return store_jobs(
            worker_conn,
            [{"url": f"https://example.com/concurrent/{index}", "title": f"Role {index}"}],
            "test",
            "jobspy",
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(insert_one, (1, 2)))

    assert sum(new for new, _ in results) == 1
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1

def test_daily_cap_is_enforced_by_every_direct_discovery_adapter(tmp_db, monkeypatch):
    import pandas as pd

    from applypilot import config
    from applypilot.database import store_jobs
    from applypilot.discovery.amazon import _insert_jobs as insert_amazon
    from applypilot.discovery.builtin import _insert_jobs as insert_builtin
    from applypilot.discovery.costco import _insert_jobs as insert_costco
    from applypilot.discovery.hackernews import _store_hn_job
    from applypilot.discovery.jobspy import store_jobspy_results
    from applypilot.discovery.smartextract import _store_jobs_filtered
    from applypilot.discovery.workday import store_results as store_workday

    monkeypatch.setattr(
        config,
        "load_search_config",
        lambda: {"defaults": {"daily_unique_job_limit": 1, "daily_unique_timezone": "Europe/London"}},
    )
    conn = tmp_db()
    assert store_jobs(
        conn,
        [{"url": "https://example.com/jobs/quota-seed", "title": "Quota seed"}],
        "test",
        "jobspy",
    ) == (1, 0)

    jobspy_df = pd.DataFrame([{
        "job_url": "https://indeed.com/jobs/quota-jobspy",
        "title": "Part-time Analyst",
        "company": "JobSpy Co",
        "location": "Remote - United Kingdom",
        "job_type": "parttime",
        "description": "Role summary",
        "site": "indeed",
        "is_remote": True,
    }])
    assert store_jobspy_results(conn, jobspy_df, "test") == (0, 0)

    workday_job = {
        "apply_url": "https://acme.wd5.myworkdayjobs.com/jobs/quota-workday",
        "title": "Part-time Analyst",
        "employer_name": "Acme",
        "location": "Remote - United Kingdom",
        "time_type": "part_time",
        "remote_type": "remote",
        "full_description": "Part-time remote analyst role. " * 10,
    }
    assert store_workday(conn, [workday_job], {"acme": {"name": "Acme"}}) == (0, 0)

    direct_job = {
        "url": "https://example.com/jobs/quota-direct",
        "title": "Part-time Analyst",
        "location": "Remote - United Kingdom",
        "employment_type": "part_time",
        "workplace_type": "remote",
        "description": "Role summary",
        "full_description": "Part-time remote analyst role. " * 10,
        "application_url": "https://example.com/jobs/quota-direct",
    }
    assert insert_amazon(conn, [direct_job]) == (0, 0)
    assert insert_costco(conn, [direct_job]) == (0, 0)

    builtin_job = {
        **direct_job,
        "url": "https://builtin.com/jobs/quota-builtin",
        "work_mode": "remote",
        "employer_name": "BuiltIn Co",
    }
    assert insert_builtin(conn, [builtin_job]) == (0, 0)

    smart_job = {
        **direct_job,
        "url": "https://example.com/jobs/quota-smart",
        "company": "Smart Co",
    }
    assert _store_jobs_filtered(
        conn, [smart_job], "Example", "json_ld", accept_locs=[], reject_locs=[]
    ) == (0, 0)

    hn_job = {
        "url": "https://example.com/jobs/quota-hn",
        "title": "Part-time Analyst",
        "company": "HN Co",
        "location": "Remote - United Kingdom",
        "remote": True,
        "employment_type": "parttime",
        "description": "Part-time remote analyst role. " * 10,
    }
    assert _store_hn_job(conn, hn_job, "Who is Hiring?") is False

    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1

def test_direct_adapter_canonicalizes_embedded_ats_url_before_deduplication(tmp_db, monkeypatch):
    import pandas as pd

    from applypilot import config
    from applypilot.database import store_jobs
    from applypilot.discovery.jobspy import store_jobspy_results

    monkeypatch.setattr(
        config,
        "load_search_config",
        lambda: {"defaults": {"daily_unique_job_limit": 1, "daily_unique_timezone": "Europe/London"}},
    )
    conn = tmp_db()
    canonical_url = "https://job-boards.greenhouse.io/stripe/jobs/123"
    assert store_jobs(
        conn,
        [{"url": canonical_url, "title": "Data Analyst"}],
        "test",
        "jobspy",
    ) == (1, 0)

    alias_df = pd.DataFrame([{
        "job_url": "https://stripe.com/careers?gh_jid=123",
        "title": "Data Analyst",
        "company": "Stripe",
        "location": "Remote - United Kingdom",
        "job_type": "parttime",
        "description": "Role summary",
        "site": "linkedin",
        "is_remote": True,
    }])

    assert store_jobspy_results(conn, alias_df, "test") == (0, 1)
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    row = conn.execute(
        "SELECT company FROM jobs WHERE url = ?", (canonical_url,)
    ).fetchone()
    assert row["company"] == "Stripe"
