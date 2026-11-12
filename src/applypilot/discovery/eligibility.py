"""Canonical eligibility normalization for the user's remote, non-full-time search.

Unknown values deliberately fail closed. This is a discovery gate, not a legal
work-authorisation determination.
"""
from __future__ import annotations

import re
from typing import Any

_ALLOWED_EMPLOYMENT = {"part_time", "contract"}
_REMOTE_TERMS = ("remote", "work from home", "work-from-home", "wfh", "distributed", "anywhere")
_ONSITE_TERMS = ("on-site", "onsite", "in-person", "in person", "office-based", "office based")
_HYBRID_TERMS = ("hybrid",)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip().lower()
    return "" if text in {"", "none", "nan", "null", "n/a", "unknown"} else text


def normalize_employment_type(value: Any = None, description: str = "", title: str = "") -> str:
    """Return part_time, contract, full_time, other, or unknown."""
    raw = _clean(value)
    text = " ".join(part for part in (raw, _clean(title), _clean(description)) if part)
    compact = re.sub(r"[_-]+", " ", text)

    if re.search(r"\b(full[ -]?time|fulltime|permanent full[ -]?time)\b", compact):
        return "full_time"
    if re.search(r"\b(part[ -]?time|parttime)\b", compact):
        return "part_time"
    if re.search(r"\b(freelance|freelancer|independent contractor|contractor|contract role|contract position|fixed[ -]?term contract)\b", compact):
        return "contract"
    if re.search(r"\b(internship|intern|temporary|temp|apprenticeship|apprentice|volunteer)\b", compact):
        return "other"

    # Structured ATS values are often concise ("contract", "parttime").
    if raw in {"contract", "contractor", "freelance", "freelancer"}:
        return "contract"
    if raw in {"parttime", "part time", "part-time"}:
        return "part_time"
    if raw in {"fulltime", "full time", "full-time", "permanent"}:
        return "full_time"
    return "unknown"


def normalize_workplace_type(value: Any = None, is_remote: Any = None,
                             location: str = "", description: str = "") -> str:
    """Return remote, hybrid, onsite, or unknown."""
    raw = _clean(value)
    loc = _clean(location)
    desc = _clean(description)
    remote_flag = _clean(is_remote)
    if remote_flag in {"true", "1", "yes"}:
        return "remote"
    if remote_flag in {"false", "0", "no"}:
        if raw in {"hybrid", "hybrid remote"}:
            return "hybrid"
        if raw in {"on-site", "onsite", "office", "in-person", "in person"}:
            return "onsite"
        return "unknown"
    if raw in {"remote", "fully remote", "fully-remote", "distributed"}:
        return "remote"
    if raw in {"hybrid", "hybrid remote"}:
        return "hybrid"
    if raw in {"on-site", "onsite", "office", "in-person", "in person"}:
        return "onsite"

    location_text = f"{loc} {raw}"
    if any(term in location_text for term in _REMOTE_TERMS):
        return "remote"
    if any(term in location_text for term in _HYBRID_TERMS):
        return "hybrid"
    if any(term in location_text for term in _ONSITE_TERMS):
        return "onsite"

    # Description fallback only uses explicit workplace phrases, not incidental
    # mentions such as "we support remote teams".
    if re.search(r"\b(fully remote|this is a remote role|remote position|work remotely|100% remote)\b", desc):
        return "remote"
    if re.search(r"\b(hybrid role|hybrid position|office attendance|days in the office)\b", desc):
        return "hybrid"
    if re.search(r"\b(on[- ]site role|on[- ]site position|must work from our office|office based role)\b", desc):
        return "onsite"
    return "unknown"


def classify_job_eligibility(job: dict, allowed_employment: set[str] | None = None,
                             require_remote: bool = True) -> dict:
    """Add canonical employment/workplace fields and a fail-closed decision."""
    allowed = allowed_employment or _ALLOWED_EMPLOYMENT
    description = job.get("full_description") or job.get("description") or ""
    employment = normalize_employment_type(
        job.get("employment_type") or job.get("job_type") or job.get("time_type"),
        description, job.get("title") or "",
    )
    workplace = normalize_workplace_type(
        job.get("workplace_type") or job.get("remote_type") or job.get("workplaceType"),
        job.get("is_remote"), job.get("location") or "", description,
    )
    reasons = []
    if employment == "unknown":
        reasons.append("employment_type_unverified")
    elif employment not in allowed:
        reasons.append(f"employment_type_{employment}")
    if workplace == "unknown":
        reasons.append("workplace_type_unverified")
    elif require_remote and workplace != "remote":
        reasons.append(f"workplace_type_{workplace}")
    return {
        "employment_type": employment,
        "workplace_type": workplace,
        "eligibility_status": "eligible" if not reasons else "rejected",
        "eligibility_reason": ",".join(reasons) if reasons else None,
    }


def is_job_eligible(job: dict, allowed_employment: set[str] | None = None,
                    require_remote: bool = True) -> bool:
    return classify_job_eligibility(job, allowed_employment, require_remote)["eligibility_status"] == "eligible"
