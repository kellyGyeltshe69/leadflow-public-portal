from __future__ import annotations

import json
import logging
import math
import threading
from datetime import datetime, timedelta, timezone
from typing import cast

from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from .ai import generate_drafts
from .composio_gateway import ComposioGateway
from .config import get_settings
from .db import session_scope
from .discovery import discovery_provider, enrich_public_contact, query_pairs
from .gmail import GmailClient, add_dnc, cancel_future_messages, sync_gmail_replies
from .models import Campaign, DoNotContact, Evidence, JobRun, Lead, Message, utcnow
from .outreach import compose_final_body, validate_send_body
from .runtime import get_runtime_mode
from .scoring import score_lead
from .services.advanced_audit import AdvancedAuditService
from .services.compat_sync import sync_business_from_legacy, sync_email_from_legacy
from .services.intelligence import apply_intelligence
from .services.search_sources import LicensedSearchSources
from .timeutils import in_business_window, next_business_send, timezone_for_state
from .utils import domain_of, normalize_business_name, normalize_email

log = logging.getLogger(__name__)
OFFSETS = {0: 0, 1: 3, 2: 7, 3: 14}
_DISCOVERY_LOCK = threading.Lock()


def _utc_date():
    return datetime.now(timezone.utc).date()


def ensure_default_campaign() -> int:
    settings = get_settings()
    with session_scope() as session:
        campaign = session.scalar(select(Campaign).limit(1))
        if not campaign:
            campaign = Campaign(
                name="US Small Business Website Opportunities",
                active=True,
                locations=settings.locations,
                industries=settings.industries,
                daily_limit=settings.daily_new_lead_limit,
                minimum_score=settings.minimum_lead_score,
                discovery_hour_utc=settings.discovery_hour_utc,
            )
            session.add(campaign)
            session.flush()
        return campaign.id


def _job_start(job_type: str) -> int:
    with session_scope() as session:
        job = JobRun(job_type=job_type, status="running")
        session.add(job)
        session.flush()
        return job.id


def _job_finish(job_id: int, summary: str = "", error: str = "") -> None:
    with session_scope() as session:
        job = session.get(JobRun, job_id)
        if job:
            final_status = "failed" if error else "completed"
            job.status = final_status
            if summary:
                job.summary = summary
            elif job.summary:
                try:
                    payload = json.loads(job.summary)
                except (TypeError, ValueError):
                    payload = None
                if isinstance(payload, dict):
                    payload["status"] = final_status
                    payload["updated_at"] = utcnow().isoformat(timespec="seconds") + "Z"
                    if error:
                        payload["phase"] = "failed"
                        payload["message"] = "Discovery stopped because the current step failed."
                    job.summary = json.dumps(payload, ensure_ascii=True, default=str)
            job.error = error
            job.finished_at = utcnow()


def _job_progress(job_id: int, phase: str, message: str, **details: object) -> None:
    """Persist a safe, structured discovery snapshot and short activity trail."""
    with session_scope() as session:
        job = session.get(JobRun, job_id)
        if not job or job.status != "running":
            return
        try:
            existing = json.loads(job.summary) if job.summary else {}
        except (TypeError, ValueError):
            existing = {}
        if not isinstance(existing, dict):
            existing = {}
        raw_events = existing.get("events")
        events = (
            [cast(dict[str, object], item) for item in raw_events if isinstance(item, dict)]
            if isinstance(raw_events, list)
            else []
        )
        timestamp = utcnow().isoformat(timespec="seconds") + "Z"
        event: dict[str, object] = {"at": timestamp, "phase": phase, "message": message}
        if not events or events[-1].get("phase") != phase or events[-1].get("message") != message:
            events.append(event)
        payload = {**existing, **details}
        payload.update({
            "status": "running",
            "phase": phase,
            "message": message,
            "updated_at": timestamp,
            "events": events[-12:],
        })
        job.summary = json.dumps(payload, ensure_ascii=True, default=str)


def _is_duplicate(session, candidate) -> bool:
    conditions = []
    if candidate.place_id:
        conditions.append(Lead.place_id == candidate.place_id)
    normalized = normalize_business_name(candidate.name)
    conditions.append((Lead.normalized_name == normalized) & (Lead.city == candidate.city))
    return bool(session.scalar(select(Lead.id).where(or_(*conditions)).limit(1)))


def _discovery_provider_for_mode(runtime_mode: str):
    """Select from persisted runtime mode, never the seed-only DEMO_MODE value."""
    return discovery_provider(demo_mode=runtime_mode == "demo")


def _dnc(session, email: str | None) -> bool:
    email = normalize_email(email or "")
    return bool(email and session.scalar(select(DoNotContact.id).where(DoNotContact.email == email).limit(1)))


def _evidence_confidence(kind: str, observation: str) -> str:
    text = observation.lower()
    if any(word in text for word in ["could not", "unreachable", "inconclusive", "attempted"]):
        return "low"
    if kind in {"site_check", "public_email", "contact_page", "mobile_browser", "robots", "social_only", "security"}:
        return "high"
    if kind in {"search_snippet_email", "official_site_candidate", "discovery"}:
        return "medium"
    return "medium"


def _default_opportunity(website_status: str, issues: list[str], industry: str) -> str:
    if website_status in {"no_website_found", "social_only"}:
        return "Create a small first-party site with current business details, proof and one clear inquiry or booking path."
    if "expired" in website_status or "broken" in website_status or "unreachable" in website_status:
        return "Restore a reliable first-party page first, then publish current customer information and a direct inquiry path."
    if issues:
        return "Fix the verified customer-journey gaps before considering a platform or hosting change."
    return f"No urgent rebuild was verified; consider only a focused conversion improvement relevant to this {industry}."


def _business_payload(lead: Lead) -> dict:
    return {
        "business_name": lead.business_name,
        "industry": lead.industry,
        "city": lead.city,
        "state": lead.state,
        "website_url": lead.website_url,
        "website_status": lead.website_status,
        "public_contact_type": "public business email" if lead.contact_email else "contact page" if lead.contact_page else "none",
        "issues": lead.issues,
        "verified_facts": {k: v for k, v in (lead.audit_facts or {}).items() if k != "body_text_excerpt"},
        "opportunity": lead.opportunity,
        "scores": {
            "need": lead.need_score,
            "ability_to_pay_inferred": lead.ability_score,
            "growth": lead.growth_score,
            "response_likelihood": lead.response_score,
            "weighted": lead.weighted_score,
        },
        "score_limitations": "Ability to pay and growth are rubric-based estimates from public business type/signals, not private financial data.",
    }


def _create_messages(session, lead: Lead, bundle) -> None:
    by_stage = {int(item["stage"]): item for item in bundle.messages}
    for stage in range(4):
        item = by_stage[stage]
        message = Message(
            lead_id=lead.id,
            stage=stage,
            day_offset=OFFSETS[stage],
            subject=str(item["subject"]).strip(),
            body_core=str(item["body_core"]).strip(),
            status="draft",
        )
        session.add(message)
        session.flush()
        message.body_final = compose_final_body(lead, message, allow_postal_placeholder=True)
        sync_email_from_legacy(session, lead, message)


def discover_job(
    force: bool = False,
    *,
    candidate_target: int | None = None,
    qualified_target: int | None = None,
    job_type: str = "discover",
    campaign_id: int | None = None,
) -> dict:
    result: dict[str, object]
    job_id = _job_start(job_type)
    if not _DISCOVERY_LOCK.acquire(blocking=False):
        result = {"status": "skipped", "reason": "another discovery job is already running"}
        _job_finish(job_id, str(result))
        return result
    settings = get_settings()
    runtime_mode = get_runtime_mode()
    demo_mode = runtime_mode == "demo"
    try:
        _job_progress(
            job_id,
            "starting",
            "Starting discovery and loading campaign settings.",
            data_mode=runtime_mode,
            provider="Demo fixtures" if demo_mode else "Google Maps via Composio",
        )
        if not demo_mode:
            _job_progress(
                job_id,
                "validating_google_maps",
                "Checking the active Composio Google Maps connection.",
                data_mode=runtime_mode,
                provider="Google Maps via Composio",
            )
            if not settings.composio_ready:
                raise RuntimeError("COMPOSIO_API_KEY and COMPOSIO_USER_ID are required when DEMO_MODE=false")
            ComposioGateway().resolve_connection_id("google_maps")
            _job_progress(
                job_id,
                "google_maps_ready",
                "Google Maps connection is active. Preparing search queries.",
                data_mode=runtime_mode,
                provider="Google Maps via Composio",
            )
        else:
            _job_progress(
                job_id,
                "demo_provider_ready",
                "Demo mode is using synthetic sample businesses; Google Maps is not called.",
                data_mode=runtime_mode,
                provider="Demo fixtures",
            )
        campaign_id = campaign_id or ensure_default_campaign()
        with session_scope() as session:
            campaign = session.get(Campaign, campaign_id)
            today = _utc_date().isoformat()
            mode_date = f"{runtime_mode}:{today}"
            if not campaign.active and not force:
                result = {"status": "skipped", "reason": "campaign inactive"}
                _job_finish(job_id, str(result))
                return result
            if campaign.last_discovery_date in {today, mode_date} and not force:
                result = {"status": "skipped", "reason": "already ran today"}
                _job_finish(job_id, str(result))
                return result
            locations = campaign.locations or settings.locations
            industries = campaign.industries or settings.industries
            daily_limit = min(campaign.daily_limit, 50)
            minimum_score = campaign.minimum_score

        candidate_goal = max(1, min(candidate_target or max(daily_limit * 2, daily_limit), 500))
        qualified_goal = max(1, min(qualified_target or daily_limit, 500))
        # Runtime mode is persisted independently from the seed-only DEMO_MODE
        # environment value. Always select the provider from the persisted mode.
        provider = _discovery_provider_for_mode(runtime_mode)
        qualified = 0
        inserted = 0
        candidates_seen = 0
        duplicates_skipped = 0
        websites_audited = 0
        public_contacts_found = 0
        queries_attempted = 0
        pairs = query_pairs(locations, industries)
        max_queries = 1 if demo_mode else min(len(pairs), max(5, math.ceil(candidate_goal / 8) + 5))
        _job_progress(
            job_id,
            "ready_to_search",
            f"Prepared up to {max_queries} discovery search queries.",
            data_mode=runtime_mode,
            provider="Demo fixtures" if demo_mode else "Google Maps via Composio",
            candidates_seen=0,
            inserted=0,
            duplicates_skipped=0,
            websites_audited=0,
            public_contacts_found=0,
            pending_approval=0,
            candidate_target=candidate_goal,
            queries_attempted=0,
            current_query="",
            current_business="",
            current_website="",
        )
        for location, industry in pairs[:max_queries]:
            if candidates_seen >= candidate_goal or qualified >= qualified_goal or inserted >= candidate_goal:
                break
            query_prefix = "newly opened independent" if (candidates_seen // 10) % 2 else "independent"
            query = f"{query_prefix} {industry} in {location}"
            request_limit = min(20, max(10, candidate_goal - candidates_seen))
            queries_attempted += 1
            _job_progress(
                job_id,
                "searching_google_maps" if not demo_mode else "searching_demo",
                f"Searching {'Google Maps' if not demo_mode else 'demo fixtures'} for: {query}",
                data_mode=runtime_mode,
                provider="Demo fixtures" if demo_mode else "Google Maps via Composio",
                current_query=query,
                current_business="",
                current_website="",
                queries_attempted=queries_attempted,
                candidates_seen=candidates_seen,
                inserted=inserted,
                duplicates_skipped=duplicates_skipped,
                websites_audited=websites_audited,
                public_contacts_found=public_contacts_found,
                pending_approval=qualified,
                candidate_target=candidate_goal,
            )
            try:
                candidates = provider.search(query, limit=request_limit)
            except Exception as exc:
                log.warning("Discovery query failed (%s): %s", query, exc)
                _job_progress(
                    job_id,
                    "search_query_failed",
                    "This search query failed; LeadFlow is moving to the next configured query.",
                    data_mode=runtime_mode,
                    provider="Demo fixtures" if demo_mode else "Google Maps via Composio",
                    current_query=query,
                    query_candidates=0,
                    queries_attempted=queries_attempted,
                    candidates_seen=candidates_seen,
                    inserted=inserted,
                    duplicates_skipped=duplicates_skipped,
                    websites_audited=websites_audited,
                    public_contacts_found=public_contacts_found,
                    pending_approval=qualified,
                    candidate_target=candidate_goal,
                )
                continue
            _job_progress(
                job_id,
                "search_results_received",
                f"The provider returned {len(candidates)} candidate business(es) for this query.",
                data_mode=runtime_mode,
                provider="Demo fixtures" if demo_mode else "Google Maps via Composio",
                current_query=query,
                query_candidates=len(candidates),
                queries_attempted=queries_attempted,
                candidates_seen=candidates_seen,
                inserted=inserted,
                duplicates_skipped=duplicates_skipped,
                websites_audited=websites_audited,
                public_contacts_found=public_contacts_found,
                pending_approval=qualified,
                candidate_target=candidate_goal,
            )
            for candidate in candidates:
                if candidates_seen >= candidate_goal or qualified >= qualified_goal or inserted >= candidate_goal:
                    break
                candidates_seen += 1
                business_label = candidate.name[:160]
                with session_scope() as session:
                    duplicate = _is_duplicate(session, candidate)
                if duplicate:
                    duplicates_skipped += 1
                    _job_progress(
                        job_id,
                        "duplicate_skipped",
                        f"Skipped an existing business: {business_label}",
                        data_mode=runtime_mode,
                        provider="Demo fixtures" if demo_mode else "Google Maps via Composio",
                        current_query=query,
                        current_business=business_label,
                        current_website="",
                        candidates_seen=candidates_seen,
                        inserted=inserted,
                        duplicates_skipped=duplicates_skipped,
                        websites_audited=websites_audited,
                        public_contacts_found=public_contacts_found,
                        pending_approval=qualified,
                        candidate_target=candidate_goal,
                    )
                    continue

                # Network and model work deliberately happens outside a DB
                # transaction so long audits cannot exhaust the connection pool.
                _job_progress(
                    job_id,
                    "finding_public_contact",
                    f"Finding the official website and public contact route for {business_label}.",
                    data_mode=runtime_mode,
                    provider="Demo fixtures" if demo_mode else "Google Maps via Composio",
                    current_query=query,
                    current_business=business_label,
                    current_website=domain_of(candidate.website_url),
                    candidates_seen=candidates_seen,
                    inserted=inserted,
                    duplicates_skipped=duplicates_skipped,
                    websites_audited=websites_audited,
                    public_contacts_found=public_contacts_found,
                    pending_approval=qualified,
                    candidate_target=candidate_goal,
                )
                contact = enrich_public_contact(candidate)
                if contact.email or contact.contact_page:
                    public_contacts_found += 1
                website_url = contact.website_url or candidate.website_url
                website_domain = domain_of(website_url)
                _job_progress(
                    job_id,
                    "auditing_website",
                    (
                        f"Auditing the public website for {business_label}."
                        if website_url
                        else f"No independent website was found for {business_label}; recording that result."
                    ),
                    data_mode=runtime_mode,
                    provider="Demo fixtures" if demo_mode else "Google Maps via Composio",
                    current_query=query,
                    current_business=business_label,
                    current_website=website_domain,
                    candidates_seen=candidates_seen,
                    inserted=inserted,
                    duplicates_skipped=duplicates_skipped,
                    websites_audited=websites_audited,
                    public_contacts_found=public_contacts_found,
                    pending_approval=qualified,
                    candidate_target=candidate_goal,
                )
                audit = AdvancedAuditService().audit_result(website_url)
                if website_url:
                    websites_audited += 1
                audit.facts.update({
                    "google_rating": candidate.google_rating,
                    "google_review_count": candidate.google_review_count,
                })
                extended_sources = []
                if settings.extended_source_discovery and not demo_mode:
                    _job_progress(
                        job_id,
                        "checking_extended_sources",
                        f"Checking licensed search sources for {business_label}.",
                        data_mode=runtime_mode,
                        provider="Google Maps via Composio",
                        current_query=query,
                        current_business=business_label,
                        current_website=website_domain,
                        candidates_seen=candidates_seen,
                        inserted=inserted,
                        duplicates_skipped=duplicates_skipped,
                        websites_audited=websites_audited,
                        public_contacts_found=public_contacts_found,
                        pending_approval=qualified,
                        candidate_target=candidate_goal,
                    )
                    try:
                        extended_sources = LicensedSearchSources().search(
                            candidate.name,
                            candidate.city,
                            candidate.state,
                        )
                    except Exception as exc:
                        log.warning("Extended source discovery failed for %s: %s", candidate.name, exc)
                _job_progress(
                    job_id,
                    "scoring_candidate",
                    f"Scoring verified website and contact evidence for {business_label}.",
                    data_mode=runtime_mode,
                    provider="Demo fixtures" if demo_mode else "Google Maps via Composio",
                    current_query=query,
                    current_business=business_label,
                    current_website=website_domain,
                    candidates_seen=candidates_seen,
                    inserted=inserted,
                    duplicates_skipped=duplicates_skipped,
                    websites_audited=websites_audited,
                    public_contacts_found=public_contacts_found,
                    pending_approval=qualified,
                    candidate_target=candidate_goal,
                )
                score = score_lead(
                    audit.website_status,
                    audit.issues,
                    candidate.industry or industry,
                    bool(contact.email),
                    bool(contact.contact_page),
                    candidate.growth_signals,
                )
                lead = Lead(
                    campaign_id=campaign_id,
                    place_id=candidate.place_id,
                    business_name=candidate.name,
                    normalized_name=normalize_business_name(candidate.name),
                    industry=candidate.industry or industry,
                    city=candidate.city,
                    state=candidate.state,
                    formatted_address=candidate.formatted_address,
                    latitude=candidate.latitude,
                    longitude=candidate.longitude,
                    timezone_name=timezone_for_state(candidate.state),
                    website_url=website_url,
                    contact_email=normalize_email(contact.email or "") or None,
                    contact_page=contact.contact_page,
                    maps_url=candidate.maps_url,
                    source_url=candidate.source_url,
                    discovery_provider=candidate.provider,
                    data_mode=runtime_mode,
                    website_status=audit.website_status,
                    audit_facts=audit.facts,
                    issues=audit.issues,
                    opportunity=_default_opportunity(audit.website_status, audit.issues, candidate.industry or industry),
                    need_score=score.need,
                    ability_score=score.ability,
                    growth_score=score.growth,
                    response_score=score.response,
                    weighted_score=score.weighted,
                    score_reasons=score.reasons,
                    last_verified_at=utcnow(),
                )
                if not lead.contact_email:
                    lead.status = "needs_contact"
                elif score.weighted < minimum_score:
                    lead.status = "unqualified"
                else:
                    lead.status = "pending_approval"
                bundle = None
                if lead.status == "pending_approval":
                    _job_progress(
                        job_id,
                        "generating_drafts",
                        (
                            f"Generating safe deterministic demo drafts for {business_label}."
                            if demo_mode
                            else f"Generating review-only outreach drafts for {business_label} with {settings.ai_provider}."
                        ),
                        data_mode=runtime_mode,
                        provider="Demo fixtures" if demo_mode else "Google Maps via Composio",
                        ai_provider="deterministic_demo" if demo_mode else settings.ai_provider,
                        current_query=query,
                        current_business=business_label,
                        current_website=website_domain,
                        candidates_seen=candidates_seen,
                        inserted=inserted,
                        duplicates_skipped=duplicates_skipped,
                        websites_audited=websites_audited,
                        public_contacts_found=public_contacts_found,
                        pending_approval=qualified,
                        candidate_target=candidate_goal,
                    )
                    bundle = generate_drafts(_business_payload(lead), demo_mode=demo_mode)
                    lead.analysis_summary = bundle.analysis_summary
                    lead.opportunity = bundle.opportunity or lead.opportunity
                    lead.checklist = bundle.checklist

                _job_progress(
                    job_id,
                    "saving_candidate",
                    f"Saving {business_label} as {lead.status.replace('_', ' ')} with its evidence.",
                    data_mode=runtime_mode,
                    provider="Demo fixtures" if demo_mode else "Google Maps via Composio",
                    current_query=query,
                    current_business=business_label,
                    current_website=website_domain,
                    candidates_seen=candidates_seen,
                    inserted=inserted,
                    duplicates_skipped=duplicates_skipped,
                    websites_audited=websites_audited,
                    public_contacts_found=public_contacts_found,
                    pending_approval=qualified,
                    candidate_target=candidate_goal,
                )
                with session_scope() as session:
                    if _is_duplicate(session, candidate):
                        continue
                    if _dnc(session, lead.contact_email):
                        lead.status = "do_not_contact"
                        bundle = None
                    session.add(lead)
                    session.flush()
                    if candidate.source_url:
                        discovery_observation = f"Discovered through {candidate.provider}; verify all details before outreach."
                        session.add(Evidence(
                            lead_id=lead.id,
                            evidence_type="discovery",
                            observation=discovery_observation,
                            source_url=candidate.source_url,
                            confidence=_evidence_confidence("discovery", discovery_observation),
                        ))
                    for kind, observation, evidence_url in contact.evidence + audit.evidence:
                        if evidence_url:
                            session.add(Evidence(
                                lead_id=lead.id,
                                evidence_type=kind,
                                observation=observation,
                                source_url=evidence_url,
                                confidence=_evidence_confidence(kind, observation),
                            ))
                    for source_result in extended_sources:
                        if source_result.url:
                            session.add(Evidence(
                                lead_id=lead.id,
                                evidence_type=source_result.source,
                                observation=f"Licensed search result: {source_result.title}"[:1000],
                                source_url=source_result.url,
                                confidence="medium",
                            ))
                    sync_business_from_legacy(session, lead)
                    if bundle is not None and lead.status == "pending_approval":
                        _create_messages(session, lead, bundle)
                        session.flush()
                        session.refresh(lead, attribute_names=["messages"])
                        apply_intelligence(session, lead)
                        qualified += 1
                    inserted += 1
                _job_progress(
                    job_id,
                    "candidate_saved",
                    f"Saved {business_label} as {lead.status.replace('_', ' ')}.",
                    data_mode=runtime_mode,
                    provider="Demo fixtures" if demo_mode else "Google Maps via Composio",
                    current_query=query,
                    current_business=business_label,
                    current_website=website_domain,
                    current_lead_status=lead.status,
                    current_score=round(lead.weighted_score, 1),
                    candidates_seen=candidates_seen,
                    inserted=inserted,
                    duplicates_skipped=duplicates_skipped,
                    websites_audited=websites_audited,
                    public_contacts_found=public_contacts_found,
                    pending_approval=qualified,
                    candidate_target=candidate_goal,
                )
        with session_scope() as session:
            campaign = session.get(Campaign, campaign_id)
            campaign.last_discovery_date = f"{runtime_mode}:{_utc_date().isoformat()}"
        result = {
            "status": "completed",
            "candidates_seen": candidates_seen,
            "inserted": inserted,
            "duplicates_skipped": duplicates_skipped,
            "websites_audited": websites_audited,
            "public_contacts_found": public_contacts_found,
            "pending_approval": qualified,
            "candidate_target": candidate_goal,
            "qualified_target": qualified_goal,
            "queries_attempted": queries_attempted,
            "demo_mode": demo_mode,
            "data_mode": runtime_mode,
        }
        _job_progress(
            job_id,
            "completed",
            (
                f"Discovery completed: saved {inserted}, skipped {duplicates_skipped} duplicate(s), "
                f"and queued {qualified} for human review."
            ),
            **result,
        )
        _job_finish(job_id)
        return result
    except Exception as exc:
        log.exception("Discovery job failed")
        _job_finish(job_id, error=str(exc))
        raise
    finally:
        _DISCOVERY_LOCK.release()


def fast_start_job(ignore_cooldown: bool = False) -> dict:
    settings = get_settings()
    if not settings.fast_start_enabled:
        return {"status": "skipped", "reason": "fast start disabled"}
    runtime_mode = get_runtime_mode()
    job_type = f"startup_discover_{runtime_mode}"
    cutoff = utcnow() - timedelta(hours=settings.startup_discovery_cooldown_hours)
    with session_scope() as session:
        recent = session.scalar(
            select(JobRun.id)
            .where(
                JobRun.job_type == job_type,
                JobRun.status == "completed",
                JobRun.started_at >= cutoff,
                JobRun.summary.like("%candidate_target%"),
            )
            .limit(1)
        )
    if recent and not ignore_cooldown:
        return {"status": "skipped", "reason": "startup discovery completed within cooldown"}
    return discover_job(
        force=True,
        candidate_target=settings.startup_discovery_target,
        qualified_target=settings.startup_discovery_target,
        job_type=job_type,
    )

def reverify_lead(lead_id: int) -> dict:
    job_id = _job_start("reverify")
    try:
        with session_scope() as session:
            lead = session.scalar(select(Lead).options(selectinload(Lead.messages)).where(Lead.id == lead_id))
            if not lead:
                raise ValueError("Lead not found")
            audit = AdvancedAuditService().audit_result(lead.website_url)
            score = score_lead(
                audit.website_status,
                audit.issues,
                lead.industry,
                bool(lead.contact_email),
                bool(lead.contact_page),
                [],
                audit.facts,
            )
            lead.website_status = audit.website_status
            lead.audit_facts = audit.facts
            lead.issues = audit.issues
            lead.need_score, lead.ability_score, lead.growth_score, lead.response_score, lead.weighted_score = score.need, score.ability, score.growth, score.response, score.weighted
            lead.score_reasons = score.reasons
            lead.last_verified_at = utcnow()
            for kind, observation, source in audit.evidence:
                session.add(Evidence(
                    lead_id=lead.id,
                    evidence_type=kind,
                    observation=observation,
                    source_url=source,
                    confidence=_evidence_confidence(kind, observation),
                ))
            if all(m.status == "draft" for m in lead.messages):
                bundle = generate_drafts(_business_payload(lead), demo_mode=lead.data_mode == "demo")
                lead.analysis_summary, lead.opportunity, lead.checklist = bundle.analysis_summary, bundle.opportunity, bundle.checklist
                by_stage = {int(x["stage"]): x for x in bundle.messages}
                for message in lead.messages:
                    message.subject = by_stage[message.stage]["subject"]
                    message.body_core = by_stage[message.stage]["body_core"]
                    message.body_final = compose_final_body(lead, message, allow_postal_placeholder=True)
                    sync_email_from_legacy(session, lead, message)
            sync_business_from_legacy(session, lead)
            if lead.messages:
                apply_intelligence(session, lead)
            result = {"lead_id": lead.id, "website_status": lead.website_status, "score": lead.weighted_score}
        _job_finish(job_id, str(result))
        return result
    except Exception as exc:
        _job_finish(job_id, error=str(exc))
        raise


def approve_lead(lead_id: int) -> None:
    settings = get_settings()
    if not settings.postal_ready:
        raise ValueError("Add a valid PHYSICAL_POSTAL_ADDRESS before approving outreach")
    with session_scope() as session:
        lead = session.scalar(select(Lead).options(selectinload(Lead.messages)).where(Lead.id == lead_id))
        if not lead:
            raise ValueError("Lead not found")
        if lead.data_mode != "live":
            raise ValueError("Demo leads cannot be approved for sending. Switch to Live mode and discover real leads.")
        if lead.status not in {"pending_approval", "approved"}:
            raise ValueError(f"Lead status {lead.status!r} cannot be approved")
        if not lead.contact_email or _dnc(session, lead.contact_email):
            raise ValueError("The public email is missing or on the do-not-contact list")
        if not lead.last_verified_at or utcnow() - lead.last_verified_at > timedelta(days=3):
            raise ValueError("Reverify this lead before approval; verification is older than three days")
        initial_at = next_business_send(utcnow(), lead.timezone_name, 0)
        strategy_days = (
            ((lead.audit_facts or {}).get("ai_intelligence") or {})
            .get("followup_strategy", {})
            .get("days", [])
        )
        for message in lead.messages:
            if message.stage > 0 and len(strategy_days) >= message.stage:
                message.day_offset = int(strategy_days[message.stage - 1])
            message.body_final = compose_final_body(lead, message, allow_postal_placeholder=False)
            errors = validate_send_body(lead, message)
            if errors:
                raise ValueError("; ".join(errors))
            message.planned_at = next_business_send(initial_at, lead.timezone_name, message.day_offset)
            message.status = "scheduled"
            message.error = None
            sync_email_from_legacy(session, lead, message)
        lead.status = "approved"
        sync_business_from_legacy(session, lead)
        lead.approved_at = utcnow()


def reject_lead(lead_id: int, reason: str) -> None:
    with session_scope() as session:
        lead = session.scalar(select(Lead).options(selectinload(Lead.messages)).where(Lead.id == lead_id))
        if not lead:
            raise ValueError("Lead not found")
        lead.status = "rejected"
        lead.rejected_at = utcnow()
        lead.rejection_reason = reason.strip() or "Rejected during human review"
        cancel_future_messages(session, lead, "Rejected during human review")
        sync_business_from_legacy(session, lead)
        for message in lead.messages:
            sync_email_from_legacy(session, lead, message)


def send_due_job() -> dict:
    result: dict[str, object]
    job_id = _job_start("send_due")
    settings = get_settings()
    runtime_mode = get_runtime_mode()
    demo_mode = runtime_mode == "demo"
    try:
        if not settings.production_send_ready_for(demo_mode):
            result = {"status": "blocked", "reason": "Production send gate is not satisfied. Check /health."}
            _job_finish(job_id, str(result))
            return result
        try:
            client = GmailClient()
        except RuntimeError as exc:
            result = {"status": "blocked", "reason": str(exc)}
            _job_finish(job_id, str(result))
            return result
        now = utcnow()
        today_start = datetime.combine(_utc_date(), datetime.min.time())
        with session_scope() as session:
            total_sent_today = session.scalar(
                select(func.count(Message.id)).where(
                    Message.sent_at >= today_start,
                    Message.lead.has(Lead.data_mode == "live"),
                )
            ) or 0
            initial_sent_today = session.scalar(
                select(func.count(Message.id)).where(
                    Message.sent_at >= today_start,
                    Message.stage == 0,
                    Message.lead.has(Lead.data_mode == "live"),
                )
            ) or 0
            available_total = max(0, settings.effective_total_send_limit - total_sent_today)
            available_initial = max(0, settings.daily_new_lead_limit - initial_sent_today)
            due = session.scalars(
                select(Message)
                .options(selectinload(Message.lead).selectinload(Lead.messages))
                .where(
                    Message.status.in_(["scheduled", "retry"]),
                    Message.planned_at <= now,
                    Message.lead.has(Lead.data_mode == "live"),
                )
                .order_by(Message.planned_at)
                .limit(available_total)
            ).all()
            sent = 0
            skipped = 0
            failed = 0
            for message in due:
                lead = message.lead
                if message.stage == 0 and available_initial <= 0:
                    skipped += 1
                    continue
                if lead.status in {"replied", "opted_out", "bounced", "rejected", "do_not_contact"} or _dnc(session, lead.contact_email):
                    message.status = "cancelled"
                    message.error = "Lead no longer eligible"
                    skipped += 1
                    continue
                if message.stage > 0:
                    prior = next((x for x in lead.messages if x.stage == message.stage - 1), None)
                    if not prior or prior.status != "sent":
                        message.planned_at = now + timedelta(hours=2)
                        skipped += 1
                        continue
                if not in_business_window(now, lead.timezone_name):
                    message.planned_at = next_business_send(now, lead.timezone_name, 0)
                    skipped += 1
                    continue
                errors = validate_send_body(lead, message)
                if errors:
                    message.status = "failed"
                    message.error = "; ".join(errors)
                    failed += 1
                    continue
                try:
                    response = client.send(lead, message)
                    message.gmail_message_id = response["gmail_message_id"]
                    message.gmail_thread_id = response["thread_id"]
                    message.rfc_message_id = response["rfc_message_id"]
                    message.sent_at = now
                    message.status = "sent"
                    lead.gmail_thread_id = response["thread_id"]
                    if message.stage == 0:
                        lead.root_rfc_message_id = response["rfc_message_id"]
                        available_initial -= 1
                    lead.status = "sequence_complete" if message.stage == 3 else "sequence_active"
                    sync_email_from_legacy(session, lead, message)
                    sync_business_from_legacy(session, lead)
                    sent += 1
                except Exception as exc:
                    message.retry_count += 1
                    message.error = str(exc)
                    if message.retry_count >= 3:
                        message.status = "failed"
                        failed += 1
                    else:
                        message.status = "retry"
                        message.planned_at = now + timedelta(minutes=30 * message.retry_count)
            result = {
                "status": "completed",
                "due": len(due),
                "sent": sent,
                "skipped": skipped,
                "failed": failed,
                "configured_total_cap": settings.daily_total_send_limit,
                "effective_total_cap": settings.effective_total_send_limit,
                "runtime_mode": runtime_mode,
            }
        _job_finish(job_id, str(result))
        return result
    except Exception as exc:
        log.exception("Send job failed")
        _job_finish(job_id, error=str(exc))
        raise


def sync_replies_job() -> dict:
    job_id = _job_start("sync_replies")
    try:
        runtime_mode = get_runtime_mode()
        with session_scope() as session:
            result = sync_gmail_replies(session, demo_mode=runtime_mode == "demo")
        _job_finish(job_id, str(result))
        return result
    except Exception as exc:
        log.exception("Reply sync failed")
        _job_finish(job_id, error=str(exc))
        raise


def unsubscribe_lead(lead_id: int, source: str = "web") -> bool:
    with session_scope() as session:
        lead = session.scalar(select(Lead).options(selectinload(Lead.messages)).where(Lead.id == lead_id))
        if not lead:
            return False
        lead.status = "opted_out"
        cancel_future_messages(session, lead, "Cancelled after opt-out")
        add_dnc(session, lead.contact_email or "", "opt_out", source)
        return True


def maybe_discover_job() -> dict:
    ensure_default_campaign()
    runtime_mode = get_runtime_mode()
    now = datetime.now(timezone.utc)
    with session_scope() as session:
        campaigns = session.scalars(
            select(Campaign).where(Campaign.active.is_(True), Campaign.status != "archived")
        ).all()
        due_ids = [
            campaign.id
            for campaign in campaigns
            if campaign.last_discovery_date != f"{runtime_mode}:{_utc_date().isoformat()}"
            and now.hour >= campaign.discovery_hour_utc
        ]
    results = []
    for campaign_id in due_ids:
        results.append(
            discover_job(
                force=False,
                campaign_id=campaign_id,
                job_type=f"discover_campaign_{campaign_id}",
            )
        )
    return {"status": "completed", "campaigns_due": len(due_ids), "results": results}
