import json
import logging
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

from filelock import FileLock

from app.database.repository import Repository
from app.services.deduplicator import normalize_url
from app.services.eligibility import evaluate
from app.services.firecrawl_service import Firecrawl, queries_for
from app.services.google_sheets import GoogleSheets
from app.services.job_extractor import extract
from app.services.job_sources.ats import PublicATS
from app.services.matcher import CompatibleLLM, match_job
from app.services.verifier import ATS_HOSTS, EXPORTABLE, verify
from app.services.weekly_target import weekly_progress

log = logging.getLogger("internscout")


def event(name, **details):
    # Only structured, selected fields; never exception bodies, credentials or job descriptions.
    log.info(json.dumps({"event": name, **details}, ensure_ascii=True))


class Pipeline:
    def __init__(self, settings, firecrawl=None, ats=None, sheets=None, analyzer=None):
        self.settings = settings
        self.repo = Repository(settings.database_path)
        self.profile = json.loads(Path(settings.profile_path).read_text(encoding="utf-8"))
        self.sources = json.loads(Path(settings.sources_path).read_text(encoding="utf-8"))
        self.firecrawl = firecrawl or Firecrawl(settings.firecrawl_api_key)
        self.ats = ats or PublicATS()
        self.sheets = sheets
        self.analyzer = analyzer
        if not analyzer and settings.llm_api_key and settings.max_llm_calls:
            self.analyzer = CompatibleLLM(settings.llm_api_key, settings.llm_model, settings.llm_base_url)

    def run(self, sync=True):
        with FileLock(self.settings.database_path + ".lock", timeout=0):
            return self._run(sync)

    def _run(self, sync):
        run_id = self.repo.start_run()
        metrics = dict(
            discovered=0,
            filtered=0,
            verified=0,
            matched=0,
            added=0,
            duplicates=0,
            scraped=0,
            queries=0,
            llm_calls=0,
            errors=0,
            sheet_added=0,
            sheet_updated=0,
        )
        event("SEARCH_STARTED", run_id=run_id)
        successful_sources = 0
        try:
            candidates = []
            for board in self.sources.get("boards", []):
                try:
                    jobs = self.ats.fetch(board)
                    metrics["discovered"] += len(jobs)
                    candidates.extend(jobs)
                    successful_sources += 1
                except Exception as exc:
                    metrics["errors"] += 1
                    event("ERROR", stage="ats", source=board.get("type"), error_type=type(exc).__name__)
            seen = set()
            # Public structured sources precede Firecrawl to avoid unnecessary scrapes.
            for job in candidates:
                try:
                    url = normalize_url(job.source_url)
                    if url in seen:
                        metrics["duplicates"] += 1
                        continue
                    seen.add(url)
                    self.process(job, metrics)
                except Exception as exc:
                    metrics["errors"] += 1
                    event("ERROR", stage="ats_job", error_type=type(exc).__name__)
            if self.settings.firecrawl_api_key and self.settings.max_queries:
                urls = []
                queries = queries_for(self.profile, date.today().toordinal() * self.settings.max_queries)
                for query in queries[: self.settings.max_queries]:
                    try:
                        metrics["queries"] += 1
                        results = self.firecrawl.search(query, self.settings.results_per_query)
                        successful_sources += 1
                        metrics["discovered"] += len(results)
                        for result in results:
                            try:
                                url = normalize_url(result["url"])
                            except ValueError:
                                metrics["filtered"] += 1
                                continue
                            if url in seen or self.repo.recently_seen(url, self.settings.recheck_days):
                                metrics["duplicates"] += 1
                                event("JOB_DUPLICATE", url=url)
                                continue
                            seen.add(url)
                            host = urlsplit(url).hostname
                            # Exclude unknown/aggregator domains before spending scrape credits.
                            if host not in ATS_HOSTS and host not in self.sources.get("official_domains", {}):
                                metrics["filtered"] += 1
                                continue
                            hint = result.get("title", "") + " " + result.get("description", "") + " " + url
                            if not re.search(r"intern", hint, re.I):
                                metrics["filtered"] += 1
                                continue
                            urls.append(url)
                    except Exception as exc:
                        metrics["errors"] += 1
                        event("ERROR", stage="search", error_type=type(exc).__name__)
                for url in urls[: self.settings.max_scrapes]:
                    try:
                        metrics["scraped"] += 1
                        page = self.firecrawl.scrape(url)
                        event("JOB_SCRAPED", source="firecrawl", url=url)
                        job = extract(page, url)
                        if job is None:
                            metrics["filtered"] += 1
                            self.repo.record_source(url, "UNVERIFIED")
                            event("JOB_REJECTED", url=url, reason="No unambiguous structured JobPosting")
                            continue
                        self.process(job, metrics)
                    except Exception as exc:
                        metrics["errors"] += 1
                        self.repo.record_source(url, "ERROR")
                        event("ERROR", stage="scrape", url=url, error_type=type(exc).__name__)
            if not successful_sources:
                metrics["errors"] += 1
                event(
                    "ERROR",
                    stage="configuration",
                    reason="No successful discovery source; configure Firecrawl or public ATS boards",
                )
            event("JOBS_DISCOVERED", count=metrics["discovered"])
            if sync and self.settings.sheet_id:
                try:
                    sheets = self.sheets or GoogleSheets(self.settings)
                    metrics.update(sheets.sync(self.repo.jobs(), self.repo))
                    event("SHEET_UPDATED", added=metrics["sheet_added"], updated=metrics["sheet_updated"])
                except Exception as exc:
                    metrics["errors"] += 1
                    event("ERROR", stage="sheets", error_type=type(exc).__name__)
            state = "COMPLETED" if not metrics["errors"] else "PARTIAL" if successful_sources else "FAILED"
        except Exception as exc:
            metrics["errors"] += 1
            state = "FAILED"
            event("ERROR", stage="pipeline", error_type=type(exc).__name__)
        finally:
            metrics.update(weekly_progress(self.repo.jobs(), self.profile))
            self.repo.finish_run(run_id, metrics, locals().get("state", "FAILED"))
        event("SEARCH_COMPLETED", state=state, **metrics)
        return {"state": state, **metrics}

    def process(self, job, metrics):
        verify(job, self.sources.get("official_domains", {}))
        eligibility = evaluate(job, self.profile)
        match = match_job(job, self.profile, eligibility)
        if job.verification_status not in EXPORTABLE or not eligibility.accepted:
            metrics["filtered"] += 1
            self.repo.record_source(job.source_url, "REJECTED")
            self.repo.invalidate(
                job.source_url, job.application_url, eligibility.reasons + job.verification_reasons
            )
            reasons = eligibility.reasons[:]
            if job.verification_status not in EXPORTABLE:
                reasons.extend(job.verification_reasons)
            self.record_near_match(job, match, eligibility, reasons)
            event(
                "JOB_REJECTED",
                url=normalize_url(job.source_url),
                reasons=eligibility.reasons + job.verification_reasons,
            )
            return
        metrics["verified"] += 1
        event("JOB_VERIFIED", url=normalize_url(job.source_url), status=job.verification_status)
        if match.match_score < self.settings.min_match_score:
            metrics["filtered"] += 1
            self.repo.record_source(job.source_url, "LOW_MATCH")
            self.repo.invalidate(
                job.source_url,
                job.application_url,
                [f"Match score {match.match_score} is below the required {self.settings.min_match_score}"],
            )
            self.record_near_match(
                job,
                match,
                eligibility,
                [f"Match score {match.match_score} is below the required {self.settings.min_match_score}"],
            )
            event("JOB_REJECTED", url=normalize_url(job.source_url), reason="Below match score threshold")
            return
        if self.analyzer and metrics["llm_calls"] < self.settings.max_llm_calls:
            metrics["llm_calls"] += 1
            try:
                match.ai_notes = self.analyzer.analyze(job, match)
            except Exception as exc:
                metrics["errors"] += 1
                event("ERROR", stage="ai", error_type=type(exc).__name__)
        _, added = self.repo.upsert(job, match)
        self.repo.resolve_rejection(job)
        metrics["matched"] += 1
        metrics["added" if added else "duplicates"] += 1
        self.repo.record_source(job.source_url, "ACCEPTED")
        event(
            "JOB_MATCHED" if added else "JOB_DUPLICATE",
            url=normalize_url(job.source_url),
            score=match.match_score,
        )

    def record_near_match(self, job, match, eligibility, reasons):
        unrelated = {
            "Not explicitly an internship",
            "Outside target technical roles",
            "Role is unrelated or too senior",
        }
        if not unrelated.intersection(eligibility.reasons) and match.matching_skills:
            self.repo.record_rejection(job, match, reasons)
