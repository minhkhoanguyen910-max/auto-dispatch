"""driver's road path between GPS fixes for the live map

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-28 00:00:00
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('drivers', sa.Column('last_path', postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column('drivers', 'last_path')
