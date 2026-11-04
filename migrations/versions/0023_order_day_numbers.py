"""orders numbered per day: dispatchers and drivers see "#3", the full number is "2026-11-03-003"

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-02 00:00:00
"""
from alembic import op
import sqlalchemy as sa

from app.config import settings


revision = '0023'
down_revision = '0022'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('orders', sa.Column('order_day', sa.Date(), nullable=True))
    op.add_column('orders', sa.Column('day_number', sa.Integer(), nullable=True))
    # Existing orders: their day in APP_TIMEZONE, numbered in the order they were created
    op.execute(sa.text(
        "UPDATE orders SET order_day = n.day, day_number = n.number FROM ("
        " SELECT id, (created_at AT TIME ZONE :tz)::date AS day,"
        " row_number() OVER (PARTITION BY (created_at AT TIME ZONE :tz)::date ORDER BY created_at, id) AS number"
        " FROM orders) n WHERE orders.id = n.id"
    ).bindparams(tz=settings.app_timezone))
    op.alter_column('orders', 'order_day', nullable=False)
    op.alter_column('orders', 'day_number', nullable=False)
    op.create_unique_constraint('uq_orders_day_number', 'orders', ['order_day', 'day_number'])


def downgrade() -> None:
    op.drop_constraint('uq_orders_day_number', 'orders')
    op.drop_column('orders', 'day_number')
    op.drop_column('orders', 'order_day')
