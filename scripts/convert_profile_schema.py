"""Convert the newer profile.json layout to ApplyPilot's runtime schema.

Usage:
  python scripts/convert_profile_schema.py --input profile.json
  python scripts/convert_profile_schema.py --input profile.json --output ~/.applypilot/profile.json

The converter preserves the supplied facts and never guesses missing values.
Review the output before using ApplyPilot's browser workflow.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def convert(source: dict[str, Any]) -> dict[str, Any]:
    info = source.get("personal_info") or {}
    location = info.get("location") or {}
    links = info.get("links") or {}
    auth = source.get("application_defaults") or {}
    exp_items = source.get("experience") or []
    education_items = source.get("education") or []
    skills = source.get("skills") or {}

    experience = exp_items[0] if exp_items and isinstance(exp_items[0], dict) else {}
    education = education_items[0] if education_items and isinstance(education_items[0], dict) else {}

    linkedin = links.get("linkedin") or ""
    if "YOUR_" in linkedin.upper():
        linkedin = ""

    salary = str(auth.get("desired_salary_gbp") or "")
    projects = source.get("projects") or []
    project_names = [p.get("name", "") for p in projects if isinstance(p, dict) and p.get("name")]

    return {
        "personal": {
            "full_name": info.get("full_name") or " ".join(
                part for part in (info.get("first_name"), info.get("last_name")) if part
            ),
            "email": info.get("email", ""),
            "phone": info.get("phone", ""),
            "city": location.get("city", ""),
            "country": location.get("country", ""),
            "address": "",
            "postal_code": location.get("postcode", ""),
            "linkedin_url": linkedin,
            "github_url": links.get("github", ""),
            "portfolio_url": links.get("portfolio", ""),
            "password": "",
        },
        "work_authorization": {
            "legally_authorized": auth.get("work_authorization_uk"),
            "needs_sponsorship": auth.get("requires_visa_sponsorship"),
        },
        "compensation": {
            "salary_expectation": salary,
            "salary_currency": "GBP",
            "salary_range_min": "",
            "salary_range_max": "",
        },
        "experience": {
            "years_of_experience_total": "",
            "education_level": education.get("qualification", ""),
            "current_title": experience.get("title", ""),
        },
        "skills_boundary": {
            "programming_languages": skills.get("languages", []),
            "frameworks": skills.get("libraries_and_frameworks", []),
            "databases": skills.get("databases", []),
            "tools": skills.get("tools_and_platforms", []),
            "core_competencies": skills.get("core_competencies", []),
        },
        "resume_facts": {
            "preserved_companies": [experience.get("company", "")] if experience.get("company") else [],
            "preserved_projects": project_names,
            "preserved_school": education.get("institution", ""),
            "real_metrics": [],
        },
        "availability": {
            "earliest_start_date": auth.get("notice_period", ""),
        },
        "eeo_voluntary": source.get("eeo_voluntary") or {
            "gender": "Decline to self-identify",
            "ethnicity": "Decline to self-identify",
            "veteran_status": "Decline to self-identify",
            "disability_status": "Decline to self-identify",
        },
        # Retain the richer source sections for future prompts and auditing.
        "candidate_headline": source.get("headline", ""),
        "candidate_summary": source.get("summary", ""),
        "work_history": exp_items,
        "education_history": education_items,
        "projects": projects,
        "target_roles": source.get("target_roles", []),
        "remote_preference": auth.get("remote_preference", "Remote"),
        "contract_types": auth.get("contract_types", []),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="Path to the newer profile JSON.")
    parser.add_argument(
        "--output",
        default=str(Path.home() / ".applypilot" / "profile.converted.json"),
        help="Output path (default: ~/.applypilot/profile.converted.json; review before replacing profile.json).",
    )
    args = parser.parse_args()

    input_path = Path(args.input).expanduser()
    output_path = Path(args.output).expanduser()
    data = json.loads(input_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SystemExit("Input profile must be a JSON object.")

    converted = convert(data)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(converted, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Converted profile written to: {output_path}")
    print("Review every field, especially UK work authorisation, sponsorship, education, experience and salary.")


if __name__ == "__main__":
    main()
