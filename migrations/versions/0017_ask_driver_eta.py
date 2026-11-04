"""ask the driver for an ETA

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-28 00:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('orders', sa.Column('eta_asked_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('orders', sa.Column('driver_eta_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('orders', sa.Column('driver_eta_answered_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('orders', sa.Column('driver_eta_or_later', sa.Boolean(), server_default='false', nullable=False))


def downgrade() -> None:
    op.drop_column('orders', 'driver_eta_or_later')
    op.drop_column('orders', 'driver_eta_answered_at')
    op.drop_column('orders', 'driver_eta_at')
    op.drop_column('orders', 'eta_asked_at')
