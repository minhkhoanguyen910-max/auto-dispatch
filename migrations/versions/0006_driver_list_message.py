"""driver delivery list message

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-22 00:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('drivers', sa.Column('list_message_id', sa.BigInteger(), nullable=True))


def downgrade() -> None:
    op.drop_column('drivers', 'list_message_id')
