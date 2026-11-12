"""Applicant eligibility and candidate suitability decision engine.

This module deliberately separates:
- discovery eligibility (remote + employment type + searchable market),
- applicant eligibility (whether the candidate may work from the role's
  explicitly stated location),
- candidate suitability (seniority/experience fit),
- flexible preferences (ranking and auto-apply routing).

Unknown evidence is never treated as a pass. This is not legal advice and does
not determine immigration or right-to-work status.
"""
from __future__ import annotations

import re
from typing import Any

from applypilot.config import load_search_config

_COUNTRY_ALIASES = {
    "united kingdom": ("united kingdom", "uk", "great britain", "england", "scotland", "wales", "northern ireland"),
    "ireland": ("ireland", "republic of ireland"),
    "united states": ("united states", "united states of america", "usa", "u.s.a.", "us", "u.s."),
    "canada": ("canada",),
    "australia": ("australia",),
    "new zealand": ("new zealand",),
    "south africa": ("south africa",),
    "singapore": ("singapore",),
}


def _text(value: Any) -> str:
    return str(value or "").strip().casefold()


def _country_terms(country: str) -> tuple[str, ...]:
    key = _text(country)
    return _COUNTRY_ALIASES.get(key, (key,)) if key else ()


def _contains_country(text: str, country: str) -> bool:
    return any(
        re.search(r"(?<![a-z])" + re.escape(term) + r"(?![a-z])", text)
        for term in _country_terms(country)
    )


def _profile_country(profile: dict, cfg: dict) -> str:
    matching = cfg.get("matching", {})
    return str(
        matching.get("candidate_country")
        or profile.get("personal", {}).get("country")
        or ""
    ).strip()


def _explicit_country_restriction(text: str, candidate_country: str) -> str | None:
    """Return a different country only when the listing explicitly restricts applicants to it."""
    countries = list(_COUNTRY_ALIASES)
    # Add common aliases as candidates, but resolve each back to its canonical name.
    for canonical, aliases in _COUNTRY_ALIASES.items():
        for alias in aliases:
            countries.append(alias)
    for country in sorted(set(countries), key=len, reverse=True):
        if _contains_country(text, country) and not _contains_country(text, candidate_country):
            escaped = re.escape(country)
            patterns = (
                rf"(?:must|need to|required to|only|applicants|candidates).{{0,80}}"
                rf"(?:be based|based|located|reside|live|sit) in (?:the )?{escaped}",
                rf"(?:remote|work) (?:only )?(?:within|from) (?:the )?{escaped}",
                rf"(?:{escaped}) (?:residents|based applicants) only",
            )
            if any(re.search(pattern, text) for pattern in patterns):
                canonical = next(
                    (name for name, aliases in _COUNTRY_ALIASES.items()
                     if country == name or country in aliases),
                    country,
                )
                return canonical
    return None


def _years_required(text: str) -> float | None:
    patterns = (
        r"(?:at least|minimum of|min\.?|no less than)\s*(\d+(?:\.\d+)?)\s*\+?\s*years?",
        r"\b(\d+(?:\.\d+)?)\s*\+\s*years?(?: of experience| experience| exp)?",
        r"\b(\d+(?:\.\d+)?)\s+years? of (?:relevant |professional )?experience",
    )
    values = []
    for pattern in patterns:
        values.extend(float(match) for match in re.findall(pattern, text))
    return max(values) if values else None


def _years_available(profile: dict) -> float | None:
    raw = profile.get("experience", {}).get("years_of_experience_total")
    match = re.search(r"\d+(?:\.\d+)?", str(raw or ""))
    return float(match.group()) if match else None


def _profile_skills(profile: dict) -> set[str]:
    boundary = profile.get("skills_boundary", {})
    values: list[str] = []
    for key in ("programming_languages", "languages", "frameworks", "tools", "platforms", "databases"):
        raw = boundary.get(key) or []
        values.extend([str(item) for item in raw] if isinstance(raw, list) else [str(raw)])
    return {item.strip().casefold() for item in values if item.strip()}


def _evaluate_applicant(job: dict, profile: dict, cfg: dict) -> tuple[str, list[str]]:
    matching = cfg.get("matching", {})
    candidate_country = _profile_country(profile, cfg)
    location = _text(job.get("location"))
    description = _text(job.get("full_description") or job.get("description"))
    combined = f"{location}\n{description}"
    reasons: list[str] = []

    if not candidate_country:
        return "unverified", ["candidate_country_not_configured"]

    excluded_country = _explicit_country_restriction(combined, candidate_country)
    if excluded_country:
        return "rejected", [f"applicants_restricted_to_{excluded_country.replace(' ', '_')}"]

    # A US/other city + Remote is not evidence that UK residents may apply.
    if _contains_country(location, candidate_country):
        status = "eligible"
        reasons.append("candidate_country_present_in_job_location")
    else:
        acceptance_patterns = (
            r"(?:remote|work) (?:role )?(?:available|open) to (?:applicants|candidates) in ",
            r"(?:applicants|candidates) (?:must|can|may) be based in ",
            r"(?:remote|work) (?:within|from) ",
            r"(?:open to|accepting) applicants from ",
        )
        explicitly_accepted = any(re.search(p, combined) for p in acceptance_patterns) and _contains_country(
            combined, candidate_country
        )
        if explicitly_accepted:
            status = "eligible"
            reasons.append("explicit_candidate_country_acceptance")
        else:
            status = "unverified"
            reasons.append("candidate_country_acceptance_not_established")

    timezone_terms = ("must sit in est", "must sit in cst", "must be in est", "must be in cst",
                      "est or cst time zones", "eastern or central time zone")
    if any(term in combined for term in timezone_terms):
        reasons.append("timezone_constraint_requires_candidate_confirmation")
        if matching.get("timezone_policy", "review") == "reject":
            return "rejected", reasons
        if status == "eligible":
            status = "unverified"

    # Work-authorisation claims are only assessed when the profile explicitly
    # records an answer for that same country. Never infer from citizenship.
    if re.search(r"(?:must|need to be|requires? you to be) (?:legally )?authorized to work in", combined):
        auth = profile.get("work_authorization", {})
        auth_country = _text(auth.get("country"))
        if auth_country and _contains_country(combined, auth_country):
            if auth.get("legally_authorized") is False:
                return "rejected", reasons + ["work_authorisation_requirement_not_met"]
            if auth.get("legally_authorized") is not True:
                status = "unverified"
                reasons.append("work_authorisation_unverified")
        else:
            status = "unverified"
            reasons.append("country_specific_work_authorisation_unverified")

    return status, reasons


def evaluate_job_match(job: dict, profile: dict, fit_score: int | None = None,
                       config: dict | None = None) -> dict:
    """Evaluate applicant eligibility, suitability, flexible preferences and route."""
    cfg = config if config is not None else (load_search_config() or {})
    matching = cfg.get("matching", {})
    strict = matching.get("strict", {})
    flexible = matching.get("flexible", {})
    description = _text(job.get("full_description") or job.get("description"))
    title = _text(job.get("title"))
    combined = f"{title}\n{description}"

    applicant_status, applicant_reasons = _evaluate_applicant(job, profile, cfg)

    # Explicit required experience is a deterministic suitability gate only
    # when the candidate's experience is known. Missing profile evidence means
    # review, not an invented pass or rejection.
    required_years = _years_required(combined)
    candidate_years = _years_available(profile)
    suitability_reasons: list[str] = []
    if required_years is not None and candidate_years is None:
        suitability = "review"
        suitability_reasons.append("candidate_years_of_experience_not_configured")
    elif required_years is not None and candidate_years is not None and candidate_years < required_years:
        suitability = "unsuitable"
        suitability_reasons.append(
            f"requires_{required_years:g}_years_candidate_has_{candidate_years:g}"
        )
    else:
        suitability = "suitable"
        if required_years is not None:
            suitability_reasons.append("required_experience_threshold_met")
        else:
            suitability_reasons.append("no_explicit_minimum_experience_detected")

    # Flexible match: role-family and skill preferences are independently
    # scored. Only configured categories are considered; empty preferences
    # cannot silently make a job an auto-apply match.
    role_terms = [str(x).casefold() for x in flexible.get("role_keywords", [])]
    preferred_skills = [str(x).casefold() for x in flexible.get("preferred_skills", [])]
    role_configured = bool(role_terms)
    skills_configured = bool(preferred_skills)
    role_met = any(term in title for term in role_terms) if role_configured else None
    skills = _profile_skills(profile)
    job_skill_hits = [skill for skill in preferred_skills if skill in skills and skill in combined]
    skills_met = bool(job_skill_hits) if skills_configured else None

    score_parts = []
    if role_configured:
        score_parts.append(50 if role_met else 0)
    if skills_configured:
        score_parts.append(round(50 * len(job_skill_hits) / len(preferred_skills)))
    if fit_score is not None:
        min_fit = int(flexible.get("minimum_fit_score", 8))
        score_parts.append(100 if fit_score >= min_fit else round(100 * fit_score / max(min_fit, 1)))
    flexible_score = round(sum(score_parts) / len(score_parts)) if score_parts else 0

    configured_flexible_checks = [value for value in (role_met, skills_met) if value is not None]
    all_flexible_met = bool(configured_flexible_checks) and all(configured_flexible_checks)
    if fit_score is not None:
        all_flexible_met = all_flexible_met and fit_score >= int(flexible.get("minimum_fit_score", 8))
    elif flexible.get("minimum_fit_score") is not None:
        all_flexible_met = False

    if applicant_status == "rejected" or suitability == "unsuitable":
        route = "rejected"
    elif applicant_status != "eligible" or suitability == "review":
        route = "review_required"
    elif all_flexible_met:
        route = "auto_apply_queue" if matching.get("auto_apply", {}).get("enabled", False) else "manual_review"
    else:
        route = "manual_review"

    auto_apply_qualified = (
        route == "auto_apply_queue"
        and bool(matching.get("auto_apply", {}).get("enabled", False))
        and strict.get("require_explicit_applicant_country", True)
    )

    return {
        "applicant_eligibility": applicant_status,
        "applicant_eligibility_reason": ",".join(applicant_reasons) or None,
        "candidate_suitability": suitability,
        "suitability_score": 100 if suitability == "suitable" else 0 if suitability == "unsuitable" else 50,
        "flexible_score": flexible_score,
        "suitability_reason": ",".join(suitability_reasons) or None,
        "flexible_criteria_met": int(all_flexible_met),
        "decision_route": route,
        "auto_apply_qualified": int(auto_apply_qualified),
    }
