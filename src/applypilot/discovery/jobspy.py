"""JobSpy-based job discovery: searches Indeed, LinkedIn, Glassdoor, ZipRecruiter.

Uses python-jobspy to scrape multiple job boards, deduplicates results,
parses salary ranges, and stores everything in the ApplyPilot database.

Search queries, locations, and filtering rules are loaded from the user's
search configuration YAML (searches.yaml) rather than being hardcoded.
"""

import logging
import sqlite3
import time
from datetime import datetime, timezone

from jobspy import scrape_jobs

# Patch TLSRotating to always specify a client_identifier.
# Without one, the tls-client Go binary receives a nil JA3 string and panics
# (SIGABRT), killing the process. A named profile avoids the crash.
try:
    import tls_client

    _orig_tls_init = tls_client.Session.__init__

    def _safe_tls_init(self, *args, **kwargs):
        if not kwargs.get("client_identifier") and not kwargs.get("ja3_string"):
            kwargs["client_identifier"] = "chrome_120"
        _orig_tls_init(self, *args, **kwargs)

    tls_client.Session.__init__ = _safe_tls_init
except Exception:
    pass  # If tls_client isn't available, jobspy will use regular requests

# Patch Country.from_string to return WORLDWIDE for unsupported country strings
# (e.g. "sri lanka") instead of raising ValueError that kills the entire scrape.
try:
    from jobspy.model import Country as _Country

    _orig_country_from_string = _Country.from_string.__func__

    @classmethod  # type: ignore[misc]
    def _safe_country_from_string(cls, country_str: str):
        try:
            return _orig_country_from_string(cls, country_str)
        except ValueError:
            return cls.WORLDWIDE

    _Country.from_string = _safe_country_from_string
except Exception:
    pass  # If patching fails, fall back to original behavior

from applypilot import config
from applypilot.database import get_connection, init_db, write_with_retry
from applypilot.discovery.eligibility import (
    classify_job_eligibility,
    configured_remote_countries,
    normalize_employment_type,
    normalize_workplace_type,
    remote_country_allowed,
)

log = logging.getLogger(__name__)


# -- Proxy parsing -----------------------------------------------------------

def parse_proxy(proxy_str: str) -> dict:
    """Parse host:port:user:pass into components."""
    parts = proxy_str.split(":")
    if len(parts) == 4:
        host, port, user, passwd = parts
        return {
            "host": host,
            "port": port,
            "user": user,
            "pass": passwd,
            "jobspy": f"{user}:{passwd}@{host}:{port}",
            "playwright": {
                "server": f"http://{host}:{port}",
                "username": user,
                "password": passwd,
            },
        }
    elif len(parts) == 2:
        host, port = parts
        return {
            "host": host,
            "port": port,
            "user": None,
            "pass": None,
            "jobspy": f"{host}:{port}",
            "playwright": {"server": f"http://{host}:{port}"},
        }
    else:
        raise ValueError(
            f"Proxy format not recognized: {proxy_str}. "
            f"Expected: host:port:user:pass or host:port"
        )


# -- Retry wrapper -----------------------------------------------------------

def _scrape_with_retry(kwargs: dict, max_retries: int = 2, backoff: float = 5.0):
    """Call scrape_jobs with retry on transient failures."""
    for attempt in range(max_retries + 1):
        try:
            return scrape_jobs(**kwargs)
        except Exception as e:
            err = str(e).lower()
            transient = any(k in err for k in ("timeout", "429", "proxy", "connection", "reset", "refused"))
            if transient and attempt < max_retries:
                wait = backoff * (attempt + 1)
                log.warning("Retry %d/%d in %.0fs: %s", attempt + 1, max_retries, wait, e)
                time.sleep(wait)
            else:
                raise


# -- Location filtering ------------------------------------------------------

def _load_location_config(search_cfg: dict) -> tuple[list[str], list[str]]:
    """Extract accept/reject location lists from search config.

    Falls back to sensible defaults if not defined in the YAML.
    """
    accept = search_cfg.get("location_accept", [])
    reject = search_cfg.get("location_reject_non_remote", [])
    return accept, reject


def _location_ok(location: str | None, accept: list[str], reject: list[str]) -> bool:
    """Check if a job location passes the user's location filter.

    Remote jobs are always accepted. Non-remote jobs must match an accept
    pattern and not match a reject pattern.
    """
    if not location:
        return True  # unknown location -- keep it, let scorer decide

    loc = location.lower()

    # Remote jobs always OK
    if any(r in loc for r in ("remote", "anywhere", "work from home", "wfh", "distributed")):
        return True

    # Reject non-remote matches
    for r in reject:
        if r.lower() in loc:
            return False

    # Accept matches
    for a in accept:
        if a.lower() in loc:
            return True

    # No match -- reject unknown
    return False


# -- Remote-country scope ----------------------------------------------------

def _remote_country_ok(
    location: str | None, allowed_countries: list[str] | None, description: str | None = None
) -> bool:
    """Require a positively identified country from the configured allowlist."""
    return remote_country_allowed(location, allowed_countries, description)



def _jobspy_filter_results(
    df,
    remote_only: bool,
    job_types: list[str],
    allowed_remote_countries: list[str] | None = None,
    accept_locs: list[str] | None = None,
    reject_locs: list[str] | None = None,
):
    """Apply filters in sequence and return disjoint rejection counts."""
    remaining = df.copy()
    counts = {"raw": len(remaining), "remote": 0, "employment": 0, "country": 0, "location": 0}

    if remote_only:
        mask = remaining.apply(_remote_result_ok, axis=1)
        counts["remote"] = int((~mask).sum())
        remaining = remaining[mask]

    mask = remaining.apply(lambda row: _employment_type_ok(row, job_types), axis=1)
    counts["employment"] = int((~mask).sum())
    remaining = remaining[mask]

    if remote_only and allowed_remote_countries:
        mask = remaining.apply(
            lambda row: _remote_country_ok(row.get("location"), allowed_remote_countries, row.get("description")), axis=1
        )
        counts["country"] = int((~mask).sum())
        remaining = remaining[mask]
    elif not remote_only:
        mask = remaining.apply(lambda row: _location_ok(
            str(row.get("location", "")) if str(row.get("location", "")) != "nan" else None,
            accept_locs or [], reject_locs or [],
        ), axis=1)
        counts["location"] = int((~mask).sum())
        remaining = remaining[mask]

    return remaining, counts

def _normalise_job_types(values) -> list[str]:
    """Map user-facing job types to JobSpy's supported type values."""
    if isinstance(values, str):
        values = [values]
    aliases = {
        "part-time": "parttime", "part time": "parttime",
        "freelance": "contract", "contractor": "contract",
    }
    allowed = {"parttime", "contract"}
    result = []
    for value in values or ["parttime", "contract"]:
        normalised = str(value).strip().lower().replace("_", " ")
        normalised = aliases.get(normalised, normalised.replace(" ", ""))
        if normalised not in allowed:
            raise ValueError(
                f"Unsupported employment type {value!r}. This search is restricted "
                "to part-time and freelance/contract work."
            )
        if normalised not in result:
            result.append(normalised)
    return result


def _remote_result_ok(row) -> bool:
    """Return True only when remote status is explicit and not hybrid/on-site."""
    return normalize_workplace_type(
        None, row.get("is_remote"), str(row.get("location", "") or ""),
        str(row.get("description", "") or ""),
    ) == "remote"


def _employment_type_ok(row, allowed_types: list[str]) -> bool:
    """Unknown and disallowed employment types fail closed."""
    employment = normalize_employment_type(
        row.get("job_type"), str(row.get("description", "") or ""),
        str(row.get("title", "") or ""),
    )
    aliases = {"parttime": "part_time", "contract": "contract"}
    allowed = {aliases.get(value, value) for value in allowed_types}
    return employment in allowed


# -- DB storage (JobSpy DataFrame -> SQLite) ---------------------------------

def store_jobspy_results(conn: sqlite3.Connection, df, source_label: str) -> tuple[int, int]:
    """Store JobSpy DataFrame results into the DB. Returns (new, existing)."""
    now = datetime.now(timezone.utc).isoformat()
    counts = {"new": 0, "existing": 0}

    rows_iter = list(df.iterrows())

    def _do_inserts() -> None:
        counts["new"] = 0
        counts["existing"] = 0
        for _, row in rows_iter:
            url = str(row.get("job_url", ""))
            if not url or url == "nan":
                continue
            eligibility = classify_job_eligibility({
                "title": row.get("title"),
                "job_type": row.get("job_type"),
                "is_remote": row.get("is_remote"),
                "location": row.get("location"),
                "description": row.get("description"),
            }, require_remote=True)
            if eligibility["eligibility_status"] != "eligible":
                continue

            title = str(row.get("title", "")) if str(row.get("title", "")) != "nan" else None
            company_value = row.get("company")
            _company = str(company_value).strip() if company_value is not None else None
            if not _company or _company.casefold() in {"nan", "none", "null", "<na>"}:
                _company = None
            location_str = str(row.get("location", "")) if str(row.get("location", "")) != "nan" else None

            # Build salary string from min/max
            salary = None
            min_amt = row.get("min_amount")
            max_amt = row.get("max_amount")
            interval = str(row.get("interval", "")) if str(row.get("interval", "")) != "nan" else ""
            currency = str(row.get("currency", "")) if str(row.get("currency", "")) != "nan" else ""
            if min_amt and str(min_amt) != "nan":
                if max_amt and str(max_amt) != "nan":
                    salary = f"{currency}{int(float(min_amt)):,}-{currency}{int(float(max_amt)):,}"
                else:
                    salary = f"{currency}{int(float(min_amt)):,}"
                if interval:
                    salary += f"/{interval}"

            description = str(row.get("description", "")) if str(row.get("description", "")) != "nan" else None
            site_name = str(row.get("site", source_label))
            is_remote = row.get("is_remote", False)

            site_label = f"{site_name}"
            if is_remote:
                location_str = f"{location_str} (Remote)" if location_str else "Remote"

            strategy = "jobspy"

            # If JobSpy gave us a full description, promote it directly
            full_description = None
            detail_scraped_at = None
            if description and len(description) > 200:
                full_description = description
                detail_scraped_at = now

            # Extract apply URL if JobSpy provided it
            apply_url = str(row.get("job_url_direct", "")) if str(row.get("job_url_direct", "")) != "nan" else None

            # Capture posting date if available
            date_posted_raw = row.get("date_posted")
            posted_at = None
            if date_posted_raw and str(date_posted_raw) not in ("nan", "None", ""):
                try:
                    dp = date_posted_raw
                    if hasattr(dp, "isoformat"):
                        posted_at = dp.isoformat()
                    else:
                        posted_at = str(dp)
                except Exception:
                    posted_at = None

            initial_state = "enriched" if full_description else "discovered"

            try:
                conn.execute(
                    "INSERT INTO jobs (url, title, salary, description, location, site, strategy, discovered_at, "
                    "company, full_description, application_url, detail_scraped_at, posted_at, state, "
                    "employment_type, workplace_type, eligibility_status, eligibility_reason) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (url, title, salary, description, location_str, site_label, strategy, now,
                     _company, full_description, apply_url, detail_scraped_at, posted_at, initial_state,
                     eligibility["employment_type"], eligibility["workplace_type"],
                     eligibility["eligibility_status"], eligibility["eligibility_reason"]),
                )
                conn.execute(
                    "INSERT INTO job_state_transitions "
                    "(job_url, from_state, to_state, at, reason, metadata) "
                    "VALUES (?, NULL, ?, ?, ?, ?)",
                    (url, initial_state, now, f"discovered via {strategy}", None),
                )
                counts["new"] += 1
            except sqlite3.IntegrityError:
                # A duplicate URL may have been discovered previously without
                # employer metadata. Backfill only a missing company; never
                # overwrite a known employer with a later blank/incorrect value.
                if _company:
                    conn.execute(
                        "UPDATE jobs SET company = ? "
                        "WHERE url = ? AND (company IS NULL OR TRIM(company) = '')",
                        (_company, url),
                    )
                counts["existing"] += 1

    write_with_retry(conn, _do_inserts)
    return counts["new"], counts["existing"]


# -- Single search execution -------------------------------------------------

def _run_one_search(
    search: dict,
    sites: list[str],
    results_per_site: int,
    hours_old: int,
    proxy_config: dict | None,
    defaults: dict,
    max_retries: int,
    accept_locs: list[str],
    reject_locs: list[str],
    glassdoor_map: dict,
) -> dict:
    """Run a single search query and store results in DB."""
    s = search
    label = f"\"{s['query']}\" in {s['location']} {'(remote)' if s.get('remote') else ''}"
    if "tier" in s:
        label += f" [tier {s['tier']}]"

    # Apply the user's remote and work-type preferences at source level.
    remote_only = bool(defaults.get("remote_only", True)) or bool(s.get("remote"))
    job_types = _normalise_job_types(defaults.get("employment_types", ["parttime", "contract"]))

    # Split sites: Glassdoor needs simplified location.
    gd_location = glassdoor_map.get(s["location"], s["location"].split(",")[0])
    has_glassdoor = "glassdoor" in sites
    effective_sites = [si for si in sites if si != "glassdoor"]
    # Indeed is retained for remote searches because country_indeed is
    # explicitly set to UK in the request below; do not infer from proxy IP.
    other_sites = effective_sites

    all_dfs = []

    # JobSpy accepts one job_type per request; run one request for each desired type.
    for job_type in job_types:
        if other_sites:
            kwargs = {
                "site_name": other_sites,
                "search_term": s["query"],
                "location": s["location"],
                "results_wanted": results_per_site,
                "hours_old": hours_old,
                "description_format": "markdown",
                "job_type": job_type,
                "verbose": 0,
            }
            if "indeed" in other_sites:
                kwargs["country_indeed"] = defaults.get("country_indeed", "uk")
            if remote_only:
                kwargs["is_remote"] = True
            if proxy_config:
                kwargs["proxies"] = [proxy_config["jobspy"]]
            if "linkedin" in other_sites:
                kwargs["linkedin_fetch_description"] = True
            try:
                df = _scrape_with_retry(kwargs, max_retries=max_retries)
                if df is not None and len(df):
                    all_dfs.append(df)
            except Exception as e:
                log.error("[%s] (non-gd, %s): %s", label, job_type, e)

        if has_glassdoor:
            gd_kwargs = {
                "site_name": ["glassdoor"],
                "search_term": s["query"],
                "location": gd_location,
                "results_wanted": results_per_site,
                "hours_old": hours_old,
                "description_format": "markdown",
                "job_type": job_type,
                "verbose": 0,
            }
            if remote_only:
                gd_kwargs["is_remote"] = True
            gd_kwargs["country_indeed"] = defaults.get("country_indeed", "uk")
            if proxy_config:
                gd_kwargs["proxies"] = [proxy_config["jobspy"]]
            try:
                gd_df = _scrape_with_retry(gd_kwargs, max_retries=max_retries)
                if gd_df is not None and len(gd_df):
                    all_dfs.append(gd_df)
            except Exception as e:
                log.error("[%s] (glassdoor, %s): %s", label, job_type, e)

    if not all_dfs:
        log.error("[%s]: all sites failed", label)
        return {"new": 0, "existing": 0, "errors": 1, "filtered": 0, "total": 0, "label": label}

    import pandas as pd
    import warnings
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        df = pd.concat(all_dfs, ignore_index=True) if len(all_dfs) > 1 else all_dfs[0]

    if len(df) == 0:
        log.info("[%s] 0 results", label)
        return {"new": 0, "existing": 0, "errors": 0, "filtered": 0, "total": 0, "label": label}

    # Apply each gate separately so rejected results are attributable.
    remote_only = bool(defaults.get("remote_only", True)) or bool(s.get("remote"))
    job_types = _normalise_job_types(defaults.get("employment_types", ["parttime", "contract"]))
    allowed_countries = defaults.get("allowed_remote_countries", configured_remote_countries())
    raw_df = df.copy()
    df, rejection_counts = _jobspy_filter_results(
        raw_df, remote_only, job_types, allowed_countries, accept_locs, reject_locs
    )
    filtered = sum(rejection_counts[k] for k in ("remote", "employment", "country", "location"))

    conn = get_connection()
    new, existing = store_jobspy_results(conn, df, s["query"])

    log.info(
        "[%s] raw=%d | rejected remote=%d employment=%d country=%d location=%d "
        "| eligible_candidates=%d | inserted=%d duplicates=%d",
        label, rejection_counts["raw"], rejection_counts["remote"],
        rejection_counts["employment"], rejection_counts["country"],
        rejection_counts["location"], len(df), new, existing,
    )
    if filtered:
        rejected = raw_df.loc[~raw_df.index.isin(df.index)].head(3)
        for _, sample in rejected.iterrows():
            log.info(
                "[%s] diagnostic sample title=%r site=%r location=%r job_type=%r is_remote=%r",
                label, sample.get("title"), sample.get("site"), sample.get("location"),
                sample.get("job_type"), sample.get("is_remote"),
            )

    return {
        "new": new, "existing": existing, "errors": 0, "filtered": filtered,
        "total": rejection_counts["raw"], "rejections": rejection_counts, "label": label,
    }


# -- Single query search -----------------------------------------------------

def search_jobs(
    query: str,
    location: str,
    sites: list[str] | None = None,
    remote_only: bool = True,
    results_per_site: int = 50,
    hours_old: int = 72,
    proxy: str | None = None,
    country_indeed: str = "uk",
) -> dict:
    """Run a single job search via JobSpy and store results in DB."""
    if sites is None:
        # zip_recruiter removed 2026-06-10: Cloudflare-blocked, 403
        # "forbidden aa" on 100% of requests; python-jobspy 1.1.82 is
        # the latest release and has no fix. Re-add if a fix ships.
        sites = ["indeed", "linkedin"]

    proxy_config = parse_proxy(proxy) if proxy else None

    log.info("Search: \"%s\" in %s | sites=%s | remote=%s", query, location, sites, remote_only)

    job_types = _normalise_job_types(["parttime", "contract"])
    all_dfs = []
    for job_type in job_types:
        kwargs = {
            "site_name": sites,
            "search_term": query,
            "location": location,
            "results_wanted": results_per_site,
            "hours_old": hours_old,
            "description_format": "markdown",
            "country_indeed": country_indeed,
            "job_type": job_type,
            "verbose": 2,
        }

        if remote_only:
            kwargs["is_remote"] = True

        if proxy_config:
            kwargs["proxies"] = [proxy_config["jobspy"]]

        if "linkedin" in sites:
            kwargs["linkedin_fetch_description"] = True

        try:
            df = scrape_jobs(**kwargs)
            if df is not None and len(df):
                all_dfs.append(df)
        except Exception as e:
            log.error("JobSpy search failed for %s: %s", job_type, e)

    if not all_dfs:
        return {"total": 0, "new": 0, "existing": 0}

    import pandas as pd
    df = pd.concat(all_dfs, ignore_index=True) if len(all_dfs) > 1 else all_dfs[0]
    allowed_countries = configured_remote_countries()
    df, rejection_counts = _jobspy_filter_results(
        df, remote_only, job_types, list(allowed_countries)
    )

    total = len(df)
    log.info(
        "JobSpy single search: raw=%d | rejected remote=%d employment=%d country=%d "
        "| eligible_candidates=%d",
        rejection_counts["raw"], rejection_counts["remote"],
        rejection_counts["employment"], rejection_counts["country"], total,
    )

    if total == 0:
        return {"total": 0, "new": 0, "existing": 0}

    if "site" in df.columns:
        site_counts = df["site"].value_counts()
        for site, count in site_counts.items():
            log.info("  %s: %d", site, count)

    conn = init_db()
    new, existing = store_jobspy_results(conn, df, query)
    log.info("Stored: %d new, %d already in DB", new, existing)

    db_total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM jobs WHERE detail_scraped_at IS NULL").fetchone()[0]
    log.info("DB total: %d jobs, %d pending detail scrape", db_total, pending)

    return {"total": total, "new": new, "existing": existing}


# -- Full crawl (all queries x all locations) --------------------------------

def _full_crawl(
    search_cfg: dict,
    tiers: list[int] | None = None,
    locations: list[str] | None = None,
    sites: list[str] | None = None,
    results_per_site: int = 100,
    hours_old: int = 72,
    proxy: str | None = None,
    max_retries: int = 2,
) -> dict:
    """Run all search queries from search config across all locations."""
    if sites is None:
        # zip_recruiter removed 2026-06-10: Cloudflare-blocked, 403
        # "forbidden aa" on 100% of requests; python-jobspy 1.1.82 is
        # the latest release and has no fix. Re-add if a fix ships.
        sites = ["indeed", "linkedin"]

    # Build search combinations from config
    queries = search_cfg.get("queries", [])
    locs = search_cfg.get("locations", [])
    defaults = dict(search_cfg.get("defaults", {}) or {})
    # Country scope is top-level so it is easy to review and edit in searches.yaml.
    # Older user configs predate this setting; apply the new safe default and
    # broaden the exact legacy UK-remote location to a global remote query.
    configured_countries = search_cfg.get(
        "allowed_remote_countries", defaults.get("allowed_remote_countries")
    )
    if configured_countries is None:
        configured_countries = list(configured_remote_countries())
        # Older generated configs requested 100 results per board. Cap that
        # legacy default after broadening the search to avoid unnecessary load.
        if results_per_site > 30:
            log.info("Legacy search config: reducing results_per_site from %d to 30", results_per_site)
            results_per_site = 30
        if defaults.get("remote_only", True):
            for location_cfg in search_cfg.get("locations", []):
                legacy_location = str(location_cfg.get("location", "")).strip().casefold()
                if legacy_location in {"remote - united kingdom", "remote - uk"}:
                    location_cfg["location"] = "Remote"
            log.info(
                "Legacy remote search config detected: broadened UK-only search location to Remote; "
                "country allowlist remains enforced."
            )
    defaults["allowed_remote_countries"] = configured_countries
    glassdoor_map = search_cfg.get("glassdoor_location_map", {})
    accept_locs, reject_locs = _load_location_config(search_cfg)

    if tiers:
        queries = [q for q in queries if q.get("tier") in tiers]
    if locations:
        locs = [loc for loc in locs if loc.get("label") in locations]

    searches = []
    for q in queries:
        for loc in locs:
            searches.append({
                "query": q["query"],
                "location": loc["location"],
                "remote": bool(loc.get("remote", False) or defaults.get("remote_only", True)),
                "tier": q.get("tier", 0),
            })

    proxy_config = parse_proxy(proxy) if proxy else None

    log.info("Full crawl: %d search combinations", len(searches))
    log.info("Sites: %s | Results/site: %d | Hours old: %d",
             ", ".join(sites), results_per_site, hours_old)

    # Ensure DB schema is ready
    init_db()

    total_new = 0
    total_existing = 0
    total_errors = 0
    completed = 0

    for s in searches:
        result = _run_one_search(
            s, sites, results_per_site, hours_old,
            proxy_config, defaults, max_retries,
            accept_locs, reject_locs, glassdoor_map,
        )
        completed += 1
        total_new += result["new"]
        total_existing += result["existing"]
        total_errors += result["errors"]

        if completed % 5 == 0 or completed == len(searches):
            log.info("Progress: %d/%d queries done (%d new, %d dupes, %d errors)",
                     completed, len(searches), total_new, total_existing, total_errors)

    # Final stats
    conn = get_connection()
    db_total = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    log.info("Full crawl complete: %d new | %d dupes | %d errors | %d total in DB",
             total_new, total_existing, total_errors, db_total)

    return {
        "new": total_new,
        "existing": total_existing,
        "errors": total_errors,
        "db_total": db_total,
        "queries": len(searches),
    }


# -- Public entry point ------------------------------------------------------

def run_discovery(cfg: dict | None = None, sites_override: list[str] | None = None) -> dict:
    """Main entry point for JobSpy-based job discovery.

    Loads search queries and locations from the user's search config YAML,
    then runs a full crawl across all configured job boards.

    Args:
        cfg: Override the search configuration dict. If None, loads from
             the user's searches.yaml file.
        sites_override: If provided, use these sites instead of the config's
             sites key. Used to run a specific board (e.g. ["dice"]).

    Returns:
        Dict with stats: new, existing, errors, db_total, queries.
    """
    if cfg is None:
        cfg = config.load_search_config()

    if not cfg:
        log.warning("No search configuration found. Run `applypilot init` to create one.")
        return {"new": 0, "existing": 0, "errors": 0, "db_total": 0, "queries": 0}

    proxy = cfg.get("proxy")
    sites = sites_override or cfg.get("sites")
    results_per_site = cfg.get("defaults", {}).get("results_per_site", 100)
    hours_old = cfg.get("defaults", {}).get("hours_old", 72)
    tiers = cfg.get("tiers")
    locations = cfg.get("location_labels")

    return _full_crawl(
        search_cfg=cfg,
        tiers=tiers,
        locations=locations,
        sites=sites,
        results_per_site=results_per_site,
        hours_old=hours_old,
        proxy=proxy,
    )
