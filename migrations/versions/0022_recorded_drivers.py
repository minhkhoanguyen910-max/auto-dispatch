"""drivers recorded by the dispatcher (no Telegram), for testing with real days

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-30 00:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0022'
down_revision = '0021'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('drivers', sa.Column('recorded_by_dispatcher', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('drivers', 'recorded_by_dispatcher')
