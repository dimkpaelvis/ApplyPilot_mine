"""Google Sheets review queue integration.

SQLite remains the source of truth. Sheets is a human review surface:
- export scored, non-rejected jobs idempotently;
- import explicit review decisions without changing candidate facts;
- never submit an application from a spreadsheet edit.
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from applypilot.config import load_env
from applypilot.database import get_connection, write_with_retry

log = logging.getLogger(__name__)

HEADERS = [
    "Job ID",
    "Title",
    "Company",
    "Location",
    "Employment Type",
    "Workplace Type",
    "Fit Score",
    "Applicant Eligibility",
    "Applicant Eligibility Reason",
    "Candidate Suitability",
    "Suitability Reason",
    "Flexible Score",
    "Decision Route",
    "Posting URL",
    "Discovered At",
    "Application Status",
    "Review Decision",
    "Review Notes",
    "Last Synced At",
]
MANAGED_COLUMNS = 16  # A:P are owned by ApplyPilot; Q:R are owned by the reviewer; S is sync metadata.
VALID_DECISIONS = {
    "approve": "approved",
    "approved": "approved",
    "reject": "rejected",
    "rejected": "rejected",
    "hold": "hold",
    "review": "hold",
    "needs_review": "hold",
}


def spreadsheet_id_from_value(value: str) -> str:
    """Accept either a spreadsheet ID or its canonical Google Sheets URL."""
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("GOOGLE_SHEET_ID is missing.")
    match = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", raw)
    if match:
        return match.group(1)
    if "://" in raw or "/" in raw:
        raise ValueError("GOOGLE_SHEET_ID must be a spreadsheet ID or a Google Sheets URL.")
    return raw


def _credentials_info() -> dict:
    load_env()
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not set.")
    try:
        info = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON.") from exc
    if not isinstance(info, dict) or not info.get("client_email") or not info.get("private_key"):
        raise RuntimeError("Google service-account JSON is missing required fields.")
    return info


def _spreadsheet_id() -> str:
    load_env()
    return spreadsheet_id_from_value(os.environ.get("GOOGLE_SHEET_ID", ""))


def _build_service():
    """Build a Sheets API client without logging credentials."""
    try:
        from google.oauth2.service_account import Credentials
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "Google Sheets dependencies are missing. Reinstall ApplyPilot dependencies."
        ) from exc

    credentials = Credentials.from_service_account_info(
        _credentials_info(),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return build("sheets", "v4", credentials=credentials, cache_discovery=False)


def _quote_tab(tab: str) -> str:
    return "'" + tab.replace("'", "''") + "'"


def _ensure_tab(service, spreadsheet_id: str, tab: str) -> int:
    metadata = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="sheets(properties(sheetId,title))",
    ).execute()
    for item in metadata.get("sheets", []):
        props = item.get("properties", {})
        if props.get("title") == tab:
            return int(props["sheetId"])

    response = service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": tab}}}]},
    ).execute()
    return int(response["replies"][0]["addSheet"]["properties"]["sheetId"])


def test_connection(service=None, spreadsheet_id: str | None = None) -> str:
    """Verify API write access using a temporary tab, then remove that tab."""
    service = service or _build_service()
    spreadsheet_id = spreadsheet_id or _spreadsheet_id()
    tab = "_ApplyPilot_Test_" + uuid.uuid4().hex[:12]
    sheet_id = _ensure_tab(service, spreadsheet_id, tab)
    quoted = _quote_tab(tab)
    try:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{quoted}!A1",
            valueInputOption="RAW",
            body={"values": [["ApplyPilot write-access test"]]},
        ).execute()
        service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id,
            range=f"{quoted}!A1",
            body={},
        ).execute()
    finally:
        # The tab has a unique per-run name, so it cannot be an existing user tab.
        try:
            service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": [{"deleteSheet": {"sheetId": sheet_id}}]},
            ).execute()
        except Exception:
            log.warning("Could not remove temporary Google Sheets test tab %s.", tab)

    metadata = service.spreadsheets().get(
        spreadsheetId=spreadsheet_id,
        fields="properties.title",
    ).execute()
    return str(metadata.get("properties", {}).get("title", "Google spreadsheet"))


def _cell(value: Any) -> str | int | float:
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return value
    return str(value)


def _job_values(row: Any, synced_at: str) -> list:
    get = row.__getitem__
    return [
        _cell(get("url")),
        _cell(get("title")),
        _cell(get("company")),
        _cell(get("location")),
        _cell(get("employment_type")),
        _cell(get("workplace_type")),
        _cell(get("fit_score")),
        _cell(get("applicant_eligibility")),
        _cell(get("applicant_eligibility_reason")),
        _cell(get("candidate_suitability")),
        _cell(get("suitability_reason")),
        _cell(get("flexible_score")),
        _cell(get("decision_route")),
        _cell(get("url")),
        _cell(get("discovered_at")),
        _cell(get("apply_status")),
        "",  # reviewer-owned decision; never overwrite on sync
        "",  # reviewer-owned notes; never overwrite on sync
        synced_at,
    ]


def _normalise_decision(value: Any) -> str | None:
    return VALID_DECISIONS.get(str(value or "").strip().casefold())


def import_review_decisions(conn=None, service=None, spreadsheet_id: str | None = None,
                            tab: str = "Job Review") -> dict:
    """Import reviewer decisions from Q:R into SQLite, preserving notes."""
    conn = conn or get_connection()
    service = service or _build_service()
    spreadsheet_id = spreadsheet_id or _spreadsheet_id()
    _ensure_tab(service, spreadsheet_id, tab)
    values = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{_quote_tab(tab)}!A1:S",
    ).execute().get("values", [])

    changed = 0
    ignored = 0
    now = datetime.now(timezone.utc).isoformat()
    for row in values[1:]:
        url = str(row[0]).strip() if row else ""
        if not url:
            continue
        decision = _normalise_decision(row[16] if len(row) > 16 else "")
        notes = str(row[17]).strip() if len(row) > 17 else ""
        if decision is None:
            ignored += 1
            continue
        existing = conn.execute(
            "SELECT review_decision, review_notes FROM jobs WHERE url = ?", (url,)
        ).fetchone()
        if existing is None:
            ignored += 1
            continue
        old_decision = existing["review_decision"]
        old_notes = existing["review_notes"] or ""
        if old_decision == decision and old_notes == notes:
            continue

        def _update():
            conn.execute(
                "UPDATE jobs SET review_decision = ?, review_notes = ?, reviewed_at = ? WHERE url = ?",
                (decision, notes, now, url),
            )
        write_with_retry(conn, _update)
        changed += 1

    return {"updated": changed, "ignored": ignored}


def sync_to_sheet(conn=None, service=None, spreadsheet_id: str | None = None,
                  tab: str = "Job Review", dry_run: bool = False) -> dict:
    """Upsert all evaluated jobs into the review tab without overwriting reviewer cells."""
    conn = conn or get_connection()
    jobs = conn.execute(
        """
        SELECT url, title, company, location, employment_type, workplace_type,
               fit_score, applicant_eligibility, applicant_eligibility_reason,
               candidate_suitability, suitability_reason, flexible_score,
               decision_route, discovered_at, apply_status
          FROM jobs
         WHERE decision_route IN ('manual_review', 'review_required', 'auto_apply_queue')
         ORDER BY discovered_at DESC, fit_score DESC
        """
    ).fetchall()

    if dry_run:
        return {"eligible_rows": len(jobs), "updated": 0, "appended": 0, "dry_run": True}

    service = service or _build_service()
    spreadsheet_id = spreadsheet_id or _spreadsheet_id()
    sheet_id = _ensure_tab(service, spreadsheet_id, tab)
    quoted = _quote_tab(tab)
    current = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{quoted}!A1:S",
    ).execute().get("values", [])

    if not current:
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{quoted}!A1:S1",
            valueInputOption="RAW",
            body={"values": [HEADERS]},
        ).execute()
        current = [HEADERS]
    elif current[0][:len(HEADERS)] != HEADERS:
        raise RuntimeError(
            f"Tab '{tab}' has an unexpected header. Rename the tab or back it up before syncing; "
            "ApplyPilot will not overwrite an unknown sheet layout."
        )

    row_by_url = {}
    for index, existing in enumerate(current[1:], start=2):
        if existing and existing[0]:
            row_by_url[str(existing[0]).strip()] = index

    now = datetime.now(timezone.utc).isoformat()
    updates = []
    new_rows = []
    updated = 0
    for job in jobs:
        values = _job_values(job, now)
        row_number = row_by_url.get(str(job["url"]))
        if row_number:
            updates.append({
                "range": f"{quoted}!A{row_number}:P{row_number}",
                "values": [values[:MANAGED_COLUMNS]],
            })
            updates.append({
                "range": f"{quoted}!S{row_number}:S{row_number}",
                "values": [[now]],
            })
            updated += 1
        else:
            new_rows.append(values)

    if updates:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"valueInputOption": "RAW", "data": updates},
        ).execute()
    if new_rows:
        service.spreadsheets().values().append(
            spreadsheetId=spreadsheet_id,
            range=f"{quoted}!A:S",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": new_rows},
        ).execute()

    # Constrain reviewer decisions to the documented values.
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{
            "setDataValidation": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": 1,
                    "endRowIndex": max(1000, len(current) + len(new_rows) + 1),
                    "startColumnIndex": 16,
                    "endColumnIndex": 17,
                },
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [
                            {"userEnteredValue": "approve"},
                            {"userEnteredValue": "reject"},
                            {"userEnteredValue": "hold"},
                        ],
                    },
                    "showCustomUi": True,
                    "strict": True,
                },
            }
        }]},
    ).execute()

    return {
        "eligible_rows": len(jobs),
        "updated": updated,
        "appended": len(new_rows),
        "dry_run": False,
    }


def sync_review_queue(conn=None, service=None, spreadsheet_id: str | None = None,
                      tab: str = "Job Review", dry_run: bool = False) -> dict:
    """Import reviewer edits first, then refresh ApplyPilot-owned cells."""
    if dry_run:
        return sync_to_sheet(
            conn=conn, service=service, spreadsheet_id=spreadsheet_id, tab=tab, dry_run=True
        )
    conn = conn or get_connection()
    service = service or _build_service()
    spreadsheet_id = spreadsheet_id or _spreadsheet_id()
    imported = import_review_decisions(
        conn=conn, service=service, spreadsheet_id=spreadsheet_id, tab=tab
    )
    exported = sync_to_sheet(
        conn=conn, service=service, spreadsheet_id=spreadsheet_id, tab=tab, dry_run=False
    )
    return {"imported_decisions": imported, **exported}
