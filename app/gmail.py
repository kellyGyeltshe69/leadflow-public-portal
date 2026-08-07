from __future__ import annotations

import base64
import binascii
import logging
import re
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .composio_gateway import ComposioGateway
from .config import get_settings
from .database.saas_models import Reply
from .models import DoNotContact, Lead, Message, utcnow
from .outreach import unsubscribe_url
from .services.compat_sync import sync_business_from_legacy, sync_email_from_legacy
from .utils import normalize_email

log = logging.getLogger(__name__)
OPTOUT_RE = re.compile(r"\b(unsubscribe|remove me|stop emailing|do not contact|no thanks|opt[ -]?out)\b", re.IGNORECASE)


@dataclass(frozen=True)
class GmailSenderIdentity:
    """A sender address that Gmail has proved belongs to the connected account."""

    connected_email: str
    sender_email: str
    sender_type: str
    verification_status: str
    is_default: bool | None = None
    uses_external_smtp: bool = False

    @property
    def own_addresses(self) -> frozenset[str]:
        return frozenset({self.connected_email, self.sender_email})

    def report(self) -> dict[str, str | bool | None]:
        return {
            "ready": True,
            # Do not expose the private forwarding/login address in dashboard
            # screenshots or CLI output. It remains available only in memory
            # for self-sender exclusion during reply classification.
            "sender_email": self.sender_email,
            "sender_type": self.sender_type,
            "verification_status": self.verification_status,
            "is_default": self.is_default,
            "uses_external_smtp": self.uses_external_smtp,
        }


class GmailClient:
    """Gmail API access with OAuth credentials injected by Composio.

    SENDER_EMAIL may be either the connected account's primary address or an
    accepted Gmail ``Send mail as`` alias. A different, unverified address is
    always rejected before a message can be built or sent.
    """

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.gmail_ready:
            raise RuntimeError("Composio and SENDER_EMAIL must be configured for Gmail")
        self.gateway = ComposioGateway()
        # Fail early if the pinned/unique Gmail connection is not ACTIVE.
        self.gateway.resolve_connection_id("gmail")
        self.settings = settings
        self.sender_identity = self._resolve_sender_identity()
        self.authenticated_email = self.sender_identity.connected_email
        self.own_addresses = self.sender_identity.own_addresses

    @staticmethod
    def _unwrap(data: object) -> dict:
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected Gmail response through Composio: {type(data).__name__}")
        response_data = data.get("response_data")
        return response_data if isinstance(response_data, dict) else data

    def profile(self) -> dict:
        return self._unwrap(
            self.gateway.proxy(
                "gmail",
                "https://gmail.googleapis.com/gmail/v1/users/me/profile",
                "GET",
            )
        )

    def send_as_aliases(self) -> list[dict]:
        """Return Gmail's own sender list; never infer aliases from configuration."""
        try:
            response = self._unwrap(
                self.gateway.proxy(
                    "gmail",
                    "https://gmail.googleapis.com/gmail/v1/users/me/settings/sendAs",
                    "GET",
                )
            )
        except Exception as exc:
            raise RuntimeError(
                "Could not verify Gmail send-as aliases through Composio. "
                "Check the Gmail OAuth grant and reconnect it if its scopes changed."
            ) from exc
        aliases = response.get("sendAs")
        if not isinstance(aliases, list):
            raise RuntimeError("Gmail returned an unexpected send-as alias response")
        return [item for item in aliases if isinstance(item, dict)]

    def _resolve_sender_identity(self) -> GmailSenderIdentity:
        configured_email = normalize_email(self.settings.sender_email)
        if not configured_email or "@" not in configured_email:
            raise RuntimeError("SENDER_EMAIL is not a valid email address")

        try:
            profile = self.profile()
        except Exception as exc:
            raise RuntimeError("Could not verify the connected Gmail profile through Composio") from exc
        profile_email = normalize_email(profile.get("emailAddress", ""))
        if not profile_email:
            raise RuntimeError("Could not verify the email address of the connected Gmail account")
        if profile_email == configured_email:
            return GmailSenderIdentity(
                connected_email=profile_email,
                sender_email=configured_email,
                sender_type="primary",
                verification_status="primary",
            )

        matching_alias = next(
            (
                alias
                for alias in self.send_as_aliases()
                if normalize_email(str(alias.get("sendAsEmail", ""))) == configured_email
            ),
            None,
        )
        if matching_alias is None:
            raise RuntimeError(
                "SENDER_EMAIL does not match the connected Gmail account and is not configured "
                "as a Gmail send-as alias. Add and verify it in Gmail, then recheck the connection."
            )

        verification_status = str(matching_alias.get("verificationStatus", "")).strip().lower()
        if verification_status != "accepted":
            status = verification_status or "unspecified"
            raise RuntimeError(
                f"The configured Gmail send-as alias is not ready (verification status: {status}). "
                "Complete Gmail's address verification before sending."
            )
        return GmailSenderIdentity(
            connected_email=profile_email,
            sender_email=configured_email,
            sender_type="verified_send_as_alias",
            verification_status=verification_status,
            is_default=bool(matching_alias.get("isDefault")),
            uses_external_smtp=isinstance(matching_alias.get("smtpMsa"), dict),
        )

    def sender_report(self) -> dict[str, str | bool | None]:
        """Return non-secret sender verification details for the admin/CLI."""
        return self.sender_identity.report()

    def send(self, lead: Lead, message: Message) -> dict:
        sender_email = self.sender_identity.sender_email
        email = EmailMessage()
        email["To"] = lead.contact_email
        email["From"] = formataddr((self.settings.sender_name, sender_email))
        # Replies must return to the public business identity even when the
        # connected Gmail login is a private forwarding destination.
        email["Reply-To"] = sender_email
        email["Subject"] = " ".join(message.subject.splitlines()).strip()
        rfc_message_id = make_msgid(domain=sender_email.split("@")[-1])
        email["Message-ID"] = rfc_message_id
        optout = unsubscribe_url(lead)
        email["List-Unsubscribe"] = f"<{optout}>, <mailto:{sender_email}?subject=unsubscribe>"
        email["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        if lead.root_rfc_message_id and message.stage > 0:
            email["In-Reply-To"] = lead.root_rfc_message_id
            email["References"] = lead.root_rfc_message_id
        email.set_content(message.body_final)
        raw = base64.urlsafe_b64encode(email.as_bytes()).decode()
        body = {"raw": raw}
        if lead.gmail_thread_id and message.stage > 0:
            body["threadId"] = lead.gmail_thread_id
        sent = self._unwrap(
            self.gateway.proxy(
                "gmail",
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                "POST",
                body=body,
            )
        )
        return {"gmail_message_id": sent.get("id"), "thread_id": sent.get("threadId"), "rfc_message_id": rfc_message_id}

    def thread(self, thread_id: str) -> dict:
        return self._unwrap(
            self.gateway.proxy(
                "gmail",
                f"https://gmail.googleapis.com/gmail/v1/users/me/threads/{thread_id}",
                "GET",
                parameters=[{"name": "format", "value": "full", "type": "query"}],
            )
        )


def _headers(message: dict) -> dict[str, str]:
    return {h.get("name", "").lower(): h.get("value", "") for h in (message.get("payload") or {}).get("headers", [])}


def _payload_text(payload: dict) -> str:
    pieces: list[str] = []
    body = (payload.get("body") or {}).get("data")
    if body:
        try:
            pieces.append(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)).decode("utf-8", errors="replace"))
        except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
            log.debug("Could not decode one Gmail MIME body part: %s", exc)
    for part in payload.get("parts") or []:
        mime = part.get("mimeType", "")
        if mime.startswith("text/plain") or part.get("parts"):
            pieces.append(_payload_text(part))
    return "\n".join(pieces)


def cancel_future_messages(session: Session, lead: Lead, reason: str) -> None:
    for item in lead.messages:
        if item.status in {"draft", "scheduled", "retry"}:
            item.status = "cancelled"
            item.error = reason


def add_dnc(session: Session, email: str, reason: str, source: str) -> None:
    email = normalize_email(email)
    if not email:
        return
    existing = session.scalar(select(DoNotContact).where(DoNotContact.email == email))
    if not existing:
        session.add(DoNotContact(email=email, reason=reason, source=source))


def sync_gmail_replies(session: Session, demo_mode: bool | None = None) -> dict[str, int | str]:
    settings = get_settings()
    if demo_mode is None:
        demo_mode = settings.demo_mode
    if not settings.gmail_ready or demo_mode:
        return {"checked": 0, "replies": 0, "optouts": 0, "bounces": 0}
    try:
        client = GmailClient()
    except RuntimeError as exc:
        return {"checked": 0, "replies": 0, "optouts": 0, "bounces": 0, "error": str(exc)}
    leads = session.scalars(
        select(Lead)
        .options(selectinload(Lead.messages))
        .where(
            Lead.gmail_thread_id.is_not(None),
            Lead.data_mode == "live",
            Lead.status.in_(["approved", "sequence_active", "sequence_complete"]),
        )
    ).all()
    stats: dict[str, int | str] = {"checked": 0, "replies": 0, "optouts": 0, "bounces": 0}
    # Exclude both the public send-as identity and the private authenticated
    # Gmail address. Otherwise a message sent manually from the primary Gmail
    # account inside a campaign thread could be misclassified as a lead reply.
    own_addresses = client.own_addresses
    for lead in leads:
        stats["checked"] = int(stats["checked"]) + 1
        if not lead.gmail_thread_id:
            continue
        try:
            thread = client.thread(lead.gmail_thread_id)
        except Exception as exc:
            log.warning("Could not sync Gmail thread %s: %s", lead.gmail_thread_id, exc)
            continue
        inbound = []
        for raw in thread.get("messages", []):
            headers = _headers(raw)
            from_email = normalize_email(headers.get("from", ""))
            if not from_email or from_email in own_addresses:
                continue
            inbound.append((headers, _payload_text(raw.get("payload") or {}), from_email, raw.get("id")))
        if not inbound:
            lead.last_reply_sync_at = utcnow()
            continue
        headers, text, from_email, provider_message_id = inbound[-1]
        subject = headers.get("subject", "")
        if "mailer-daemon" in from_email or "postmaster" in from_email or "delivery status notification" in subject.lower():
            classification = "bounce"
            lead.status = "bounced"
            cancel_future_messages(session, lead, "Cancelled after delivery failure")
            add_dnc(session, lead.contact_email or "", "bounce", "gmail")
            stats["bounces"] = int(stats["bounces"]) + 1
        elif OPTOUT_RE.search(text or ""):
            lead.status = "opted_out"
            cancel_future_messages(session, lead, "Cancelled after opt-out")
            add_dnc(session, lead.contact_email or from_email, "opt_out", "gmail_reply")
            stats["optouts"] = int(stats["optouts"]) + 1
        else:
            classification = "reply"
            lead.status = "replied"
            cancel_future_messages(session, lead, "Cancelled after reply")
            stats["replies"] = int(stats["replies"]) + 1

        business = sync_business_from_legacy(session, lead)
        sent_messages = [item for item in lead.messages if item.status == "sent"]
        if sent_messages:
            legacy_message = max(sent_messages, key=lambda item: item.stage)
            canonical_email = sync_email_from_legacy(session, lead, legacy_message)
            existing_reply = None
            if provider_message_id:
                existing_reply = session.scalar(
                    select(Reply).where(Reply.provider_message_id == str(provider_message_id))
                )
            if not existing_reply:
                sentiment = "positive" if re.search(r"\b(interested|yes|tell me more|call me)\b", text or "", re.IGNORECASE) else None
                session.add(
                    Reply(
                        email_id=canonical_email.id,
                        provider_message_id=str(provider_message_id) if provider_message_id else None,
                        sender_email=from_email,
                        classification=classification,
                        sentiment=sentiment,
                        body_excerpt=(text or "")[:1000],
                    )
                )
        for item in lead.messages:
            sync_email_from_legacy(session, lead, item)
        business.status = lead.status
        lead.last_reply_sync_at = utcnow()
    return stats
