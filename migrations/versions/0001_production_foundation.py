"""Production SaaS foundation schema.

Revision ID: 0001_production_foundation
Revises: None
"""
from alembic import op

from app import models  # noqa: F401
from app.database import saas_models  # noqa: F401
from app.db import Base

revision = "0001_production_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Base.metadata is imported from the same pinned application version as this
    # baseline. Subsequent changes must use explicit Alembic operations.
    Base.metadata.create_all(bind=op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
