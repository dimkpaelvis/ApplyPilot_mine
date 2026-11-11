"""Gemini-assisted Playwright application workflow.

Human approval is required for every final submission. This module does not
solve CAPTCHAs, bypass MFA, create accounts, or guess eligibility answers.
"""
from __future__ import annotations
import json, logging, re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from applypilot.config import PROFILE_PATH
from applypilot.database import get_connection, commit_with_retry

log = logging.getLogger(__name__)

def _load_jobs(limit, min_score, max_age_days, max_score, target_url):
    conn = get_connection()
    clauses = ["tailored_resume_path IS NOT NULL",
               "(apply_status IS NULL OR apply_status NOT IN ('applied','in_progress','needs_human'))",
               "(eligibility IS NULL OR eligibility = 'eligible')", "COALESCE(fit_score,0) >= ?"]
    params = [min_score]
    if max_score is not None:
        clauses.append("fit_score <= ?"); params.append(max_score)
    if max_age_days > 0:
        cutoff = (datetime.now(timezone.utc)-timedelta(days=max_age_days)).isoformat()
        clauses.append("(discovered_at IS NULL OR discovered_at >= ?)"); params.append(cutoff)
    if target_url:
        clauses.append("url = ?"); params.append(target_url)
    sql = ("SELECT url,title,company,site,fit_score,application_url,tailored_resume_path,cover_letter_path "
           "FROM jobs WHERE " + " AND ".join(clauses) +
           " ORDER BY fit_score DESC, discovered_at DESC LIMIT ?")
    params.append(max(1, limit))
    return [dict(row) for row in conn.execute(sql, params).fetchall()]

def _profile_facts(profile):
    personal = profile.get("personal", {})
    return {"personal": {k:v for k,v in personal.items()
                         if k not in {"password","job_site_password","ssn","national_id"}},
            "work_authorization": profile.get("work_authorization", {}),
            "availability": profile.get("availability", {}),
            "experience": profile.get("experience", {}),
            "skills_boundary": profile.get("skills_boundary", {})}

def _suggest_answers(page, profile):
    from applypilot.llm import get_client
    fields = page.locator("input:not([type=hidden]):not([type=submit]):not([type=password]), textarea, select")
    visible = []
    for i in range(min(fields.count(), 80)):
        field = fields.nth(i)
        try:
            if not field.is_visible() or not field.is_enabled(): continue
            label = field.get_attribute("aria-label") or field.get_attribute("placeholder") or field.get_attribute("name") or ""
            field_id = field.get_attribute("id")
            if field_id:
                try: label = page.locator(f'label[for="{field_id}"]').inner_text(timeout=300)
                except Exception: pass
            if not label:
                label = field.evaluate("(el) => el.parentElement?.innerText?.slice(0,160) || ''")
            visible.append({"index":i,"label":re.sub(r"\\s+"," ",label).strip()[:180],
                            "type":field.get_attribute("type") or field.evaluate("(el)=>el.tagName.toLowerCase()"),
                            "name":field.get_attribute("name") or ""})
        except Exception: continue
    if not visible: return {}
    prompt = ("Map form labels to exact profile facts. Return ONLY a JSON object keyed by numeric field index. "
              "Never invent or infer. Leave unknown fields blank. Never answer EEO, disability, veteran, "
              "criminal-history, sponsorship, work authorization, salary, or legal questions unless the exact "
              "answer is explicitly stated in the profile. Never provide passwords.\nPROFILE_FACTS=" +
              json.dumps(_profile_facts(profile),ensure_ascii=False) +
              "\nVISIBLE_FIELDS="+json.dumps(visible,ensure_ascii=False))
    raw = get_client().ask(prompt, temperature=0, max_tokens=1200)
    match = re.search(r"\\{[\\s\\S]*\\}", raw)
    if not match: return {}
    data = json.loads(match.group(0))
    return {str(k):str(v) for k,v in data.items()
            if str(k).isdigit() and isinstance(v,(str,int,float)) and str(v).strip()}

def _fill_fields(page, answers):
    fields = page.locator("input:not([type=hidden]):not([type=submit]):not([type=password]), textarea, select")
    filled = 0
    for key,value in answers.items():
        try:
            field = fields.nth(int(key))
            if not field.is_visible() or not field.is_enabled(): continue
            kind = (field.get_attribute("type") or "").lower()
            if kind in {"checkbox","radio","file"}: continue
            if field.evaluate("(el)=>el.tagName.toLowerCase()") == "select":
                options=field.locator("option"); exact=None
                for j in range(options.count()):
                    option=options.nth(j)
                    if option.inner_text().strip().casefold()==value.casefold():
                        exact=option.get_attribute("value"); break
                if exact is not None: field.select_option(exact); filled+=1
            else:
                field.fill(value); filled+=1
        except Exception as exc: log.debug("Could not fill field %s: %s",key,exc)
    return filled

def run_browser_apply(limit=1,min_score=8,max_age_days=14,max_score=None,
                      target_url=None,headless=False,dry_run=False):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Playwright is missing. Install dependencies and run playwright install chromium.") from exc
    if not PROFILE_PATH.exists():
        raise FileNotFoundError(f"Profile not found: {PROFILE_PATH}. Run applypilot init first.")
    profile=json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    jobs=_load_jobs(limit,min_score,max_age_days,max_score,target_url)
    if not jobs:
        print("No eligible jobs with tailored resumes are ready."); return 0
    completed=0
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=headless)
        context=browser.new_context()
        page=context.new_page()
        for job in jobs:
            url=job.get("application_url") or job["url"]
            parsed=urlparse(url)
            if parsed.scheme not in {"http","https"} or not parsed.netloc:
                log.warning("Skipping invalid application URL: %s",url); continue
            print(f"\\n{job.get('title') or 'Untitled'} — {job.get('company') or parsed.netloc} (score {job.get('fit_score')})")
            print(url)
            if dry_run:
                print(f"DRY RUN: would open; resume={job.get('tailored_resume_path')}"); continue
            page.goto(url,wait_until="domcontentloaded",timeout=60000); page.wait_for_timeout(1500)
            body=(page.locator("body").inner_text(timeout=5000) or "").lower()
            if any(token in body for token in ("verify you are human","captcha","security challenge")):
                print("Human verification detected. Complete it yourself; no bypass will be attempted.")
                input("Press Enter after completing verification, or Ctrl+C to stop...")
            answers=_suggest_answers(page,profile)
            filled=_fill_fields(page,answers)
            resume=Path(job["tailored_resume_path"]).expanduser()
            if resume.is_file():
                uploads=page.locator('input[type="file"]')
                if uploads.count():
                    try: uploads.first.set_input_files(str(resume))
                    except Exception as exc: log.warning("Resume upload needs manual attention: %s",exc)
            if job.get("cover_letter_path"): print(f"Cover letter prepared: {job['cover_letter_path']}")
            print(f"Filled {filled} profile-grounded fields. Review every field and document.")
            approval=input("Type SUBMIT to authorize this one submission, or press Enter to stop for manual review: ").strip()
            if approval!="SUBMIT":
                print("Not submitted. Finish manually in the open browser."); continue
            submit=page.locator('button[type="submit"], input[type="submit"]')
            if submit.count()!=1:
                print("Submission control is ambiguous. Nothing submitted."); continue
            submit.first.click(); page.wait_for_timeout(2500)
            confirmation=input("Did the employer site confirm successful submission? Type YES to record it: ").strip()
            if confirmation=="YES":
                conn=get_connection(); now=datetime.now(timezone.utc).isoformat()
                conn.execute("UPDATE jobs SET apply_status='applied', applied_at=?, apply_category='applied' WHERE url=?",(now,job["url"]))
                commit_with_retry(conn); completed+=1; print("Recorded as applied.")
            else: print("Not marked as applied. Verify the employer portal.")
        context.close(); browser.close()
    return completed
