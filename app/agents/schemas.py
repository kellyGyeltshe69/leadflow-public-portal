from __future__ import annotations

from pydantic import BaseModel, Field


class SubjectCandidate(BaseModel):
    text: str
    score: float = Field(ge=0, le=100)
    rationale: str = ""


class EmailVariantData(BaseModel):
    style: str
    subject: str
    body: str
    quality_score: float = Field(ge=0, le=100)
    predicted_open_rate: float = Field(ge=0, le=1)
    predicted_click_rate: float = Field(ge=0, le=1)
    predicted_reply_rate: float = Field(ge=0, le=1)


class LeadIntelligence(BaseModel):
    summary: str
    problems: list[str]
    personalized_solutions: list[str]
    recommended_hostinger_plan: str
    plan_rationale: str
    reply_probability: float = Field(ge=0, le=1)
    conversion_probability: float = Field(ge=0, le=1)
    best_send_time_local: str
    followup_strategy: dict
    cta: str
    subject_lines: list[SubjectCandidate]
    email_variants: list[EmailVariantData]
    limitations: list[str] = Field(default_factory=list)
