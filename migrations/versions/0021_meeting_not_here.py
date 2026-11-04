"""meeting points: when the bot asked the driver "Customer not here?"

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-30 00:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0021'
down_revision = '0020'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('meeting_requests', sa.Column('not_here_asked_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('meeting_requests', 'not_here_asked_at')
