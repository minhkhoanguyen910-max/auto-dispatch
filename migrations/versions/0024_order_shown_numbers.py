"""the number dispatchers and drivers see counts only the day's orders that aren't canceled

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-02 00:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0024'
down_revision = '0023'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('orders', sa.Column('shown_number', sa.Integer(), nullable=True))
    # Orders that aren't canceled: numbered from 1 each day without gaps; canceled ones keep their day number
    op.execute(
        "UPDATE orders SET shown_number = n.number FROM ("
        " SELECT id, row_number() OVER (PARTITION BY order_day ORDER BY day_number) AS number"
        " FROM orders WHERE status <> 'CANCELED') n WHERE orders.id = n.id"
    )
    op.execute("UPDATE orders SET shown_number = day_number WHERE shown_number IS NULL")
    op.alter_column('orders', 'shown_number', nullable=False)


def downgrade() -> None:
    op.drop_column('orders', 'shown_number')
