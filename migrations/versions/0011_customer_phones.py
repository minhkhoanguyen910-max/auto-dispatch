"""several phone numbers per customer

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-23 00:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('customer_phones',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('customer_id', sa.Integer(), nullable=False),
    sa.Column('label', sa.String(length=50), nullable=True),
    sa.Column('phone', sa.String(length=32), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['customer_id'], ['customers.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('phone')
    )
    op.create_index(op.f('ix_customer_phones_customer_id'), 'customer_phones', ['customer_id'], unique=False)
    op.execute("INSERT INTO customer_phones (customer_id, phone, position) SELECT id, phone, 0 FROM customers WHERE phone IS NOT NULL")
    op.drop_column('customers', 'phone')


def downgrade() -> None:
    op.add_column('customers', sa.Column('phone', sa.String(length=32), nullable=True))
    op.create_unique_constraint('customers_phone_key', 'customers', ['phone'])
    # Only the main number fits back
    op.execute("UPDATE customers c SET phone = p.phone FROM customer_phones p WHERE p.customer_id = c.id AND p.position = 0")
    op.drop_index(op.f('ix_customer_phones_customer_id'), table_name='customer_phones')
    op.drop_table('customer_phones')
