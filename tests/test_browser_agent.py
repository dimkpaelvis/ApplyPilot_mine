from applypilot.apply.browser_agent import _placeholder_paths, _profile_facts


def test_profile_facts_excludes_password_and_identity_secrets():
    profile = {
        "personal": {
            "full_name": "Example User",
            "email": "user@example.test",
            "password": "must-not-leak",
            "ssn": "000-00-0000",
            "national_id": "secret-id",
        },
        "work_authorization": {"legally_authorized_to_work": "Yes"},
        "availability": {"available_for_full_time": "Yes"},
        "experience": {"target_role": "Junior Data Analyst"},
        "skills_boundary": {"languages": ["SQL", "Python"]},
    }

    facts = _profile_facts(profile)

    assert facts["personal"] == {
        "full_name": "Example User",
        "email": "user@example.test",
    }
    assert "password" not in str(facts)
    assert "000-00-0000" not in str(facts)
    assert "secret-id" not in str(facts)
    assert "work_authorization" not in facts


def test_profile_facts_handles_missing_sections():
    assert _profile_facts({}) == {
        "personal": {},
        "availability": {},
        "experience": {},
        "skills_boundary": {},
    }


def test_placeholder_profile_values_are_detected_before_apply():
    profile = {
        "personal": {"full_name": "YOUR_LEGAL_NAME", "email": "user@example.test"},
        "experience": {"target_role": "Junior Data Analyst"},
    }

    assert _placeholder_paths(profile) == ["personal.full_name"]


def test_real_profile_values_are_not_flagged():
    profile = {
        "personal": {"full_name": "Alex Example", "email": "alex@example.com"},
        "experience": {"target_role": "Junior Data Analyst"},
    }

    assert _placeholder_paths(profile) == []


def test_manual_review_job_requires_explicit_sheet_approval(tmp_db, seed_job, monkeypatch):
    from applypilot.apply import browser_agent

    conn = tmp_db()
    row = seed_job(
        conn,
        decision_route="manual_review",
        review_decision=None,
        eligibility="eligible",
        eligibility_status="eligible",
        applicant_eligibility="eligible",
        candidate_suitability="suitable",
    )
    monkeypatch.setattr(browser_agent, "get_connection", lambda: conn)

    pending = browser_agent._load_jobs(10, 8, 0, None, None)
    assert pending == []

    conn.execute(
        "UPDATE jobs SET review_decision = 'approved' WHERE url = ?",
        (row["url"],),
    )
    conn.commit()

    approved = browser_agent._load_jobs(10, 8, 0, None, None)
    assert len(approved) == 1
    assert approved[0]["url"] == row["url"]


def test_auto_apply_queue_requires_auto_apply_qualification(tmp_db, seed_job, monkeypatch):
    from applypilot.apply import browser_agent

    conn = tmp_db()
    row = seed_job(
        conn,
        decision_route="auto_apply_queue",
        auto_apply_qualified=0,
        eligibility="eligible",
        eligibility_status="eligible",
        applicant_eligibility="eligible",
        candidate_suitability="suitable",
    )
    monkeypatch.setattr(browser_agent, "get_connection", lambda: conn)

    assert browser_agent._load_jobs(10, 8, 0, None, None) == []

    conn.execute(
        "UPDATE jobs SET auto_apply_qualified = 1 WHERE url = ?",
        (row["url"],),
    )
    conn.commit()

    approved = browser_agent._load_jobs(10, 8, 0, None, None)
    assert len(approved) == 1
    assert approved[0]["url"] == row["url"]
