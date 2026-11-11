from applypilot.apply.browser_agent import _profile_facts


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
    assert facts["work_authorization"]["legally_authorized_to_work"] == "Yes"


def test_profile_facts_handles_missing_sections():
    assert _profile_facts({}) == {
        "personal": {},
        "work_authorization": {},
        "availability": {},
        "experience": {},
        "skills_boundary": {},
    }
