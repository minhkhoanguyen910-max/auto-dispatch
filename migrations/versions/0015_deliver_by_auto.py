"""mark automatic deliver-by limits

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-24 00:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('orders', sa.Column('deliver_by_auto', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('orders', 'deliver_by_auto')
