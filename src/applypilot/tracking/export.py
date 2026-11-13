"""Export confirmed applications from the local ApplyPilot SQLite database.

The export never invents employer contact details. Known contact emails are
included only when they exist in tracking_people (for example, a recruiter
identified from a tracked email); otherwise the CSV field is blank.
"""
from __future__ import annotations

import csv
from pathlib import Path

from applypilot.config import DB_PATH, TRACKING_DIR
from applypilot.database import get_connection, init_db

EXPORT_FIELDS = [
    "applied_at",
    "application_status",
    "job_title",
    "company",
    "known_contact_emails",
    "job_description_url",
    "application_url",
    "location",
    "fit_score",
    "job_board",
    "tailored_resume_path",
]


def export_applications(output_path: str | Path | None = None) -> Path:
    """Write confirmed applications to CSV and return the output path."""
    init_db()
    destination = Path(output_path).expanduser() if output_path else TRACKING_DIR / "applications.csv"
    destination.parent.mkdir(parents=True, exist_ok=True)

    conn = get_connection()
    rows = conn.execute(
        """
        SELECT
            j.applied_at AS applied_at,
            COALESCE(j.apply_status, 'unknown') AS application_status,
            COALESCE(j.title, '') AS job_title,
            COALESCE(j.company, '') AS company,
            COALESCE((
                SELECT GROUP_CONCAT(DISTINCT NULLIF(TRIM(tp.email), ''))
                FROM tracking_people tp
                WHERE tp.job_url = j.url AND tp.email IS NOT NULL AND TRIM(tp.email) <> ''
            ), '') AS known_contact_emails,
            j.url AS job_description_url,
            COALESCE(j.application_url, j.url, '') AS application_url,
            COALESCE(j.location, '') AS location,
            j.fit_score AS fit_score,
            COALESCE(j.site, '') AS job_board,
            COALESCE(j.tailored_resume_path, '') AS tailored_resume_path
        FROM jobs j
        WHERE LOWER(COALESCE(j.apply_status, '')) = 'applied'
           OR j.applied_at IS NOT NULL
        ORDER BY j.applied_at DESC, j.discovered_at DESC
        """
    ).fetchall()

    with destination.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXPORT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))

    return destination
