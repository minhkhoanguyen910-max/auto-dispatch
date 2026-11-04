"""orders keep only their own drop-off address

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-23 00:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0012'
down_revision = '0011'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint('orders_customer_address_id_fkey', 'orders', type_='foreignkey')
    op.drop_column('orders', 'customer_address_id')


def downgrade() -> None:
    op.add_column('orders', sa.Column('customer_address_id', sa.Integer(), nullable=True))
    op.create_foreign_key('orders_customer_address_id_fkey', 'orders', 'customer_addresses', ['customer_address_id'], ['id'], ondelete='SET NULL')
