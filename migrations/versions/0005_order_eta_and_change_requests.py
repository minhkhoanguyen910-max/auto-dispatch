"""order eta and change requests

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-22 00:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('orders', sa.Column('eta_reported_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('orders', sa.Column('eta_at', sa.DateTime(timezone=True), nullable=True))
    op.create_table('order_change_requests',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('order_id', sa.Integer(), nullable=False),
    sa.Column('driver_id', sa.Integer(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_by_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['driver_id'], ['drivers.id'], ),
    sa.ForeignKeyConstraint(['order_id'], ['orders.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['resolved_by_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_order_change_requests_order_id'), 'order_change_requests', ['order_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_order_change_requests_order_id'), table_name='order_change_requests')
    op.drop_table('order_change_requests')
    op.drop_column('orders', 'eta_at')
    op.drop_column('orders', 'eta_reported_at')
