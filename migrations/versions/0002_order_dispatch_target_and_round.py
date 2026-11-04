"""order dispatch target and round

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-20 14:50:52.982301
"""
from alembic import op
import sqlalchemy as sa
import geoalchemy2


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('orders', sa.Column('target_driver_id', sa.Integer(), nullable=True))
    op.add_column('orders', sa.Column('dispatch_started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False))
    op.create_foreign_key('orders_target_driver_id_fkey', 'orders', 'drivers', ['target_driver_id'], ['id'])


def downgrade() -> None:
    op.drop_constraint('orders_target_driver_id_fkey', 'orders', type_='foreignkey')
    op.drop_column('orders', 'dispatch_started_at')
    op.drop_column('orders', 'target_driver_id')
