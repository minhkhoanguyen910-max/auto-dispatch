"""change request applied

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-23 00:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('order_change_requests', sa.Column('applied', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('order_change_requests', 'applied')
