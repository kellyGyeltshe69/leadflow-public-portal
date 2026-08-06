from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

import httpx

from .config import get_settings
from .services.email_quality import review_email_bundle

log = logging.getLogger(__name__)


@dataclass
class DraftBundle:
    analysis_summary: str
    opportunity: str
    checklist: list[str]
    messages: list[dict]
    quality_review: dict[str, object] = field(default_factory=dict)


def _clean_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _email_safe_issue(issue: str) -> str:
    issue = " ".join(str(issue).split()).strip()
    slow = re.search(r"slow server response \((\d+) ms\)", issue, re.IGNORECASE)
    if slow:
        seconds = int(slow.group(1)) / 1000
        return (
            f"The homepage response took about {seconds:.1f} seconds from the audit server "
            "during that check."
        )
    images = re.search(r"found (\d+) image\(s\) larger than 500 KB", issue, re.IGNORECASE)
    if images:
        count = int(images.group(1))
        noun = "image" if count == 1 else "images"
        return f"The homepage sample found {count} {noun} reporting file sizes above 500 KB."
    if "did not expose a meta description" in issue.lower():
        return (
            "The homepage did not expose a meta description, which can affect how its "
            "search-result snippet is presented."
        )
    if "no mobile viewport" in issue.lower():
        return "The homepage HTML did not expose a mobile viewport tag."
    return re.sub(r";?\s*retest before using this claim\.?$", ".", issue, flags=re.IGNORECASE)


def _fallback(business: dict) -> DraftBundle:
    name = business["business_name"]
    city_state = ", ".join(x for x in [business.get("city"), business.get("state")] if x)
    issues = business.get("issues") or ["No clear independent website was verified."]
    safe_issues = [_email_safe_issue(str(issue)) for issue in issues]
    primary_issue = safe_issues[0]
    opportunity = business.get("opportunity") or "Create a simple first-party page with current business details and a clear inquiry path."
    second_issue = safe_issues[1] if len(safe_issues) > 1 else ""
    observation = " ".join(item.strip() for item in [primary_issue, second_issue] if item).strip()
    observation = observation[:320].rstrip()
    practical_step = (opportunity.split(".", 1)[0].strip() + ".")[:180]
    location_note = f" in {city_state}" if city_state else ""
    checklist = [
        "Confirm the current business name, address, hours and contact details.",
        "Put the most common customer action near the top of the page.",
        "Add a concise service/menu overview and proof such as photos or reviews.",
        "Use a short inquiry form that asks only for details needed to respond.",
        "Test every link on a phone before publishing.",
    ]
    subject = "Two website observations"
    messages = [
        {
            "stage": 0,
            "subject": subject,
            "body_core": (
                f"Hi {name} team,\n\n"
                f"During a recent point-in-time review of your public website{location_note}, "
                f"I noted the following: {observation}\n\n"
                f"One practical first step: {practical_step}\n\n"
                "I summarized the observations and a short checklist in the report linked below."
            ),
        },
        {
            "stage": 1,
            "subject": subject,
            "body_core": (
                f"Hi {name} team,\n\n"
                "One practical follow-up: start with the single customer action that matters most, "
                "then keep only the information needed to complete it.\n\n"
                "Would a short implementation order be useful?"
            ),
        },
        {
            "stage": 2,
            "subject": subject,
            "body_core": (
                f"Hi {name} team,\n\n"
                "A focused first pass can cover current details, core services, proof, and one inquiry action. "
                "I kept the hosted report available below so the verified observations stay in one place."
            ),
        },
        {
            "stage": 3,
            "subject": subject,
            "body_core": (
                f"Hi {name} team,\n\n"
                "I am closing the loop after this note. The suggestion was based only on the public website "
                "observations in the report. No reply is needed if this is not a priority, and I will not follow up again."
            ),
        },
    ]
    bundle = DraftBundle(
        f"Public review found: {primary_issue}",
        opportunity,
        checklist,
        messages,
    )
    bundle.quality_review = review_email_bundle(name, messages)
    return bundle


SYSTEM_PROMPT = """You write ethical, one-to-one B2B outreach for an independent Hostinger affiliate.
You must use only facts supplied in the JSON. Never invent an owner name, performance result, revenue, opening date, missing feature, or technical defect.
The sender does NOT build websites, configure hosting, work for Hostinger, or offer professional audits. He may offer a simple no-cost checklist based on public observations.
Write plain-text, respectful messages. No fake urgency, flattery, fear, guaranteed results, tracking language, or spam phrasing.
Do not include an affiliate link, affiliate disclosure, signature, postal address, or opt-out footer; the application adds those safely.
Address the business as "Hi <business name> team," and never invent an owner or personal name.
Use a point-in-time qualifier for response-time observations. A viewport proves only that a viewport tag exists, not that mobile performance is good. A missing meta description affects search-snippet presentation, not guaranteed rankings. Reported large-image sizes may contribute to load time; do not state certainty.
The application appends a hosted report link to stages 0 and 2. Those stages must not contain a URL, ask whether the reader wants a checklist, or add any competing question CTA. End stage 0 by saying the observations and checklist are summarized in the report linked below.
Stage 1 may contain one low-friction question. Stage 2 adds one new useful idea and refers to the report below without a question. The final follow-up closes the loop and promises no more follow-up.
Avoid generic filler such as "no pressure", "common in your industry", "I hope this finds you well", and broad claims such as "performs well".
Return one JSON object only with keys: analysis_summary, opportunity, checklist, messages.
checklist must contain exactly 5 short items.
messages must contain exactly four objects with stages 0,1,2,3 and keys stage, subject, body_core.
Use truthful 3-7 word subjects with no Re:/Fwd:. Keep stage 0 at 45-110 words, stages 1 and 2 at 25-80 words, and stage 3 at 20-65 words. Internally self-edit every message against these rules before returning only the final JSON."""


def _request_model_content(settings, business: dict) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(business, ensure_ascii=False)},
    ]
    provider = settings.ai_provider.strip().lower()
    if provider == "ollama":
        response = httpx.post(
            settings.ollama_base_url.rstrip("/") + "/api/chat",
            json={
                "model": settings.ollama_model,
                "messages": messages,
                "stream": False,
                "format": "json",
                "keep_alive": "10m",
                "options": {"temperature": 0.2, "num_ctx": settings.ollama_num_ctx},
            },
            timeout=settings.ollama_timeout_seconds,
        )
        response.raise_for_status()
        return response.json()["message"]["content"]
    if provider == "llamacpp":
        response = httpx.post(
            settings.llamacpp_base_url.rstrip("/") + "/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {settings.llamacpp_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": settings.llamacpp_model_alias,
                "messages": messages,
                "temperature": 0.2,
                "max_tokens": 1800,
                "response_format": {"type": "json_object"},
            },
            timeout=settings.llamacpp_timeout_seconds,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    if provider == "openai_compatible":
        response = httpx.post(
            settings.openai_base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"},
            json={"model": settings.openai_model, "temperature": 0.2, "messages": messages},
            timeout=settings.openai_timeout_seconds,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    raise RuntimeError(f"Unsupported AI_PROVIDER: {settings.ai_provider}")


def generate_drafts(business: dict, demo_mode: bool | None = None) -> DraftBundle:
    settings = get_settings()
    if demo_mode is None:
        demo_mode = settings.demo_mode
    if not settings.ai_ready_for(demo_mode):
        return _fallback(business)
    try:
        content = _request_model_content(settings, business)
        data = _clean_json(content)
        messages = data.get("messages") or []
        stages = sorted(int(m.get("stage", -1)) for m in messages)
        if stages != [0, 1, 2, 3] or len(data.get("checklist") or []) != 5:
            raise ValueError("AI response did not match the required four-message/five-item structure")
        for item in messages:
            if not item.get("subject") or not item.get("body_core"):
                raise ValueError("AI message missing subject or body_core")
            # Defense in depth: the model must not inject the affiliate link itself.
            item["body_core"] = item["body_core"].replace(settings.affiliate_url, "").strip()
        bundle = DraftBundle(
            analysis_summary=str(data.get("analysis_summary", "")).strip(),
            opportunity=str(data.get("opportunity", "")).strip(),
            checklist=[str(x).strip() for x in data["checklist"]],
            messages=messages,
        )
        bundle.quality_review = review_email_bundle(
            str(business.get("business_name") or "the business"),
            messages,
        )
        raw_minimum_score = bundle.quality_review.get("minimum_score", 0)
        minimum_score = (
            float(raw_minimum_score)
            if isinstance(raw_minimum_score, (int, float))
            else 0
        )
        if settings.email_ai_quality_gate_enabled and (
            not bundle.quality_review.get("approved")
            or minimum_score < settings.email_ai_min_quality_score
        ):
            log.warning(
                "AI draft failed the professional quality gate; using evidence-based fallback: %s",
                bundle.quality_review,
            )
            return _fallback(business)
        return bundle
    except Exception as exc:
        log.exception("AI drafting failed; using deterministic fallback: %s", exc)
        return _fallback(business)


def ai_backend_report(demo_mode: bool | None = None) -> dict:
    """Authenticated-dashboard/CLI diagnostic; never required by the public health check."""
    settings = get_settings()
    if demo_mode is None:
        demo_mode = settings.demo_mode
    provider = settings.ai_provider.strip().lower()
    if demo_mode:
        return {"provider": provider, "ready": False, "detail": "Demo mode uses deterministic drafts."}
    if provider == "ollama":
        try:
            response = httpx.get(settings.ollama_base_url.rstrip("/") + "/api/tags", timeout=10)
            response.raise_for_status()
            names = [item.get("name", "") for item in response.json().get("models", [])]
            model_present = settings.ollama_model in names or any(name.startswith(settings.ollama_model + ":") for name in names)
            return {
                "provider": "ollama",
                "ready": model_present,
                "model": settings.ollama_model,
                "endpoint": settings.ollama_base_url,
                "installed_models": names,
                "detail": "Model is installed." if model_present else "Ollama is reachable but the configured model is not installed.",
            }
        except Exception as exc:
            return {"provider": "ollama", "ready": False, "model": settings.ollama_model, "detail": str(exc)}
    if provider == "llamacpp":
        try:
            response = httpx.get(settings.llamacpp_base_url.rstrip("/") + "/health", timeout=10)
            response.raise_for_status()
            return {
                "provider": "llamacpp",
                "ready": True,
                "model": settings.llamacpp_model_alias,
                "source": settings.llamacpp_hf_model,
                "endpoint": settings.llamacpp_base_url,
                "gpu_layers": settings.llamacpp_n_gpu_layers,
                "detail": "llama-server is healthy.",
            }
        except Exception as exc:
            return {
                "provider": "llamacpp",
                "ready": False,
                "model": settings.llamacpp_model_alias,
                "detail": str(exc),
            }
    if provider == "openai_compatible":
        return {
            "provider": provider,
            "ready": settings.ai_ready_for(demo_mode),
            "model": settings.openai_model,
            "endpoint": settings.openai_base_url,
            "detail": "Configured; a live paid-provider request was not made by this diagnostic.",
        }
    return {"provider": provider, "ready": False, "detail": "Unsupported AI provider."}
