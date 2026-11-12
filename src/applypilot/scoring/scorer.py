"""Job fit scoring: LLM-powered evaluation of candidate-job match quality.

Scores jobs on a 1-10 scale by comparing the user's resume against each
job description. All personal data is loaded at runtime from the user's
profile and resume file.
"""

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from applypilot.config import RESUME_PATH, load_profile
from applypilot.database import get_connection, get_jobs_by_stage, write_with_retry
from applypilot.llm import get_client

log = logging.getLogger(__name__)


# ── Scoring Prompt ────────────────────────────────────────────────────────

SCORE_PROMPT_TEMPLATE = """You are a rigorous UK-focused recruiter assessing a candidate for junior data, analytics, reporting and data operations roles.

CANDIDATE PROFILE:
{candidate_summary}

LOCATION AND ELIGIBILITY:
The candidate is based in Sheerness, Kent, United Kingdom. This search targets remote roles that can be performed from the UK. On-site and hybrid roles are not suitable; mark them ELIGIBILITY: non_remote_only. Prefer roles explicitly open to UK-based applicants; do not assume a role is UK-remote merely because it says "remote". Mark ELIGIBILITY: non_uk_only only when the posting clearly excludes UK-based applicants. If location eligibility is unclear, use eligible and explain the uncertainty.

EMPLOYMENT TYPE:
The candidate wants PART-TIME and FREELANCE/CONTRACT work. Full-time roles are not suitable and must be marked ELIGIBILITY: full_time_only when the posting explicitly identifies the role as full-time. Treat freelance, freelancer, independent contractor and contract engagements as contract work. Do not mistake a full-time role's mention of flexible hours, remote work, or a part-time team member for a part-time vacancy. If employment type is not stated, return ELIGIBILITY: unverified; do not assume it is part-time. If workplace type is not explicitly remote, return ELIGIBILITY: unverified or non_remote_only as appropriate. Never mark an unknown employment or workplace type eligible. This eligibility label does not establish the candidate's right to work or visa status.

TARGET ROLE FAMILIES:
- Junior / entry-level Data Analyst
- Data Operations Analyst / Data Administrator
- Data Quality Analyst / Data Quality Assistant
- Reporting Analyst / SQL Analyst
- Junior BI Analyst
- Junior Data Engineer (stretch target)

SCORING:
10: Exceptional match for the candidate's actual evidence and the role's level.
9: Very strong match; most required tools and responsibilities are evidenced.
7-8: Good match; core SQL, Excel, Python, data cleaning, reporting or database skills align, with manageable gaps.
5-6: Partial match; meaningful transferable skills but important required skills or experience are missing.
3-4: Weak match; role is mainly outside data/analytics or requires substantially more experience.
1-2: Not relevant to the candidate's stated target roles.

ASSESSMENT RULES:
- Evaluate junior and entry-level roles positively when the candidate's evidence aligns. Do not penalise a candidate simply for being early-career.
- Remote + part-time or freelance/contract is a hard preference, not a bonus. Explicit full-time roles must receive ELIGIBILITY: full_time_only and a score of 1. Unknown employment type must be flagged as unconfirmed rather than represented as part-time.
- Distinguish required skills from desirable skills.
- Assess SQL, Excel, Python, pandas, MySQL, data cleaning, data validation, reconciliation, dashboards, reporting, ETL and data quality where relevant.
- Do not assume commercial experience, production-scale engineering, cloud platforms, advanced statistics, degrees or certifications unless evidenced in the resume.
- Flag required experience, tools, qualifications, location restrictions and work-authorisation conditions that are not established by the supplied evidence.
- Do not advise the candidate to claim a skill or qualification they do not have.
- Score the role's fit, not the employer's prestige.

Return exactly these four lines:
ELIGIBILITY: [eligible|non_uk_only|full_time_only|non_remote_only|unverified]
SCORE: [1-10]
KEYWORDS: [comma-separated relevant ATS keywords that are genuinely supported or clearly adjacent to the candidate's evidence]
REASONING: [2-4 sentences covering match, gaps, seniority, location restrictions and any critical eligibility questions.]
"""


def _check_ineligible(job: dict) -> str | None:
    """Defer eligibility decisions to the UK-focused LLM assessment.

    A deterministic title/location pre-filter previously rejected junior
    roles and non-US locations. That behaviour is incompatible with this
    UK-based junior-data-role search. It also cannot reliably distinguish
    a location restriction from incidental country mentions or infer
    right-to-work status. Return None and let the full posting be assessed.
    """
    return None


def _parse_score_response(response: str) -> dict:
    """Parse the LLM's score response into structured data.

    Args:
        response: Raw LLM response text.

    Returns:
        A dictionary containing score, keywords, reasoning and eligibility.
        Legacy 'non_us_only' values are normalised to 'non_uk_only'.
    """
    score = 0
    keywords = ""
    reasoning = response
    eligibility = "unverified"

    for line in response.split("\n"):
        line = line.strip()
        if line.startswith("ELIGIBILITY:"):
            value = line.replace("ELIGIBILITY:", "").strip().lower()
            value = re.sub(r"[\[\]\s]+", "_", value).strip("_")
            if value in {"full_time_only", "fulltime_only", "full_time", "fulltime"}:
                eligibility = "full_time_only"
            elif value in {"non_remote_only", "onsite_only", "on_site_only", "hybrid_only"}:
                eligibility = "non_remote_only"
            elif value in {"non_uk_only", "non_us_only"} or (
                value.startswith("non_") and ("uk" in value or "us" in value)
            ):
                eligibility = "non_uk_only"
            elif value == "eligible":
                eligibility = "eligible"
            elif value in {"unverified", "unknown", "not_verified", "employment_type_unverified", "workplace_type_unverified"}:
                eligibility = "unverified"
            else:
                eligibility = "unverified"
        elif line.startswith("SCORE:"):
            try:
                score = int(re.search(r"\d+", line).group())
                score = max(1, min(10, score))
            except (AttributeError, ValueError):
                score = 0
        elif line.startswith("KEYWORDS:"):
            keywords = line.replace("KEYWORDS:", "").strip()
        elif line.startswith("REASONING:"):
            reasoning = line.replace("REASONING:", "").strip()

    return {"score": score, "keywords": keywords, "reasoning": reasoning,
            "eligibility": eligibility}


def _build_candidate_summary(profile: dict) -> str:
    """Build a concise candidate summary using both supported profile layouts."""
    exp = profile.get("experience", {})
    boundary = profile.get("skills_boundary", {})
    years = exp.get("years_of_experience_total") or "early-career"
    current_title = exp.get("current_job_title") or exp.get("current_title") or "Early-career data professional"
    targets = profile.get("target_roles") or [exp.get("target_role") or "Data Analyst"]
    languages = boundary.get("languages") or boundary.get("programming_languages") or []
    platforms = list(boundary.get("platforms") or [])
    platforms += boundary.get("databases") or []
    platforms += boundary.get("tools") or []
    parts = [f"{current_title} with {years} years experience."]
    if languages:
        parts.append(f"Languages: {', '.join(languages[:8])}.")
    if platforms:
        parts.append(f"Platforms and tools: {', '.join(platforms[:10])}.")
    parts.append(f"Target roles: {', '.join(targets[:8])}.")
    return " ".join(parts)


def score_job(resume_text: str, job: dict, profile: dict | None = None) -> dict:
    """Score a single job against the resume.

    Args:
        resume_text: The candidate's full resume text.
        job: Job dict with keys: title, site, location, full_description.

    Returns:
        A dictionary containing score, keywords, reasoning and eligibility.
    """
    if profile is None:
        profile = load_profile()

    ineligible_reason = _check_ineligible(job)
    if ineligible_reason:
        log.info("Pre-filter INELIGIBLE (non_uk_only): %s — %s", (job.get("title") or "?")[:60], ineligible_reason)
        return {
            "score": 2,
            "keywords": "",
            "reasoning": f"Ineligible: {ineligible_reason}.",
            "eligibility": "non_uk_only",
        }

    try:
        candidate_summary = _build_candidate_summary(profile)
        score_prompt = SCORE_PROMPT_TEMPLATE.format(candidate_summary=candidate_summary)

        job_text = (
            f"TITLE: {job['title']}\n"
            f"COMPANY: {job['site']}\n"
            f"LOCATION: {job.get('location', 'N/A')}\n\n"
            f"DESCRIPTION:\n{(job.get('full_description') or '')[:6000]}"
        )

        messages = [
            {"role": "system", "content": score_prompt},
            {"role": "user", "content": f"RESUME:\n{resume_text}\n\n---\n\nJOB POSTING:\n{job_text}"},
        ]

        client = get_client()
        response = client.chat(messages, max_tokens=8192, temperature=0.2)
        return _parse_score_response(response)
    except Exception as e:
        log.error("LLM error scoring job '%s': %s", (job or {}).get("title") or "?", e)
        return {"score": None, "keywords": "", "reasoning": "", "eligibility": None,
                "error": f"LLM error: {e}"}


MAX_SCORE_RETRIES = 5


def _score_backoff_minutes(retry_count: int) -> int:
    """Exponential backoff for scoring retries: 5, 20, 80, ~5h, ~21h."""
    return min(5 * (4 ** retry_count), 24 * 60)


def _flush_score_batch(conn, batch: list[dict], now: str) -> None:
    """Write a batch of scoring results to the DB.

    On success (score is not None): writes fit_score, clears score_error.
    On failure (score is None): leaves fit_score NULL, writes score_error + backoff.
    Jobs that have already hit MAX_SCORE_RETRIES stay unscored indefinitely (manual rescue needed).
    """
    from applypilot.database import transition_state
    from applypilot.config import DEFAULTS as _cfg_DEFAULTS
    _min_score = _cfg_DEFAULTS["min_score"]

    for r in batch:
        if r["score"] is not None:
            eligibility = r.get("eligibility") or "eligible"
            conn.execute(
                "UPDATE jobs SET fit_score = ?, score_reasoning = ?, scored_at = ?, "
                "eligibility = ?, eligibility_status = ?, eligibility_reason = ?, "
                "score_error = NULL, score_attempts = 0, score_next_retry_at = NULL "
                "WHERE url = ?",
                (r["score"], f"{r['keywords']}\n{r['reasoning']}", now, eligibility,
                 "eligible" if eligibility == "eligible" else "rejected",
                 None if eligibility == "eligible" else eligibility, r["url"]),
            )
            if eligibility != "eligible":
                transition_state(conn, r["url"], "archived",
                                 reason=f"ineligible or unverified: {eligibility}",
                                 metadata={"score": r["score"],
                                           "eligibility": eligibility},
                                 force=True)
            else:
                to_state = "scored" if r["score"] >= _min_score else "low_score"
                transition_state(conn, r["url"], to_state,
                                 reason=f"scored {r['score']}/10",
                                 metadata={"score": r["score"],
                                           "eligibility": eligibility})
        else:
            row = conn.execute(
                "SELECT COALESCE(score_attempts, 0) FROM jobs WHERE url = ?", (r["url"],)
            ).fetchone()
            retry_count = row[0] if row else 0
            if retry_count >= MAX_SCORE_RETRIES:
                conn.execute(
                    "UPDATE jobs SET score_error = ?, score_attempts = ?, "
                    "score_next_retry_at = NULL, scored_at = ? WHERE url = ?",
                    (r["error"], retry_count + 1, now, r["url"]),
                )
                transition_state(conn, r["url"], "score_failed",
                                 reason=f"LLM failed after {retry_count+1} attempts",
                                 metadata={"error": r["error"]})
            else:
                delay = _score_backoff_minutes(retry_count)
                next_retry = (
                    datetime.now(timezone.utc) + timedelta(minutes=delay)
                ).isoformat()
                conn.execute(
                    "UPDATE jobs SET score_error = ?, score_attempts = ?, "
                    "score_next_retry_at = ?, scored_at = ? WHERE url = ?",
                    (r["error"], retry_count + 1, next_retry, now, r["url"]),
                )
                log.info("  score retry %d/%d scheduled in %d min for %s",
                         retry_count + 1, MAX_SCORE_RETRIES, delay, r["url"][:60])


def run_scoring(limit: int = 0, rescore: bool = False, workers: int = 1,
                max_age_days: int | None = None) -> dict:
    """Score unscored jobs that have full descriptions.

    Args:
        limit: Maximum number of jobs to score in this run.
        rescore: If True, re-score all jobs (not just unscored ones).
        workers: Parallel LLM threads (default 1 = sequential).

    Returns:
        {"scored": int, "errors": int, "elapsed": float, "distribution": list}
    """
    resume_text = RESUME_PATH.read_text(encoding="utf-8")
    conn = get_connection()

    if rescore:
        query = "SELECT * FROM jobs WHERE full_description IS NOT NULL"
        if limit > 0:
            query += f" LIMIT {limit}"
        jobs = conn.execute(query).fetchall()
    else:
        jobs = get_jobs_by_stage(conn=conn, stage="pending_score",
                                 max_age_days=max_age_days, limit=limit)

    if not jobs:
        log.info("No unscored jobs with descriptions found.")
        return {"scored": 0, "errors": 0, "elapsed": 0.0, "distribution": []}

    if jobs and not isinstance(jobs[0], dict):
        columns = jobs[0].keys()
        jobs = [dict(zip(columns, row)) for row in jobs]

    log.info("Scoring %d jobs (workers=%d)...", len(jobs), workers)
    t0 = time.time()
    completed = 0
    errors = 0
    batch_size = 25
    batch: list[dict] = []

    def _score_one(job: dict) -> dict:
        try:
            result = score_job(resume_text, job)
            result["url"] = job["url"]
        except Exception as e:
            log.error("Unexpected error scoring '%s': %s", (job or {}).get("title") or "?", e)
            result = {
                "score": None, "keywords": "", "reasoning": "",
                "error": f"Unexpected: {e}", "url": (job or {}).get("url", ""),
            }
        return result

    def _flush_and_log(batch: list[dict], completed: int) -> list[dict]:
        now = datetime.now(timezone.utc).isoformat()
        try:
            write_with_retry(conn, _flush_score_batch, conn, batch, now)
        except Exception as flush_err:
            log.exception("Batch flush failed (batch of %d): %s", len(batch), flush_err)
        log.info("Committed batch of %d scores to DB (%d/%d total)", len(batch), completed, len(jobs))
        return []

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_score_one, job): job for job in jobs}
            for future in as_completed(futures):
                job = futures[future]
                result = future.result()
                completed += 1
                if result["score"] is None:
                    errors += 1
                batch.append(result)
                log.info(
                    "[%d/%d] score=%s  %s",
                    completed, len(jobs),
                    result["score"] if result["score"] is not None else "ERR",
                    (job.get("title") or "")[:60],
                )
                if len(batch) >= batch_size:
                    batch = _flush_and_log(batch, completed)
    else:
        for job in jobs:
            result = _score_one(job)
            completed += 1
            if result["score"] is None:
                errors += 1
            batch.append(result)
            log.info(
                "[%d/%d] score=%s  %s",
                completed, len(jobs),
                result["score"] if result["score"] is not None else "ERR",
                (job.get("title") or "")[:60],
            )
            if len(batch) >= batch_size:
                batch = _flush_and_log(batch, completed)

    if batch:
        now = datetime.now(timezone.utc).isoformat()
        try:
            write_with_retry(conn, _flush_score_batch, conn, batch, now)
        except Exception as flush_err:
            log.exception("Final batch flush failed (batch of %d): %s", len(batch), flush_err)

    elapsed = time.time() - t0
    log.info("Done: %d scored in %.1fs (%.1f jobs/sec)", completed, elapsed, completed / elapsed if elapsed > 0 else 0)

    try:
        dist = conn.execute("""
            SELECT fit_score, COUNT(*) FROM jobs
            WHERE fit_score IS NOT NULL
            GROUP BY fit_score ORDER BY fit_score DESC
        """).fetchall()
        distribution = [(row[0], row[1]) for row in dist]
    except Exception as dist_err:
        log.exception("Distribution query failed: %s", dist_err)
        distribution = []

    return {
        "scored": completed,
        "errors": errors,
        "elapsed": elapsed,
        "distribution": distribution,
    }
