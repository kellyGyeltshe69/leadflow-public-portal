from __future__ import annotations

from .config import get_settings
from .models import Lead, Message
from .security import landing_token, unsubscribe_token

AFFILIATE_STAGES = {0, 2}


def unsubscribe_url(lead: Lead) -> str:
    settings = get_settings()
    return f"{settings.report_base_url}/u/{unsubscribe_token(lead.id)}"


def landing_url(lead: Lead) -> str:
    settings = get_settings()
    return f"{settings.report_base_url}/r/{landing_token(lead.id)}"


def compose_final_body(lead: Lead, message: Message, allow_postal_placeholder: bool = False) -> str:
    settings = get_settings()
    postal = settings.physical_postal_address.strip() if settings.postal_ready else ""
    if not postal and allow_postal_placeholder:
        postal = "[VALID POSTAL ADDRESS AND ATTESTATION REQUIRED BEFORE SENDING]"
    affiliate_section = ""
    if message.stage in AFFILIATE_STAGES:
        affiliate_section = (
            "\n\nIf you are already comparing budget-oriented do-it-yourself website setup and hosting options, "
            f"I prepared a short audit page with the verified issues and hosting recommendation: {landing_url(lead)}\n"
            "It links to the current Hostinger offer so you can compare the full term, included features, and renewal price.\n\n"
            "Disclosure: I am an independent Hostinger affiliate, not a Hostinger employee, and I may earn a commission "
            "if you purchase through that link."
        )
    footer = (
        f"\n\n{settings.sender_name}\n{settings.sender_role}\nReply to this email\n\n"
        "This is a one-to-one business inquiry based on publicly listed business contact information. "
        "If it is not relevant, reply 'no thanks' or use the opt-out link and I will not contact you again.\n"
        f"Opt out: {unsubscribe_url(lead)}\n{postal}"
    )
    return message.body_core.strip() + affiliate_section + footer


def validate_send_body(lead: Lead, message: Message) -> list[str]:
    settings = get_settings()
    body = message.body_final or ""
    errors = []
    if not settings.postal_ready:
        errors.append("A valid physical postal address is required.")
    if "[VALID POSTAL ADDRESS" in body or "REQUIRED BEFORE SENDING" in body:
        errors.append("The message still contains a postal-address placeholder.")
    if settings.sender_role not in body:
        errors.append("The transparent independent-affiliate role is missing.")
    if unsubscribe_url(lead) not in body:
        errors.append("The opt-out URL is missing.")
    if message.stage in AFFILIATE_STAGES:
        report_url = landing_url(lead)
        if report_url not in body:
            errors.append("The approved audit landing-page link is missing.")
        if settings.affiliate_url in body:
            errors.append("The email must not contain the direct affiliate URL; it belongs on the disclosed audit page.")
        if "may earn a commission" not in body or "not a Hostinger employee" not in body:
            errors.append("The affiliate disclosure is incomplete.")
        if body.find(report_url) < max(1, len(message.body_core) // 2):
            errors.append("The landing-page link appeared before sufficient lead-specific value.")
    elif settings.affiliate_url in body or landing_url(lead) in body:
        errors.append("This follow-up stage should not repeat the promotional link.")
    if not lead.contact_email:
        errors.append("No public business email is available.")
    return errors
