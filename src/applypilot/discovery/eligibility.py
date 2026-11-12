"""Canonical eligibility normalization for the user's remote, non-full-time search.

Unknown values deliberately fail closed. This is a discovery gate, not a legal
work-authorisation determination.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

import yaml

_ALLOWED_EMPLOYMENT = {"part_time", "contract"}
_REMOTE_TERMS = ("remote", "work from home", "work-from-home", "wfh", "distributed", "anywhere")
_ONSITE_TERMS = ("on-site", "onsite", "in-person", "in person", "office-based", "office based")
_HYBRID_TERMS = ("hybrid",)

# Core English-speaking remote-work markets. Edit allowed_remote_countries in
# ~/.applypilot/searches.yaml to expand or narrow this list.
DEFAULT_ALLOWED_REMOTE_COUNTRIES = (
    "United Kingdom", "Ireland", "United States", "Canada",
    "Australia", "New Zealand", "South Africa", "Singapore",
)
_COUNTRY_ALIASES = {
    "United Kingdom": ("United Kingdom", "UK", "Great Britain", "England", "Scotland", "Wales", "Northern Ireland"),
    "Ireland": ("Ireland", "Republic of Ireland"),
    "United States": ("United States", "United States of America", "USA", "US", "U.S.", ),
    "Canada": ("Canada",),
    "Australia": ("Australia",),
    "New Zealand": ("New Zealand",),
    "South Africa": ("South Africa",),
    "Singapore": ("Singapore",),
}


@lru_cache(maxsize=1)
def configured_remote_countries() -> tuple[str, ...]:
    """Read the user's remote-country allowlist once per process."""
    try:
        from applypilot.config import load_search_config
        cfg = load_search_config() or {}
        countries = cfg.get("allowed_remote_countries", DEFAULT_ALLOWED_REMOTE_COUNTRIES)
        if isinstance(countries, str):
            countries = [countries]
        return tuple(str(country).strip() for country in countries if str(country).strip())
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        return DEFAULT_ALLOWED_REMOTE_COUNTRIES


def remote_country_allowed(
    location: Any, allowed_countries: Any = None, description: Any = None
) -> bool:
    """Require an allowlisted country in the location or explicit work-location wording."""
    if allowed_countries is None:
        allowed_values = configured_remote_countries()
    elif isinstance(allowed_countries, str):
        allowed_values = (allowed_countries,)
    else:
        allowed_values = tuple(allowed_countries)
    if not allowed_values:
        return True

    allowed_terms = set()
    for configured in allowed_values:
        configured_name = str(configured).strip().casefold()
        if not configured_name:
            continue
        matched_alias = False
        for canonical, aliases in _COUNTRY_ALIASES.items():
            if configured_name == canonical.casefold() or configured_name in {a.casefold() for a in aliases}:
                allowed_terms.update(aliases)
                matched_alias = True
                break
        if not matched_alias:
            # Custom country names are supported without requiring a code change.
            allowed_terms.add(str(configured).strip())

    location_text = _clean(location)
    description_text = _clean(description)
    for term in allowed_terms:
        country_pattern = re.escape(term.casefold())
        if location_text and re.search(r"(?<![a-z])" + country_pattern + r"(?![a-z])", location_text):
            return True
        # Only accept a country from a description when it is tied to explicit
        # work-location language; incidental office/customer mentions do not count.
        context_pattern = (
            r"\\b(?:remote(?:ly)?\\s+(?:in|from|within)|based\\s+in|located\\s+in|"
            r"reside\\s+in|residing\\s+in|work\\s+from|must\\s+be\\s+based\\s+in)\\s+"
            r"(?:the\\s+)?" + country_pattern + r"(?![a-z])"
        )
        if description_text and re.search(context_pattern, description_text):
            return True
    return False

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
    location_text = f"{loc} {raw}"

    # Explicit hybrid/on-site evidence from any source outranks a generic
    # remote boolean or remote label. This prevents contradictory listings
    # such as "Remote - UK" plus "hybrid role" from passing the gate.
    if "hybrid" in raw or any(term in location_text for term in _HYBRID_TERMS):
        return "hybrid"
    if re.search(r"\b(hybrid role|hybrid position|office attendance|days in the office)\b", desc):
        return "hybrid"

    if raw in {"on-site", "onsite", "office", "in-person", "in person", "in-office"}:
        return "onsite"
    if any(term in location_text for term in _ONSITE_TERMS):
        return "onsite"
    if re.search(r"\b(on[- ]site role|on[- ]site position|must work from our office|office based role)\b", desc):
        return "onsite"

    # Explicit remote wording can correct a contradictory aggregator boolean.
    if raw in {"remote", "fully remote", "fully-remote", "distributed"}:
        return "remote"
    if any(term in location_text for term in _REMOTE_TERMS):
        return "remote"
    if re.search(r"\b(fully remote|this is a remote role|remote position|work remotely|100% remote)\b", desc):
        return "remote"

    if remote_flag in {"true", "1", "yes"}:
        return "remote"
    if remote_flag in {"false", "0", "no"}:
        return "unknown"
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
    elif require_remote and not remote_country_allowed(job.get("location"), description=description):
        # A generic "Remote" label does not establish the country in which
        # the employer permits the worker to be based. Fail closed.
        reasons.append("remote_country_unverified")
    return {
        "employment_type": employment,
        "workplace_type": workplace,
        "eligibility_status": (
            "eligible" if not reasons
            else "unverified" if all(reason.endswith("_unverified") for reason in reasons)
            else "rejected"
        ),
        "eligibility_reason": ",".join(reasons) if reasons else None,
    }


def is_job_eligible(job: dict, allowed_employment: set[str] | None = None,
                    require_remote: bool = True) -> bool:
    return classify_job_eligibility(job, allowed_employment, require_remote)["eligibility_status"] == "eligible"
