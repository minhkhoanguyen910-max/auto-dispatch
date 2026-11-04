"""drop order pickup address

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-22 00:00:00
"""
from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geography


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index('idx_orders_pickup_location', table_name='orders', postgresql_using='gist')
    op.drop_column('orders', 'pickup_location')
    op.drop_column('orders', 'pickup_address')


def downgrade() -> None:
    op.add_column('orders', sa.Column('pickup_address', sa.Text(), server_default='', nullable=False))
    op.add_column('orders', sa.Column('pickup_location', Geography(geometry_type='POINT', srid=4326, spatial_index=False), nullable=True))
    op.create_index('idx_orders_pickup_location', 'orders', ['pickup_location'], postgresql_using='gist')
