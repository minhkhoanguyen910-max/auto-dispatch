"""remembered driving times and daily Route Matrix usage

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-23 00:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0013'
down_revision = '0012'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('travel_times',
    sa.Column('origin', sa.String(length=32), nullable=False),
    sa.Column('destination', sa.String(length=32), nullable=False),
    sa.Column('traffic', sa.Boolean(), nullable=False),
    sa.Column('seconds', sa.Integer(), nullable=False),
    sa.Column('fetched_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('origin', 'destination', 'traffic')
    )
    op.create_table('routes_usage',
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('pairs', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('day')
    )


def downgrade() -> None:
    op.drop_table('routes_usage')
    op.drop_table('travel_times')
