"""driver delivery list order

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-23 00:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('drivers', sa.Column('list_order_ids', sa.ARRAY(sa.Integer()), nullable=True))


def downgrade() -> None:
    op.drop_column('drivers', 'list_order_ids')
