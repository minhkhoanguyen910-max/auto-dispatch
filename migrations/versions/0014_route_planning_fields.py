"""route planning fields: urgent, deliver by, planned stop and arrival

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-23 00:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0014'
down_revision = '0013'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('orders', sa.Column('is_urgent', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('orders', sa.Column('deliver_by', sa.DateTime(timezone=True), nullable=True))
    op.add_column('orders', sa.Column('route_position', sa.Integer(), nullable=True))
    op.add_column('orders', sa.Column('planned_arrival_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('orders', sa.Column('planned_arrival_latest', sa.DateTime(timezone=True), nullable=True))
    op.add_column('orders', sa.Column('eta_estimated', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('drivers', sa.Column('route_planned_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('drivers', 'route_planned_at')
    op.drop_column('orders', 'eta_estimated')
    op.drop_column('orders', 'planned_arrival_latest')
    op.drop_column('orders', 'planned_arrival_at')
    op.drop_column('orders', 'route_position')
    op.drop_column('orders', 'deliver_by')
    op.drop_column('orders', 'is_urgent')
