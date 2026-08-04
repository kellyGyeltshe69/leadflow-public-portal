from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import httpx

from .config import get_settings

log = logging.getLogger(__name__)


@dataclass
class DraftBundle:
    analysis_summary: str
    opportunity: str
    checklist: list[str]
    messages: list[dict]


def _clean_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _fallback(business: dict) -> DraftBundle:
    name = business["business_name"]
    city_state = ", ".join(x for x in [business.get("city"), business.get("state")] if x)
    issues = business.get("issues") or ["No clear independent website was verified."]
    primary_issue = issues[0]
    opportunity = business.get("opportunity") or "Create a simple first-party page with current business details and a clear inquiry path."
    fact = f"I reviewed the public online path for {name}{' in ' + city_state if city_state else ''}. {primary_issue}"
    checklist = [
        "Confirm the current business name, address, hours and contact details.",
        "Put the most common customer action near the top of the page.",
        "Add a concise service/menu overview and proof such as photos or reviews.",
        "Use a short inquiry form that asks only for details needed to respond.",
        "Test every link on a phone before publishing.",
    ]
    messages = [
        {"stage": 0, "subject": f"One website suggestion for {name}", "body_core": f"Hi {name} team,\n\n{fact}\n\n{opportunity}\n\nIf useful, I can send a free five-point page checklist based on those public details. Would you like it?"},
        {"stage": 1, "subject": f"One website suggestion for {name}", "body_core": f"Hi {name} team,\n\nOne practical follow-up: start with the single customer action that matters most, then add only the information needed to complete it.\n\nWould a short section outline help?"},
        {"stage": 2, "subject": f"One website suggestion for {name}", "body_core": f"Hi {name} team,\n\nA simple first version could cover: current details, core services, proof, and one inquiry action. It does not need to be a large site.\n\nWould you like the copy-ready checklist?"},
        {"stage": 3, "subject": f"One website suggestion for {name}", "body_core": f"Hi {name} team,\n\nI am closing the loop after this note. The suggestion was based only on your public online presence: {primary_issue}\n\nNo reply is needed if this is not a priority, and I will not follow up again."},
    ]
    return DraftBundle(f"Public review found: {primary_issue}", opportunity, checklist, messages)


SYSTEM_PROMPT = """You write ethical, one-to-one B2B outreach for an independent Hostinger affiliate.
You must use only facts supplied in the JSON. Never invent an owner name, performance result, revenue, opening date, missing feature, or technical defect.
The sender does NOT build websites, configure hosting, work for Hostinger, or offer professional audits. He may offer a simple no-cost checklist based on public observations.
Write plain-text, respectful messages. No fake urgency, flattery, fear, guaranteed results, tracking language, or spam phrasing.
Do not include an affiliate link, affiliate disclosure, signature, postal address, or opt-out footer; the application adds those safely.
The first email must provide the verified observation and a practical suggestion before asking permission to send a checklist.
Follow-ups add new specific value. The final follow-up closes the loop and promises no more follow-up.
Return one JSON object only with keys: analysis_summary, opportunity, checklist, messages.
checklist must contain exactly 5 short items.
messages must contain exactly four objects with stages 0,1,2,3 and keys stage, subject, body_core.
Keep stage 0 under 170 words, stages 1 and 2 under 110 words, and stage 3 under 80 words."""


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
        return DraftBundle(
            analysis_summary=str(data.get("analysis_summary", "")).strip(),
            opportunity=str(data.get("opportunity", "")).strip(),
            checklist=[str(x).strip() for x in data["checklist"]],
            messages=messages,
        )
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
