from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..db import Base
from ..models import utcnow


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(30), default="viewer", index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    oauth_provider: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    oauth_subject: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Business(Base):
    """Canonical SaaS business record.

    `legacy_lead_id` keeps the existing review UI/pipeline compatible while new
    APIs and analytics migrate to the canonical table incrementally.
    """

    __tablename__ = "businesses"
    __table_args__ = (
        UniqueConstraint("legacy_lead_id", name="uq_business_legacy_lead"),
        Index("ix_business_campaign_score", "campaign_id", "weighted_score"),
        Index("ix_business_status_mode", "status", "data_mode"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    legacy_lead_id: Mapped[Optional[int]] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"), nullable=True)
    external_place_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(300), index=True)
    industry: Mapped[str] = mapped_column(String(200), default="")
    category: Mapped[str] = mapped_column(String(120), default="")
    city: Mapped[str] = mapped_column(String(120), default="")
    state: Mapped[str] = mapped_column(String(40), default="")
    country: Mapped[str] = mapped_column(String(2), default="US")
    address: Mapped[str] = mapped_column(String(500), default="")
    website_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    contact_email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True, index=True)
    contact_page: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    source_urls: Mapped[list] = mapped_column(JSON, default=list)
    technology_stack: Mapped[dict] = mapped_column(JSON, default=dict)
    audit_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    website_score: Mapped[float] = mapped_column(Float, default=0)
    seo_score: Mapped[float] = mapped_column(Float, default=0)
    mobile_score: Mapped[float] = mapped_column(Float, default=0)
    performance_score: Mapped[float] = mapped_column(Float, default=0)
    weighted_score: Mapped[float] = mapped_column(Float, default=0)
    reply_probability: Mapped[float] = mapped_column(Float, default=0)
    conversion_probability: Mapped[float] = mapped_column(Float, default=0)
    google_rating: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    google_review_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    traffic_estimate: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    domain_authority: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="discovered", index=True)
    data_mode: Mapped[str] = mapped_column(String(20), default="demo", index=True)
    last_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    emails: Mapped[list[EmailRecord]] = relationship(back_populates="business", cascade="all, delete-orphan")
    audit_history: Mapped[list[AuditHistory]] = relationship(back_populates="business", cascade="all, delete-orphan")


class EmailRecord(Base):
    __tablename__ = "emails"
    __table_args__ = (
        UniqueConstraint("legacy_message_id", name="uq_email_legacy_message"),
        Index("ix_email_status_planned", "status", "planned_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    legacy_message_id: Mapped[Optional[int]] = mapped_column(ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)
    stage: Mapped[int] = mapped_column(Integer, default=0)
    style: Mapped[str] = mapped_column(String(50), default="professional")
    subject: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50), default="draft", index=True)
    estimated_open_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    estimated_click_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    estimated_reply_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    quality_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    planned_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    thread_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    business: Mapped[Business] = relationship(back_populates="emails")
    variants: Mapped[list[EmailVariant]] = relationship(back_populates="email", cascade="all, delete-orphan")
    replies: Mapped[list[Reply]] = relationship(back_populates="email", cascade="all, delete-orphan")


class EmailVariant(Base):
    __tablename__ = "email_variants"

    id: Mapped[int] = mapped_column(primary_key=True)
    email_id: Mapped[int] = mapped_column(ForeignKey("emails.id", ondelete="CASCADE"), index=True)
    style: Mapped[str] = mapped_column(String(50), index=True)
    subject: Mapped[str] = mapped_column(String(500))
    body: Mapped[str] = mapped_column(Text)
    quality_score: Mapped[float] = mapped_column(Float, default=0)
    predicted_open_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    predicted_click_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    predicted_reply_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    email: Mapped[EmailRecord] = relationship(back_populates="variants")


class Reply(Base):
    __tablename__ = "replies"

    id: Mapped[int] = mapped_column(primary_key=True)
    email_id: Mapped[int] = mapped_column(ForeignKey("emails.id", ondelete="CASCADE"), index=True)
    provider_message_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True)
    sender_email: Mapped[str] = mapped_column(String(320))
    classification: Mapped[str] = mapped_column(String(50), default="reply", index=True)
    sentiment: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    body_excerpt: Mapped[str] = mapped_column(Text, default="")
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    email: Mapped[EmailRecord] = relationship(back_populates="replies")


class FollowUp(Base):
    __tablename__ = "followups"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    email_id: Mapped[Optional[int]] = mapped_column(ForeignKey("emails.id", ondelete="SET NULL"), nullable=True)
    sequence_number: Mapped[int] = mapped_column(Integer)
    recommended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    tone: Mapped[str] = mapped_column(String(50), default="helpful")
    urgency: Mapped[str] = mapped_column(String(30), default="low")
    rationale: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(50), default="planned", index=True)
    stopped_reason: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class LandingPage(Base):
    __tablename__ = "landing_pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), unique=True)
    slug: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    headline: Mapped[str] = mapped_column(String(500))
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    recommended_plan: Mapped[str] = mapped_column(String(80), default="Hostinger Business")
    published: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AffiliateClick(Base):
    __tablename__ = "affiliate_clicks"
    __table_args__ = (Index("ix_affiliate_click_campaign_time", "campaign_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    landing_page_id: Mapped[Optional[int]] = mapped_column(ForeignKey("landing_pages.id", ondelete="SET NULL"), nullable=True)
    ip_hash: Mapped[str] = mapped_column(String(64), default="")
    user_agent_hash: Mapped[str] = mapped_column(String(64), default="")
    referrer_domain: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class AffiliateConversion(Base):
    __tablename__ = "affiliate_conversions"

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[Optional[int]] = mapped_column(ForeignKey("businesses.id", ondelete="SET NULL"), nullable=True)
    campaign_id: Mapped[Optional[int]] = mapped_column(ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True)
    external_reference: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True)
    status: Mapped[str] = mapped_column(String(40), default="reported")
    revenue: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    commission: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    converted_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AnalyticsEvent(Base):
    __tablename__ = "analytics"
    __table_args__ = (Index("ix_analytics_campaign_event_time", "campaign_id", "event_type", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[Optional[int]] = mapped_column(ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True)
    business_id: Mapped[Optional[int]] = mapped_column(ForeignKey("businesses.id", ondelete="SET NULL"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    value: Mapped[float] = mapped_column(Float, default=1)
    properties: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class TaskRecord(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    queue_job_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True)
    task_type: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(Text, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    queued_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class LogRecord(Base):
    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    level: Mapped[str] = mapped_column(String(20), index=True)
    logger: Mapped[str] = mapped_column(String(160), default="leadflow")
    message: Mapped[str] = mapped_column(Text)
    correlation_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    context: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)


class AuditHistory(Base):
    __tablename__ = "audit_history"
    __table_args__ = (Index("ix_audit_business_created", "business_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    business_id: Mapped[int] = mapped_column(ForeignKey("businesses.id", ondelete="CASCADE"), index=True)
    audit_type: Mapped[str] = mapped_column(String(80), default="website")
    score: Mapped[float] = mapped_column(Float, default=0)
    facts: Mapped[dict] = mapped_column(JSON, default=dict)
    issues: Mapped[list] = mapped_column(JSON, default=list)
    screenshot_path: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    business: Mapped[Business] = relationship(back_populates="audit_history")
