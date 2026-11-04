"""driver heading and speed for the live map

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-28 00:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0018'
down_revision = '0017'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('drivers', sa.Column('last_course', sa.Float(), nullable=True))
    op.add_column('drivers', sa.Column('last_speed_kmh', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('drivers', 'last_speed_kmh')
    op.drop_column('drivers', 'last_course')
