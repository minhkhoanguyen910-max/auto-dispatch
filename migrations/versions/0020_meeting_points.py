"""meeting points: known spots, drivers' requests, customers who want the door

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-29 00:00:00
"""
from alembic import op
import sqlalchemy as sa
from geoalchemy2 import Geography


revision = '0020'
down_revision = '0019'
branch_labels = None
depends_on = None

meeting_status = sa.Enum(
    'REQUESTED', 'ASKED', 'AGREED', 'DECLINED', 'EXPIRED', 'NOT_THERE', 'DELIVERED', name='meeting_status'
)


def upgrade() -> None:
    op.create_table(
        'meeting_spots',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('location', Geography(geometry_type='POINT', srid=4326, dimension=2, spatial_index=False, from_text='ST_GeogFromText', name='geography'), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=True),
        sa.Column('times_chosen', sa.Integer(), server_default='0', nullable=False),
        sa.Column('times_worked', sa.Integer(), server_default='0', nullable=False),
        sa.Column('times_not_there', sa.Integer(), server_default='0', nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_geospatial_index('idx_meeting_spots_location', 'meeting_spots', ['location'], unique=False, postgresql_using='gist', postgresql_ops={})
    op.create_table(
        'meeting_requests',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('order_id', sa.Integer(), nullable=False),
        sa.Column('driver_id', sa.Integer(), nullable=False),
        sa.Column('spot_id', sa.Integer(), nullable=False),
        sa.Column('status', meeting_status, nullable=False),
        sa.Column('parked_here', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('walk_meters', sa.Integer(), nullable=True),
        sa.Column('walk_seconds', sa.Integer(), nullable=True),
        sa.Column('saving_min_seconds', sa.Integer(), nullable=True),
        sa.Column('saving_max_seconds', sa.Integer(), nullable=True),
        sa.Column('send_by', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('asked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resolved_by_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['driver_id'], ['drivers.id']),
        sa.ForeignKeyConstraint(['spot_id'], ['meeting_spots.id']),
        sa.ForeignKeyConstraint(['resolved_by_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('order_id'),
    )
    op.create_index('ix_meeting_requests_status', 'meeting_requests', ['status'])
    op.add_column('customers', sa.Column('always_to_door', sa.Boolean(), server_default='false', nullable=False))
    op.add_column('customers', sa.Column('meeting_declines', sa.Integer(), server_default='0', nullable=False))


def downgrade() -> None:
    op.drop_column('customers', 'meeting_declines')
    op.drop_column('customers', 'always_to_door')
    op.drop_index('ix_meeting_requests_status', table_name='meeting_requests')
    op.drop_table('meeting_requests')
    op.drop_geospatial_index('idx_meeting_spots_location', table_name='meeting_spots', postgresql_using='gist', column_name='location')
    op.drop_table('meeting_spots')
    meeting_status.drop(op.get_bind())
