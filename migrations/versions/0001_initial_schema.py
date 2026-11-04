"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-10-19 15:41:27.394800
"""
from alembic import op
import sqlalchemy as sa
import geoalchemy2
from geoalchemy2 import Geography

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_geospatial_table('drivers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('phone', sa.String(length=32), nullable=True),
    sa.Column('traccar_unique_id', sa.String(length=64), nullable=False),
    sa.Column('telegram_user_id', sa.BigInteger(), nullable=True),
    sa.Column('link_code', sa.String(length=32), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('last_location', Geography(geometry_type='POINT', srid=4326, dimension=2, spatial_index=False, from_text='ST_GeogFromText', name='geography'), nullable=True),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('link_code'),
    sa.UniqueConstraint('telegram_user_id'),
    sa.UniqueConstraint('traccar_unique_id')
    )
    op.create_geospatial_index('idx_drivers_last_location', 'drivers', ['last_location'], unique=False, postgresql_using='gist', postgresql_ops={})
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('is_admin', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_geospatial_table('orders',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('PENDING', 'ACCEPTED', 'EN_ROUTE', 'AT_STOP', 'COMPLETED', 'CANCELED', name='order_status'), nullable=False),
    sa.Column('pickup_address', sa.Text(), nullable=False),
    sa.Column('pickup_location', Geography(geometry_type='POINT', srid=4326, dimension=2, spatial_index=False, from_text='ST_GeogFromText', name='geography'), nullable=True),
    sa.Column('dropoff_address', sa.Text(), nullable=False),
    sa.Column('dropoff_location', Geography(geometry_type='POINT', srid=4326, dimension=2, spatial_index=False, from_text='ST_GeogFromText', name='geography'), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('driver_id', sa.Integer(), nullable=True),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['driver_id'], ['drivers.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_geospatial_index('idx_orders_dropoff_location', 'orders', ['dropoff_location'], unique=False, postgresql_using='gist', postgresql_ops={})
    op.create_geospatial_index('idx_orders_pickup_location', 'orders', ['pickup_location'], unique=False, postgresql_using='gist', postgresql_ops={})
    op.create_index(op.f('ix_orders_driver_id'), 'orders', ['driver_id'], unique=False)
    op.create_index(op.f('ix_orders_status'), 'orders', ['status'], unique=False)
    op.create_table('order_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=False),
    sa.Column('from_status', sa.Enum('PENDING', 'ACCEPTED', 'EN_ROUTE', 'AT_STOP', 'COMPLETED', 'CANCELED', name='order_status'), nullable=True),
    sa.Column('to_status', sa.Enum('PENDING', 'ACCEPTED', 'EN_ROUTE', 'AT_STOP', 'COMPLETED', 'CANCELED', name='order_status'), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('driver_id', sa.Integer(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['driver_id'], ['drivers.id'], ),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_order_events_order_id'), 'order_events', ['order_id'], unique=False)
    op.create_table('order_offers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=False),
    sa.Column('driver_id', sa.Integer(), nullable=False),
    sa.Column('result', sa.Enum('ACCEPTED', 'DECLINED', 'EXPIRED', name='offer_result'), nullable=True),
    sa.Column('telegram_message_id', sa.BigInteger(), nullable=True),
    sa.Column('sent_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('responded_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['driver_id'], ['drivers.id'], ),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_order_offers_driver_id'), 'order_offers', ['driver_id'], unique=False)
    op.create_index(op.f('ix_order_offers_order_id'), 'order_offers', ['order_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_order_offers_order_id'), table_name='order_offers')
    op.drop_index(op.f('ix_order_offers_driver_id'), table_name='order_offers')
    op.drop_table('order_offers')
    op.drop_index(op.f('ix_order_events_order_id'), table_name='order_events')
    op.drop_table('order_events')
    op.drop_index(op.f('ix_orders_status'), table_name='orders')
    op.drop_index(op.f('ix_orders_driver_id'), table_name='orders')
    op.drop_geospatial_index('idx_orders_pickup_location', table_name='orders', postgresql_using='gist', column_name='pickup_location')
    op.drop_geospatial_index('idx_orders_dropoff_location', table_name='orders', postgresql_using='gist', column_name='dropoff_location')
    op.drop_geospatial_table('orders')
    op.drop_table('users')
    op.drop_geospatial_index('idx_drivers_last_location', table_name='drivers', postgresql_using='gist', column_name='last_location')
    op.drop_geospatial_table('drivers')
    sa.Enum(name='order_status').drop(op.get_bind())
    sa.Enum(name='offer_result').drop(op.get_bind())
