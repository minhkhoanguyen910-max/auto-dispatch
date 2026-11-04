"""drivers on duty / off duty: Automatic gives orders only to drivers on duty (Plan 5)

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-02 00:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0025'
down_revision = '0024'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Everyone starts off duty, so nobody gets orders by surprise
    op.add_column('drivers', sa.Column('on_duty', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('drivers', 'on_duty')
