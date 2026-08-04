from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    """Naive UTC for consistent SQLite storage and comparisons."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class SystemState(Base):
    __tablename__ = "system_state"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    mode: Mapped[str] = mapped_column(String(20), default="demo")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="US Small Business Website Opportunities")
    slug: Mapped[Optional[str]] = mapped_column(String(180), nullable=True, unique=True, index=True)
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    template: Mapped[dict] = mapped_column(JSON, default=dict)
    schedule: Mapped[dict] = mapped_column(JSON, default=dict)
    locations: Mapped[list] = mapped_column(JSON, default=list)
    industries: Mapped[list] = mapped_column(JSON, default=list)
    daily_limit: Mapped[int] = mapped_column(Integer, default=10)
    minimum_score: Mapped[float] = mapped_column(Float, default=7.0)
    discovery_hour_utc: Mapped[int] = mapped_column(Integer, default=13)
    last_discovery_date: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    cloned_from_id: Mapped[Optional[int]] = mapped_column(ForeignKey("campaigns.id", ondelete="SET NULL"), nullable=True)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (
        UniqueConstraint("place_id", name="uq_lead_place_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    place_id: Mapped[Optional[str]] = mapped_column(String(250), nullable=True)
    business_name: Mapped[str] = mapped_column(String(300), index=True)
    normalized_name: Mapped[str] = mapped_column(String(300), index=True)
    industry: Mapped[str] = mapped_column(String(200), default="Small business")
    city: Mapped[str] = mapped_column(String(120), default="")
    state: Mapped[str] = mapped_column(String(40), default="")
    formatted_address: Mapped[str] = mapped_column(String(500), default="")
    latitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    longitude: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    timezone_name: Mapped[str] = mapped_column(String(80), default="America/New_York")

    website_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    contact_email: Mapped[Optional[str]] = mapped_column(String(320), nullable=True, index=True)
    contact_page: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    maps_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    source_url: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    discovery_provider: Mapped[str] = mapped_column(String(50), default="unknown")

    status: Mapped[str] = mapped_column(String(50), default="discovered", index=True)
    data_mode: Mapped[str] = mapped_column(String(20), default="demo", index=True)
    conversion_status: Mapped[str] = mapped_column(String(50), default="not_started", index=True)
    conversion_notes: Mapped[str] = mapped_column(Text, default="")
    website_status: Mapped[str] = mapped_column(String(100), default="unknown")
    audit_facts: Mapped[dict] = mapped_column(JSON, default=dict)
    issues: Mapped[list] = mapped_column(JSON, default=list)
    analysis_summary: Mapped[str] = mapped_column(Text, default="")
    opportunity: Mapped[str] = mapped_column(Text, default="")
    checklist: Mapped[list] = mapped_column(JSON, default=list)

    need_score: Mapped[float] = mapped_column(Float, default=1)
    ability_score: Mapped[float] = mapped_column(Float, default=1)
    growth_score: Mapped[float] = mapped_column(Float, default=1)
    response_score: Mapped[float] = mapped_column(Float, default=1)
    weighted_score: Mapped[float] = mapped_column(Float, default=1, index=True)
    score_reasons: Mapped[dict] = mapped_column(JSON, default=dict)

    approved_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    rejected_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_reply_sync_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    gmail_thread_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    root_rfc_message_id: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)

    campaign: Mapped[Campaign] = relationship()
    evidence: Mapped[list[Evidence]] = relationship(back_populates="lead", cascade="all, delete-orphan")
    messages: Mapped[list[Message]] = relationship(back_populates="lead", cascade="all, delete-orphan", order_by="Message.stage")


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    evidence_type: Mapped[str] = mapped_column(String(100))
    observation: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(String(1200))
    confidence: Mapped[str] = mapped_column(String(20), default="medium")
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    lead: Mapped[Lead] = relationship(back_populates="evidence")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (UniqueConstraint("lead_id", "stage", name="uq_lead_message_stage"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    stage: Mapped[int] = mapped_column(Integer)  # 0, 1, 2, 3
    day_offset: Mapped[int] = mapped_column(Integer)
    subject: Mapped[str] = mapped_column(String(500))
    body_core: Mapped[str] = mapped_column(Text)
    body_final: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(50), default="draft", index=True)
    planned_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    gmail_message_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    gmail_thread_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    rfc_message_id: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    lead: Mapped[Lead] = relationship(back_populates="messages")


class DoNotContact(Base):
    __tablename__ = "do_not_contact"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    reason: Mapped[str] = mapped_column(String(100), default="opt_out")
    source: Mapped[str] = mapped_column(String(100), default="reply")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class JobRun(Base):
    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_type: Mapped[str] = mapped_column(String(80), index=True)
    status: Mapped[str] = mapped_column(String(30), default="running")
    summary: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
